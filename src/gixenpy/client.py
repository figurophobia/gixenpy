"""
Gixen client (eBay sniping) WITHOUT an official API.

Gixen withdrew its public API for regular users, so this module automates
its web form with `requests` (just like a browser would, but without one):

  1. Log in: POST to home_1.php with username/password (Gixen's real login form).
  2. Locate the "add snipe" form on the already-logged-in page and REUSE its
     fields as-is (including hidden fields/tokens), filling in only the item
     number and the max bid. This way we don't depend on fixed field names:
     if Gixen renames them, we keep reading whatever form is there.
  3. Submit the snipe and check the response.

The generic <form> parsing engine (nothing Gixen-specific) lives in
`forms.py`; this module focuses on Gixen-specific logic: which form is
which, session/login, and the operations (add/list/update/delete).

Principles:
  - One request per user action (no aggressive polling).
  - Credentials are passed in by whoever instantiates the client; this
    module NEVER logs or prints them.
  - Dry-run mode: logs in and shows WHAT it would send, without submitting
    anything. Useful for verifying the field mapping before bidding for real.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit

import requests

from .forms import _Form, _parse_forms

BASE = "https://www.gixen.com/main/"
LOGIN_URL = BASE + "home_1.php"   # login form's action
# The logged-in panel (snipe list + add-snipe form) lives at home_2.php.
# home_1.php is just a bridge page that redirects there after login.
HOME_URL = BASE + "home_2.php"

# Heuristics for recognizing the snipe form's fields.
#   - add:  newitemid / newmaxbid / newsnipegroup / newbidoffset / username
#   - edit: edititemid / editmaxbid / editbidoffset ...  (we do NOT want that one)
_ITEM_RE = re.compile(r"item", re.IGNORECASE)
_BID_RE = re.compile(r"bid|max", re.IGNORECASE)
_QTY_RE = re.compile(r"quantity|qty", re.IGNORECASE)
# Signals that we are NOT logged in / of an error.
_LOGIN_FAIL_RE = re.compile(r"could not log in|incorrect|invalid login", re.IGNORECASE)
_ERROR_RE = re.compile(r"\berror\b|could not|cannot|invalid|not added|has ended|already",
                       re.IGNORECASE)


class GixenError(RuntimeError):
    """Readable error while talking to Gixen (login, network, unexpected format…)."""


def _trusted_action_url(base_url: str, action: str) -> str:
    """
    Resolves a <form>'s 'action' (relative or absolute) against `base_url`
    and checks that the result is still https://gixen.com (or a subdomain).

    Forms are located by parsing the HTML Gixen returns; if that response
    were tampered with (MITM, redirect, XSS on their site…), an 'action'
    with a foreign absolute URL could make us send the payload -including
    hidden fields and session tokens- to a different host. This doesn't
    replace HTTPS/TLS, but it stops us from blindly following any URL
    embedded in the HTML.
    """
    url = urljoin(base_url, action) if action else HOME_URL
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "gixen.com" or host.endswith(".gixen.com")):
        raise GixenError(f"Form URL outside gixen.com, rejecting it: {url!r}")
    return url


def legacy_item_number(item_id: str) -> str:
    """
    eBay Browse returns ids like 'v1|123456789012|0'. Gixen wants the
    classic item number ('123456789012'). Extracts the long numeric part.
    """
    if not item_id:
        return ""
    if "|" in item_id:
        parts = item_id.split("|")
        if len(parts) >= 2 and parts[1].isdigit():
            return parts[1]
    # If it already comes clean (or is a URL), grab the longest number.
    nums = re.findall(r"\d{9,}", item_id)
    return nums[0] if nums else item_id


@dataclass
class SnipeForm:
    """The add-snipe form, already located within the logged-in page."""

    action: str
    method: str
    fields: dict[str, str]
    item_field: str
    bid_field: str
    qty_field: str | None = None
    offset_field: str | None = None
    offset_mirror_field: str | None = None
    group_field: str | None = None


def _pick_item_field(fields: dict[str, str]) -> str | None:
    """Item-number field. Prefers the ADD one (newitemid) over the edit one."""
    cands = [n for n in fields if _ITEM_RE.search(n)]
    if not cands:
        return None
    cands.sort(key=lambda n: (0 if "new" in n.lower() else (2 if "edit" in n.lower() else 1)))
    return cands[0]


def _pick_bid_field(fields: dict[str, str]) -> str | None:
    """Max-bid field. Excludes 'bidoffset'/'group' (those aren't the bid)."""
    cands = [
        n for n in fields
        if _BID_RE.search(n) and "offset" not in n.lower() and "group" not in n.lower()
    ]
    if not cands:
        return None
    # Prefer the one containing 'max' (maxbid) and the add one (new…).
    cands.sort(key=lambda n: (0 if "max" in n.lower() else 1,
                              0 if "new" in n.lower() else 1))
    return cands[0]


def _pick_offset_field(fields: dict[str, str], *, mirror: bool) -> str | None:
    """
    Offset field (seconds before the close). Gixen has two: the main one
    ('newbidoffset') and the mirror one ('newbidoffsetmirror'). `mirror`
    decides which of the two is looked up.
    """
    cands = [n for n in fields if "offset" in n.lower() and ("mirror" in n.lower()) == mirror]
    if not cands:
        return None
    cands.sort(key=lambda n: 0 if "new" in n.lower() else 1)
    return cands[0]


def _pick_group_field(fields: dict[str, str]) -> str | None:
    """Bid-group field ('newsnipegroup')."""
    cands = [n for n in fields if "group" in n.lower()]
    if not cands:
        return None
    cands.sort(key=lambda n: 0 if "new" in n.lower() else 1)
    return cands[0]


def _find_snipe_form(html: str, base_url: str = BASE) -> SnipeForm | None:
    """
    Locates the snipe ADD form (the one with item number + max bid). Gixen
    has several forms living together on the page (edit, delete, import…);
    we score them to pick the add one ('new…' fields, empty item) and NOT
    an edit one ('edit…', already filled in).
    """
    best: SnipeForm | None = None
    best_score = -10
    for form in _parse_forms(html):
        item_field = _pick_item_field(form.fields)
        bid_field = _pick_bid_field(form.fields)
        if not item_field or not bid_field:
            continue
        score = 0
        low = item_field.lower()
        if "new" in low:
            score += 3
        if "edit" in low:
            score -= 3
        if not (form.fields.get(item_field) or "").strip():
            score += 1  # the add form comes empty; edit forms come filled in
        if score > best_score:
            best_score = score
            qty_field = next((n for n in form.fields if _QTY_RE.search(n)), None)
            action = _trusted_action_url(base_url, form.action)
            best = SnipeForm(
                action=action,
                method=form.method or "post",
                fields=dict(form.fields),
                item_field=item_field,
                bid_field=bid_field,
                qty_field=qty_field,
                offset_field=_pick_offset_field(form.fields, mirror=False),
                offset_mirror_field=_pick_offset_field(form.fields, mirror=True),
                group_field=_pick_group_field(form.fields),
            )
    return best


def _snipe_row_present(html: str, item_number: str) -> bool:
    """
    Signal that the item already appears in the active-snipes list: its
    edit controls (`edit_<id>` / `edititemid_<id>`) are in the HTML.
    """
    return f"edit_{item_number}" in html or f"edititemid_{item_number}" in html


def _bid_matches(actual: str, expected: str) -> bool:
    """
    Compares a bid read from Gixen with the expected one, tolerating format
    decorations (e.g. "80.00 USD", spaces) instead of comparing the raw
    text. Without this, a change that DID apply could be reported as "not
    confirmed" just because Gixen decorates the number differently.
    """
    try:
        cleaned = re.sub(r"[^0-9.\-]", "", actual or "")
        if cleaned in ("", ".", "-"):
            return False
        return Decimal(cleaned) == Decimal(expected)
    except (InvalidOperation, ValueError, TypeError):
        return False


def _explicit_error_line(html: str) -> str | None:
    """Looks for a short, explicit error line in a Gixen response."""
    for line in re.split(r"<[^>]+>|\n", html):
        s = line.strip()
        if s and _ERROR_RE.search(s) and len(s) < 200:
            return s
    return None


def _validate_offset(offset: int | str | None) -> str | None:
    """Validates the offset (seconds before close); Gixen requires a positive integer."""
    if offset is None:
        return None
    try:
        value = int(offset)
    except (TypeError, ValueError):
        raise GixenError(f"Invalid offset: {offset!r}")
    if value <= 0:
        raise GixenError("Offset must be greater than 0.")
    return str(value)


def _validate_group(group: int | str | None) -> str | None:
    """Validates the bid group; Gixen requires an integer >= 0 (0 = no group)."""
    if group is None:
        return None
    try:
        value = int(group)
    except (TypeError, ValueError):
        raise GixenError(f"Invalid group: {group!r}")
    if value < 0:
        raise GixenError("Group cannot be negative.")
    return str(value)


def _find_dbidid_form(forms: list[_Form], item_number: str) -> _Form | None:
    """
    The row's internal id in Gixen (`dbidid`, needed to delete or modify a
    snipe) lives in a separate `<form>`, right after that item's "modify"
    form (they share a row in the list table).
    """
    for i, f in enumerate(forms):
        if f.fields.get("edititemid") == item_number:
            for g in forms[i + 1:]:
                if "dbidid" in g.fields and "edititemid" not in g.fields:
                    return g
            break
    return None


_EBAY_ITEM_URL = "https://www.ebay.com/itm/{}"

# Gixen shows "Status (main): <STATUS>" right before each snipe's row form.
# "SCHEDULED" is the only "active" (pending) status. Terminal states are
# distinguished so a caller can tell a win from a loss: "WON" is a clean win;
# "BID UNDER ASKING PRICE" (confirmed against a real account), "LOST",
# "FAILED" and "OUTBID" all mean the snipe did not win. Anything else
# non-empty is an unrecognized terminal state ("ended") rather than being
# forced into won/lost — fragile guessing is worse than an honest "ended"
# if Gixen ever renders new wording.
_STATUS_RE = re.compile(r"Status \(main\):\s*</td>\s*<td>([^<]*)</td>", re.IGNORECASE)
_EDITITEMID_RE = re.compile(r'name="edititemid"[^>]*value="(\d+)"')

# Gixen prints "Current bid: X.XX USD" right next to "Max bid: ..." for every
# snipe, active or ended. For an ended one (won or lost) this is the
# auction's actual final price -- useful since eBay itself no longer serves
# that listing once it's gone from search results, so this is the only
# place left to read what it finally sold for.
_CURRENT_BID_RE = re.compile(r"Current bid:\s*([\d.]+\s*\w+)</td>", re.IGNORECASE)


def _pair_by_proximity(html: str, marker_re: re.Pattern[str]) -> dict[str, str]:
    """
    Pairs each item with the nearest preceding match of `marker_re` in the
    HTML (the order on the page is always these per-item markers → that
    snipe's form). With no JS or intermediate table explicitly linking them,
    page order/proximity is the only signal available.
    """
    marks = [(m.start(), m.group(1).strip()) for m in marker_re.finditer(html)]
    result: dict[str, str] = {}
    mi = 0
    current: str | None = None
    for m in _EDITITEMID_RE.finditer(html):
        pos = m.start()
        while mi < len(marks) and marks[mi][0] < pos:
            current = marks[mi][1]
            mi += 1
        item = m.group(1)
        if current is not None and item not in result:
            result[item] = current
    return result


def _snipe_statuses(html: str) -> dict[str, str]:
    """Pairs each item with its "Status (main): ..." text (see `_pair_by_proximity`)."""
    return _pair_by_proximity(html, _STATUS_RE)


def _snipe_current_bids(html: str) -> dict[str, str]:
    """Pairs each item with its "Current bid: ..." text (see `_pair_by_proximity`)."""
    return _pair_by_proximity(html, _CURRENT_BID_RE)


def _normalize_status(raw: str | None) -> str:
    if not raw:
        return "unknown"
    upper = raw.strip().upper()
    if upper == "SCHEDULED":
        return "active"
    if "WON" in upper:
        return "won"
    if "LOST" in upper or "FAILED" in upper or "UNDER" in upper or "OUTBID" in upper:
        return "lost"
    return "ended"


def _parse_snipes(html: str) -> list[Snipe]:
    """
    Extracts the snipes from the logged-in page (home_2.php), active and
    ended alike (Gixen keeps them until they're purged).

    Each snipe has its own 'modify' form with unsuffixed fields
    (edititemid/editmaxbid/...), pre-filled with its current values. We
    rely on those forms (one per snipe).
    """
    # Gixen's per-item thumbnail: <img src="...thumbnails/x.jpg"> … item=<id>
    thumbs = {}
    for thumb, iid in re.findall(
        r'(https?://[^"\']*thumbnails/[^"\']+\.jpg)"[^>]*>\s*<a[^>]*?item=(\d+)', html
    ):
        thumbs.setdefault(iid, thumb)

    statuses = _snipe_statuses(html)
    current_bids = _snipe_current_bids(html)

    snipes: list[Snipe] = []
    seen: set[str] = set()
    for form in _parse_forms(html):
        item = form.fields.get("edititemid")
        if item and item not in seen:
            seen.add(item)
            snipes.append(Snipe(
                item_id=item,
                max_bid=form.fields.get("editmaxbid", ""),
                offset=form.fields.get("editbidoffset", ""),
                group=form.fields.get("editsnipegroup", ""),
                comment=form.fields.get("editcomment", ""),
                ebay_url=_EBAY_ITEM_URL.format(item),
                thumb=thumbs.get(item, ""),
                status=_normalize_status(statuses.get(item)),
                current_bid=current_bids.get(item, ""),
            ))
    return snipes


# --------------------------------------------------------------------------- #
# Result of an operation
# --------------------------------------------------------------------------- #
@dataclass
class SnipeResult:
    ok: bool
    message: str
    dry_run: bool = False
    item_number: str = ""
    payload: dict[str, str] = field(default_factory=dict)
    action: str = ""


@dataclass
class Snipe:
    """A snipe on the Gixen account (read from the logged-in list)."""

    item_id: str        # eBay item number
    max_bid: str         # scheduled max bid
    offset: str = ""     # seconds before the close (offset)
    group: str = ""      # bid group
    comment: str = ""    # optional comment
    ebay_url: str = ""
    thumb: str = ""       # Gixen thumbnail (fallback if not in the report)
    status: str = "unknown"  # "active" | "won" | "lost" | "ended" | "unknown"
    current_bid: str = ""  # e.g. "62.00 USD" -- the auction's final price once ended


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class GixenClient:
    def __init__(
        self,
        username: str = "",
        password: str = "",
        dry_run: bool = True,
        timeout: float | tuple[float, float] = (5, 25),
        retry_backoff: float = 2.0,
    ):
        self.username = username
        self.password = password
        self.dry_run = dry_run
        # (connect, read): fail fast if Gixen doesn't respond to the
        # connection, without cutting short the time given to generate the
        # response.
        self.timeout = timeout
        # Wait before retrying a write that Gixen accepted (HTTP 200) but
        # didn't apply -- a one-off silent glitch, not a rejection.
        self.retry_backoff = retry_backoff
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "Mozilla/5.0 (gixenpy)"})
        self._logged_in = False

    @property
    def ready(self) -> bool:
        """True if there are credentials to be able to log in."""
        return bool(self.username and self.password)

    # ---- Network (with one retry against one-off Gixen hiccups) ------------
    def _request(self, method: str, url: str, **kw):
        last: Exception | None = None
        for attempt in range(2):
            try:
                return self._session.request(method, url, timeout=self.timeout, **kw)
            except requests.RequestException as e:
                last = e
                if attempt == 0:
                    time.sleep(1)
        raise GixenError(f"Network error talking to Gixen: {last}")

    def _get(self, url: str, **kw):
        return self._request("GET", url, **kw)

    def _post(self, url: str, **kw):
        return self._request("POST", url, **kw)

    # ---- Login ---------------------------------------------------------------
    def _looks_logged_in(self, html: str) -> bool:
        low = html.lower()
        if _LOGIN_FAIL_RE.search(low):
            return False
        # Logged in if we can already see the snipe form or a logout link.
        if "logout" in low or "log out" in low:
            return True
        return _find_snipe_form(html) is not None

    def _authed_home(self) -> str:
        """
        Returns the logged-in panel's HTML, REUSING the session if it's
        still alive. Only performs a fresh login (which invalidates other
        sessions on Gixen) when the current session has expired. That way,
        if the app is the only thing using the account, it won't keep
        kicking you out of the browser on every action.
        """
        if self._logged_in:
            html = self._home_html()
            if self._looks_logged_in(html):
                return html
            self._logged_in = False  # session expired (e.g. you logged in via the browser)
        # login() already fetches the panel HTML to confirm the login worked
        # (the "bridge page" home_2.php request); reuse it here instead of
        # fetching that same URL a second time right after (measured against
        # the real account: a cold login is 4 requests without this reuse,
        # ~9s; 3 with it, one whole GET home_2.php round trip saved).
        html = self.login()
        if not html:
            raise GixenError(
                "Gixen didn't return the panel after logging in (possible "
                "one-off server outage); try again."
            )
        return html

    def login(self) -> str:
        """
        Logs into Gixen (POST credentials → new session).

        Gixen only allows ONE session per account, so a fresh login kicks
        out any other open session. That's why regular actions use
        `_authed_home()`, which reuses the session and only calls this if
        it expired.

        Returns the logged-in panel's HTML (already fetched here to confirm
        the login succeeded), so `_authed_home()` doesn't need to fetch it
        again. If already logged in, re-fetches it fresh instead of no-op'ing
        with nothing to return.
        """
        if self._logged_in:
            return self._home_html()
        if not self.ready:
            raise GixenError(
                "Missing Gixen credentials. Pass username and password when "
                "instantiating GixenClient."
            )
        # No GET to the index page first: verified live against a real
        # account that Gixen sets the session cookie straight off the login
        # POST itself (no CSRF/session token was ever scraped from that page
        # first, so there was nothing it actually fed into the POST below)
        # -- one fewer round trip on every cold login.
        self._post(LOGIN_URL, data={
            "username": self.username,
            "password": self.password,
            "signin": "signin",
            "Submit": "Log in Now",
        })
        # The POST returns a bridge page; the real panel is home_2.php.
        home = self._get(HOME_URL)
        if home.status_code != 200:
            raise GixenError(f"Gixen responded with HTTP {home.status_code} while logging in.")
        if not self._looks_logged_in(home.text):
            raise GixenError(
                "Gixen didn't accept the login. Check the username/password "
                "(they're your Gixen account's, not eBay's)."
            )
        self._logged_in = True
        return home.text

    def _home_html(self) -> str:
        """
        Panel HTML, or `""` if Gixen didn't respond with 200. Gixen
        sometimes returns HTTP 500 for an expired or invalid session (not
        just expired cookies); treating a `""` the same as "not logged in"
        in `_authed_home()` lets the automatic re-login also recover from
        that case, instead of trying to parse an error page as if it were
        the real panel.
        """
        r = self._get(HOME_URL)
        return r.text if r.status_code == 200 else ""

    # ---- Schedule a snipe ----------------------------------------------------
    def add_snipe(
        self,
        item_id: str,
        max_bid: float | str,
        offset: int | str | None = None,
        group: int | str | None = None,
        dry_run: bool | None = None,
    ) -> SnipeResult:
        """
        Schedules a snipe on Gixen for `item_id` with max bid `max_bid`.

        `offset` (seconds before the close) and `group` (bid group) are
        optional; if not passed, Gixen's own form default is left as-is
        (whatever comes marked as selected).

        If `dry_run` (or self.dry_run) is True, it sends NOTHING: it
        returns what it would send, to verify the field mapping.
        """
        dry = self.dry_run if dry_run is None else dry_run
        item_number = legacy_item_number(item_id)
        if not item_number.isdigit():
            raise GixenError(f"Invalid eBay item number: {item_id!r}")
        try:
            bid = f"{float(max_bid):.2f}"
        except (TypeError, ValueError):
            raise GixenError(f"Invalid max bid: {max_bid!r}")
        if float(bid) <= 0:
            raise GixenError("Max bid must be greater than 0.")
        offset_val = _validate_offset(offset)
        group_val = _validate_group(group)

        home_html = self._authed_home()
        if not dry and _snipe_row_present(home_html, item_number):
            raise GixenError(
                f"There's already an active snipe for item {item_number}; "
                "use update_snipe() to change its bid (Gixen silently "
                "ignores a duplicate add)."
            )

        form = _find_snipe_form(home_html)
        if form is None:
            raise GixenError(
                "Couldn't find the snipe form on Gixen (did their site change?). "
                "Run dry-run mode to inspect it."
            )

        payload = dict(form.fields)
        payload[form.item_field] = item_number
        payload[form.bid_field] = bid
        if form.qty_field and not payload.get(form.qty_field):
            payload[form.qty_field] = "1"
        if offset_val is not None:
            if form.offset_field:
                payload[form.offset_field] = offset_val
            if form.offset_mirror_field:
                payload[form.offset_mirror_field] = offset_val
        if group_val is not None and form.group_field:
            payload[form.group_field] = group_val

        if dry:
            return SnipeResult(
                ok=True, dry_run=True, item_number=item_number,
                payload=payload, action=form.action,
                message=(
                    f"[dry-run] Would send to {form.action} → "
                    f"{form.item_field}={item_number}, {form.bid_field}={bid}"
                ),
            )

        return self._submit_new_snipe(form, payload, item_number, bid)

    def _submit_new_snipe(
        self, form: SnipeForm, payload: dict[str, str], item_number: str, bid: str
    ) -> SnipeResult:
        """
        Submits the already-filled-in add form and interprets the response.

        If Gixen accepts the POST (HTTP 200, no explicit error line) but
        the item doesn't show up confirmed in the list, it could be a
        one-off silent glitch -- it's retried once after `retry_backoff`
        seconds before giving up. Not retried if Gixen already gave an
        explicit reason (e.g. "item has ended"): retrying that wouldn't
        change the outcome.
        """
        result = None
        for attempt in range(2):
            if form.method == "get":
                r = self._get(form.action, params=payload)
            else:
                r = self._post(form.action, data=payload)
            if r.status_code != 200:
                raise GixenError(f"Gixen responded with HTTP {r.status_code} while scheduling.")

            result = self._interpret_add_response(r.text, item_number, bid, form.action)
            if result.ok or _explicit_error_line(r.text):
                return result
            if attempt == 0:
                time.sleep(self.retry_backoff)
        return result

    # ---- List / edit / delete snipes ------------------------------------------
    def list_snipes(self) -> list[Snipe]:
        """Returns the snipes on the Gixen account."""
        return _parse_snipes(self._authed_home())

    def _find_modify_form(self, forms: list[_Form], item_number: str) -> _Form | None:
        return next((f for f in forms if f.fields.get("edititemid") == item_number), None)

    def update_snipe(
        self,
        item_id: str,
        new_max: float | str | None = None,
        offset: int | str | None = None,
        group: int | str | None = None,
    ) -> SnipeResult:
        """
        Changes the max bid, offset and/or group of an already-scheduled
        snipe. At least one of `new_max`/`offset`/`group` must be passed;
        whatever isn't passed is kept as-is.

        A direct POST changing only `editmaxbid` isn't enough (Gixen
        silently ignores it, confirmed against a real account). We need to
        send the same fields as the add form (`newitemid`/`newmaxbid`/
        offset/group/username) plus the snipe's internal id (`dbidid`) and
        a hidden `ismodified=1` flag: that's what tells Gixen this is an
        edit and not a fresh add.
        """
        if new_max is None and offset is None and group is None:
            raise GixenError("Provide at least one of: max bid, offset or group.")
        item_number = legacy_item_number(item_id)
        offset_val = _validate_offset(offset)
        group_val = _validate_group(group)

        forms = _parse_forms(self._authed_home())
        trigger = self._find_modify_form(forms, item_number)
        if trigger is None:
            raise GixenError("Couldn't find that snipe on Gixen to edit it.")
        dbidid_form = _find_dbidid_form(forms, item_number)
        if dbidid_form is None:
            raise GixenError("Couldn't find that snipe's internal id (dbidid) to edit it.")

        if new_max is not None:
            try:
                bid = f"{float(new_max):.2f}"
            except (TypeError, ValueError):
                raise GixenError(f"Invalid max bid: {new_max!r}")
            if float(bid) <= 0:
                raise GixenError("Max bid must be greater than 0.")
        else:
            bid = trigger.fields.get("editmaxbid", "0")

        current_offset = trigger.fields.get("editbidoffset", "")
        payload = {
            "newitemid": item_number,
            "newmaxbid": bid,
            "newbidoffset": offset_val if offset_val is not None else current_offset,
            "newbidoffsetmirror": offset_val if offset_val is not None
            else trigger.fields.get("editbidoffsetmirror", current_offset),
            "newsnipegroup": group_val if group_val is not None
            else trigger.fields.get("editsnipegroup", "0"),
            "username": trigger.fields.get("username", self.username),
            "dbidid": dbidid_form.fields["dbidid"],
            "ismodified": "1",
        }
        action = _trusted_action_url(BASE, trigger.action.split("#")[0])

        def _confirmed(html: str) -> bool:
            updated = self._find_modify_form(_parse_forms(html), item_number)
            if updated is None:
                return False
            if not _bid_matches(updated.fields.get("editmaxbid", "0"), bid):
                return False
            if offset_val is not None and updated.fields.get("editbidoffset") != offset_val:
                return False
            if group_val is not None and updated.fields.get("editsnipegroup") != group_val:
                return False
            return True

        changes = []
        if new_max is not None:
            changes.append(f"Bid updated to {bid}")
        if offset_val is not None:
            changes.append(f"offset to {offset_val}")
        if group_val is not None:
            changes.append(f"group to {group_val}")

        # The POST itself returns the already-updated panel, so we verify
        # against that response without an extra GET. If Gixen accepted the
        # POST but didn't apply the change (a one-off silent glitch), it's
        # retried once before giving up.
        for attempt in range(2):
            r = self._post(action, data=payload)
            if r.status_code != 200:
                raise GixenError(f"Gixen responded with HTTP {r.status_code} while updating.")
            if _confirmed(r.text):
                return SnipeResult(
                    ok=True, item_number=item_number, action=action,
                    message=f"{', '.join(changes)} for item {item_number}.",
                )
            if attempt == 0:
                time.sleep(self.retry_backoff)
        return SnipeResult(ok=False, item_number=item_number, action=action,
                           message="Couldn't confirm the change on Gixen; check the list.")

    def delete_snipe(self, item_id: str) -> SnipeResult:
        """Deletes a scheduled snipe."""
        item_number = legacy_item_number(item_id)
        forms = _parse_forms(self._authed_home())
        del_form = _find_dbidid_form(forms, item_number)
        if del_form is None:
            raise GixenError("Couldn't find that snipe's delete control.")
        action = _trusted_action_url(BASE, del_form.action.split("#")[0])

        # Same as update_snipe: the POST already returns the updated panel
        # (no extra GET), and it's retried once if Gixen doesn't confirm the
        # deletion on the first try (a one-off silent glitch).
        for attempt in range(2):
            r = self._post(action, data=dict(del_form.fields))
            if r.status_code != 200:
                raise GixenError(f"Gixen responded with HTTP {r.status_code} while deleting.")
            still = self._find_modify_form(_parse_forms(r.text), item_number)
            if still is None:
                return SnipeResult(ok=True, item_number=item_number, action=action,
                                   message=f"Snipe for item {item_number} deleted.")
            if attempt == 0:
                time.sleep(self.retry_backoff)
        return SnipeResult(ok=False, item_number=item_number, action=action,
                           message="Couldn't confirm the deletion on Gixen; check the list.")

    def purge_completed(self) -> SnipeResult:
        """
        Purges the already-ended snipes from the list (the ones Gixen keeps
        with a status other than "SCHEDULED"), using Gixen's native
        "Purge Completed" button (a single-field form,
        `purgecompleted=1` + `gixenlinkcontinue=1`). Doesn't affect active
        snipes.
        """
        forms = _parse_forms(self._authed_home())
        purge_form = next((f for f in forms if "purgecompleted" in f.fields), None)
        if purge_form is None:
            raise GixenError("Couldn't find the button to purge ended snipes on Gixen.")
        action = _trusted_action_url(BASE, purge_form.action.split("#")[0])

        for attempt in range(2):
            r = self._post(action, data=dict(purge_form.fields))
            if r.status_code != 200:
                raise GixenError(f"Gixen responded with HTTP {r.status_code} while purging.")
            remaining = [s for s in _parse_snipes(r.text) if s.status != "active"]
            if not remaining:
                return SnipeResult(ok=True, action=action,
                                    message="Ended snipes purged from the list.")
            if attempt == 0:
                time.sleep(self.retry_backoff)
        return SnipeResult(
            ok=False, action=action,
            message="Couldn't confirm that all ended snipes were purged; check the list.",
        )

    @staticmethod
    def _interpret_add_response(html: str, item_number: str, bid: str, action: str) -> SnipeResult:
        """
        Decides whether the snipe was scheduled by reading Gixen's response.

        Strong signal: after adding it, the item shows up in the list with
        its edit/delete controls (`edit_<itemid>` / `edititemid_<itemid>`).
        If it shows up, it's scheduled (this avoids false errors from the
        page's static help text).
        """
        if _snipe_row_present(html, item_number):
            return SnipeResult(
                ok=True, item_number=item_number, action=action,
                message=f"Snipe scheduled on Gixen: item {item_number}, max {bid}.",
            )
        # Not in the list: look for a short error line explaining why.
        err = _explicit_error_line(html)
        if err:
            return SnipeResult(
                ok=False, item_number=item_number, action=action,
                message=f"Gixen didn't schedule it: {err}",
            )
        return SnipeResult(
            ok=False, item_number=item_number, action=action,
            message="Inconclusive response from Gixen; check gixen.com to see if it was scheduled.",
        )
