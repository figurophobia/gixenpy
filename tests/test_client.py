"""
Tests for the Gixen client (no network).

Cover item-number extraction, snipe-form parsing, dry-run payload building,
and response interpretation. No request is ever made: the logged-in Gixen
page is replaced with test HTML.
"""
import pytest

from gixenpy.client import (
    BASE,
    HOME_URL,
    GixenClient,
    GixenError,
    SnipeForm,
    _bid_matches,
    _find_snipe_form,
    _normalize_status,
    _parse_snipes,
    _snipe_current_bids,
    _snipe_row_present,
    _snipe_statuses,
    _trusted_action_url,
    legacy_item_number,
)

# Fake logged-in page: logout link + a search box + the snipe form.
FAKE_HOME = """
<a href="logout.php">Log Out</a>
<form action="search.php" method="get"><input name="q" type="text"></form>
<form action="home_1.php" method="post">
  <input type="hidden" name="csrftoken" value="tok-xyz">
  <input type="text" name="itemid" value="">
  <input type="text" name="maxbid" value="">
  <input type="text" name="quantity" value="1">
  <input type="hidden" name="snipegroup" value="0">
  <input type="submit" name="addsnipe" value="Add Snipe">
</form>
"""


@pytest.mark.parametrize("raw,exp", [
    ("v1|123456789012|0", "123456789012"),
    ("123456789012", "123456789012"),
    ("https://www.ebay.com/itm/123456789012", "123456789012"),
    ("v1|987654321098|123456", "987654321098"),
])
def test_legacy_item_number(raw, exp):
    assert legacy_item_number(raw) == exp


def test_find_snipe_form_ok():
    f = _find_snipe_form(FAKE_HOME)
    assert isinstance(f, SnipeForm)
    assert f.item_field == "itemid" and f.bid_field == "maxbid"
    assert f.qty_field == "quantity"
    assert f.action == "https://www.gixen.com/main/home_1.php"
    assert f.fields["csrftoken"] == "tok-xyz"  # the hidden token is kept as-is


def test_find_snipe_form_none_when_missing():
    assert _find_snipe_form("<form action='x'><input name='q'></form>") is None


# REAL Gixen structure (home_2.php): the EDIT form (edit…, already filled in)
# and the ADD form (new…, empty) coexist. We must pick the add one and not
# confuse 'newbidoffset' with the bid.
REAL_HOME = """
<a href="?logout">Log Out</a>
<form action="home_2.php?sessionid=999#modifyme" method="post">
  <input name="edititemid" value="227416916343">
  <input name="editmaxbid" value="50.00">
  <input name="editbidoffset" value="6">
  <input name="editsnipegroup" value="0">
  <input name="username" value="someuser">
</form>
<form action="home_2.php?sessionid=999" method="post">
  <input name="newitemid" value="">
  <input name="newmaxbid" value="">
  <input name="newbidoffset" value="6">
  <input name="newbidoffsetmirror" value="8">
  <input name="newsnipegroup" value="0">
  <input name="username" value="someuser">
</form>
"""


def test_find_snipe_form_picks_add_not_edit():
    f = _find_snipe_form(REAL_HOME)
    assert f.item_field == "newitemid"       # the add one, not 'edititemid'
    assert f.bid_field == "newmaxbid"        # the bid, NOT 'newbidoffset'
    assert f.fields["username"] == "someuser"  # gets resent
    assert f.fields["newsnipegroup"] == "0"


# The real form uses <select> for group/offset: we must send its default
# value ('selected' option, or the first one if none is marked), never an
# empty string.
SELECT_HOME = """
<a href="?logout">Log Out</a>
<form action="home_2.php?sessionid=1" method="post" name="addsnipe">
  <input name="newitemid" value="">
  <input name="newmaxbid" value="">
  <input name="username" type="hidden" value="u">
  <select name="newsnipegroup"><option value="0">Not grouped</option><option value="1">G1</option></select>
  <select name="newbidoffset"><option value="6">6</option><option value="1">1</option></select>
  <select name="newbidoffsetmirror"><option value="6">6</option><option value="8" selected>8</option></select>
  <input type="submit" value=" Add ">
</form>
"""


def test_select_defaults_are_captured():
    f = _find_snipe_form(SELECT_HOME)
    assert f.fields["newsnipegroup"] == "0"     # first option
    assert f.fields["newbidoffset"] == "6"      # first option (not empty)
    assert f.fields["newbidoffsetmirror"] == "8"  # the one marked 'selected' wins


def _client_logged(monkeypatch, home=FAKE_HOME):
    c = GixenClient(retry_backoff=0)  # no real sleeps on retry in tests
    c._logged_in = True  # skip the real login
    monkeypatch.setattr(c, "_home_html", lambda: home)
    return c


def test_dry_run_sends_nothing_and_builds_payload(monkeypatch):
    c = _client_logged(monkeypatch)
    res = c.add_snipe("v1|123456789012|0", 42, dry_run=True)
    assert res.ok and res.dry_run
    assert res.item_number == "123456789012"
    assert res.payload["itemid"] == "123456789012"
    assert res.payload["maxbid"] == "42.00"
    assert res.payload["csrftoken"] == "tok-xyz"  # hidden field is resent
    assert res.action.endswith("home_1.php")


def test_invalid_item_raises(monkeypatch):
    c = _client_logged(monkeypatch)
    with pytest.raises(GixenError):
        c.add_snipe("not-numeric", 10, dry_run=True)


@pytest.mark.parametrize("bad", [0, -5, "abc"])
def test_invalid_bid_raises(monkeypatch, bad):
    c = _client_logged(monkeypatch)
    with pytest.raises(GixenError):
        c.add_snipe("123456789012", bad, dry_run=True)


def test_interpret_success():
    # Strong real Gixen signal: the item shows up with its edit_<id> control.
    res = GixenClient._interpret_add_response(
        "<input name='edititemid_123456789012' value='123456789012'>"
        "<input type='submit' name='edit_123456789012'>",
        "123456789012", "42.00", "home_2.php")
    assert res.ok and "scheduled" in res.message.lower()


def test_interpret_avoids_false_positive():
    # If the number only shows up echoed in the field (not in the list), it's NOT a success.
    res = GixenClient._interpret_add_response(
        "<input name='newitemid' value='123456789012'>",
        "123456789012", "42.00", "home_2.php")
    assert not res.ok


def test_interpret_error():
    res = GixenClient._interpret_add_response(
        "<p>ERROR: item has ended, could not add</p>",
        "123456789012", "42.00", "home_1.php")
    assert not res.ok and "didn't schedule" in res.message.lower()


# Real snipe list: one 'modify' form (edititemid…) per snipe + its 'delete' one.
SNIPE_LIST_HOME = """
<a href="?logout">Log Out</a>
<form action="home_2.php?sessionid=1#modifyme" method="post">
  <input name="edititemid" value="227420152021">
  <input name="editmaxbid" value="70.00">
  <input name="editbidoffset" value="6"><input name="editsnipegroup" value="0">
  <input name="editcomment" value=""><input name="username" value="u">
</form>
<form action="home_2.php?sessionid=1" method="post">
  <input name="dbidid" value="132981433"><input name="username" value="u">
</form>
<form action="home_2.php?sessionid=1#modifyme" method="post">
  <input name="edititemid" value="111122223333">
  <input name="editmaxbid" value="25.50">
  <input name="editbidoffset" value="6"><input name="editsnipegroup" value="0">
  <input name="username" value="u">
</form>
"""


def test_parse_snipes_list():
    snipes = _parse_snipes(SNIPE_LIST_HOME)
    got = {s.item_id: s.max_bid for s in snipes}
    assert got == {"227420152021": "70.00", "111122223333": "25.50"}
    assert snipes[0].offset == "6" and snipes[0].ebay_url.endswith("227420152021")


def test_parse_snipes_empty():
    assert _parse_snipes("<p>no snipes</p>") == []


def test_interpret_inconclusive():
    res = GixenClient._interpret_add_response(
        "<p>Some unrelated page</p>", "123456789012", "42.00", "home_1.php")
    assert not res.ok


# --------------------------------------------------------------------------- #
# Security: don't follow a form 'action' pointing outside gixen.com
# --------------------------------------------------------------------------- #
def test_trusted_action_url_accepts_relative():
    assert _trusted_action_url(BASE, "home_2.php") == "https://www.gixen.com/main/home_2.php"


def test_trusted_action_url_empty_uses_home():
    assert _trusted_action_url(BASE, "") == HOME_URL


def test_trusted_action_url_accepts_gixen_subdomain():
    assert _trusted_action_url(BASE, "https://snipe.gixen.com/x") == "https://snipe.gixen.com/x"


def test_trusted_action_url_rejects_foreign_host():
    with pytest.raises(GixenError):
        _trusted_action_url(BASE, "https://evil.example.com/steal")


def test_trusted_action_url_rejects_lookalike_host():
    # 'gixen.com.evil.example' is NOT gixen.com nor a subdomain of it.
    with pytest.raises(GixenError):
        _trusted_action_url(BASE, "https://gixen.com.evil.example/steal")


def test_trusted_action_url_rejects_plain_http():
    with pytest.raises(GixenError):
        _trusted_action_url(BASE, "http://gixen.com/x")


MALICIOUS_ACTION_HOME = """
<a href="?logout">Log Out</a>
<form action="https://evil.example.com/steal" method="post" name="addsnipe">
  <input name="newitemid" value="">
  <input name="newmaxbid" value="">
  <input name="username" type="hidden" value="u">
</form>
"""


def test_find_snipe_form_rejects_foreign_action():
    with pytest.raises(GixenError):
        _find_snipe_form(MALICIOUS_ACTION_HOME)


MALICIOUS_MODIFY_HOME = """
<a href="?logout">Log Out</a>
<form action="https://evil.example.com/modify" method="post">
  <input name="edititemid" value="227420152021">
  <input name="editmaxbid" value="70.00">
  <input name="username" value="u">
</form>
<form action="home_2.php?sessionid=1" method="post">
  <input name="dbidid" value="999"><input name="username" value="u">
</form>
"""


def test_update_snipe_rejects_foreign_action(monkeypatch):
    # With dbidid present, so the rejection is really about the foreign
    # 'action' and not a missing dbidid (a separate check).
    c = _client_logged(monkeypatch, home=MALICIOUS_MODIFY_HOME)
    with pytest.raises(GixenError, match="outside gixen.com"):
        c.update_snipe("227420152021", 99)


MALICIOUS_DELETE_HOME = """
<a href="?logout">Log Out</a>
<form action="home_2.php?sessionid=1#modifyme" method="post">
  <input name="edititemid" value="227420152021">
  <input name="editmaxbid" value="70.00">
  <input name="username" value="u">
</form>
<form action="https://evil.example.com/delete" method="post">
  <input name="dbidid" value="132981433"><input name="username" value="u">
</form>
"""


def test_delete_snipe_rejects_foreign_action(monkeypatch):
    c = _client_logged(monkeypatch, home=MALICIOUS_DELETE_HOME)
    with pytest.raises(GixenError):
        c.delete_snipe("227420152021")


# --------------------------------------------------------------------------- #
# Performance: delete must not fire an extra GET just to verify.
# --------------------------------------------------------------------------- #
AFTER_DELETE_HOME = """
<a href="?logout">Log Out</a>
<form action="home_2.php?sessionid=1#modifyme" method="post">
  <input name="edititemid" value="111122223333">
  <input name="editmaxbid" value="25.50">
  <input name="editbidoffset" value="6"><input name="editsnipegroup" value="0">
  <input name="username" value="u">
</form>
"""


def test_delete_snipe_reuses_post_response_no_extra_get(monkeypatch):
    c = _client_logged(monkeypatch, home=SNIPE_LIST_HOME)
    home_calls = {"n": 0}

    def counting_home_html():
        home_calls["n"] += 1
        return SNIPE_LIST_HOME

    monkeypatch.setattr(c, "_home_html", counting_home_html)

    class FakeResp:
        status_code = 200
        text = AFTER_DELETE_HOME

    post_calls = {"n": 0}

    def fake_post(url, **kw):
        post_calls["n"] += 1
        return FakeResp()

    monkeypatch.setattr(c, "_post", fake_post)

    res = c.delete_snipe("227420152021")
    assert res.ok
    assert post_calls["n"] == 1
    assert home_calls["n"] == 1


# --------------------------------------------------------------------------- #
# Configurable timeout (connect, read)
# --------------------------------------------------------------------------- #
def test_default_timeout():
    assert GixenClient().timeout == (5, 25)


def test_configurable_timeout():
    assert GixenClient(timeout=(2, 10)).timeout == (2, 10)


# --------------------------------------------------------------------------- #
# update_snipe: a direct POST changing only `editmaxbid` isn't enough (Gixen
# silently ignores it); the add-form payload + `dbidid` + `ismodified=1` is
# needed, all in a single POST. Confirmed against a real account.
# --------------------------------------------------------------------------- #
def test_snipe_row_present():
    assert _snipe_row_present("<input name='edit_123'>", "123")
    assert _snipe_row_present("<input name='edititemid_123'>", "123")
    assert not _snipe_row_present("<input name='edit_456'>", "123")


UPDATE_HOME = """
<a href="?logout">Log Out</a>
<form action="home_2.php?sessionid=1#modifyme" method="post">
  <input name="edititemid" value="227425016088">
  <input name="editmaxbid" value="60.00">
  <input name="editbidoffset" value="10">
  <input name="editbidoffsetmirror" value="10">
  <input name="editsnipegroup" value="2">
  <input name="editcomment" value="">
  <input name="username" value="u">
</form>
<form action="home_2.php?sessionid=1" method="post">
  <input name="dbidid" value="999"><input name="username" value="u">
</form>
<form action="home_2.php?sessionid=1" method="post">
  <input name="newitemid" value="">
  <input name="newmaxbid" value="">
  <input name="username" value="u">
</form>
"""


def test_update_snipe_single_post_with_dbidid_and_ismodified(monkeypatch):
    c = _client_logged(monkeypatch, home=UPDATE_HOME)
    posts = []

    def fake_post(url, **kw):
        data = kw.get("data", {})
        posts.append(data)

        class R:
            status_code = 200
            # The POST itself returns the panel with the bid already updated.
            text = UPDATE_HOME.replace('value="60.00"', f'value="{data["newmaxbid"]}"')

        return R()

    monkeypatch.setattr(c, "_post", fake_post)

    res = c.update_snipe("227425016088", 80)

    assert res.ok
    assert "Bid updated to 80.00" in res.message
    assert len(posts) == 1
    payload = posts[0]
    assert payload["ismodified"] == "1"
    assert payload["dbidid"] == "999"
    assert payload["newitemid"] == "227425016088"
    assert payload["newmaxbid"] == "80.00"
    # The offset/group the snipe already had are kept, read from the row.
    assert payload["newbidoffset"] == "10"
    assert payload["newbidoffsetmirror"] == "10"
    assert payload["newsnipegroup"] == "2"


def test_update_snipe_dbidid_not_found(monkeypatch):
    # Home with the "modify" form but no delete one (no dbidid).
    NO_DBIDID_HOME = """
    <a href="?logout">Log Out</a>
    <form action="home_2.php?sessionid=1#modifyme" method="post">
      <input name="edititemid" value="227425016088">
      <input name="editmaxbid" value="60.00">
      <input name="username" value="u">
    </form>
    """
    c = _client_logged(monkeypatch, home=NO_DBIDID_HOME)
    with pytest.raises(GixenError, match="dbidid"):
        c.update_snipe("227425016088", 80)


def test_update_snipe_unconfirmed_returns_ok_false(monkeypatch):
    c = _client_logged(monkeypatch, home=UPDATE_HOME)

    def fake_post(url, **kw):
        class R:
            status_code = 200
            text = UPDATE_HOME  # the POST doesn't reflect the change (still 60.00)

        return R()

    monkeypatch.setattr(c, "_post", fake_post)

    res = c.update_snipe("227425016088", 80)
    assert not res.ok
    assert "Couldn't confirm the change" in res.message


# --------------------------------------------------------------------------- #
# add_snipe: if Gixen already has the item, a duplicate add does nothing
# (and used to be wrongly reported as a success) -- now it's rejected early.
# --------------------------------------------------------------------------- #
ADD_DUP_HOME = """
<a href="?logout">Log Out</a>
<form action="home_2.php?sessionid=1#modifyme" method="post">
  <input name="edititemid" value="227420152021">
  <input name="editmaxbid" value="70.00">
  <input name="username" value="u">
</form>
<input name="edit_227420152021" type="submit" value="Edit">
<form action="home_2.php?sessionid=1" method="post">
  <input name="newitemid" value="">
  <input name="newmaxbid" value="">
  <input name="username" value="u">
</form>
"""


def test_add_snipe_rejects_if_already_exists(monkeypatch):
    c = _client_logged(monkeypatch, home=ADD_DUP_HOME)
    with pytest.raises(GixenError, match="already an active snipe"):
        c.add_snipe("227420152021", 42, dry_run=False)


def test_add_snipe_dry_run_not_blocked_even_if_exists(monkeypatch):
    c = _client_logged(monkeypatch, home=ADD_DUP_HOME)
    res = c.add_snipe("227420152021", 42, dry_run=True)
    assert res.ok and res.dry_run


# --------------------------------------------------------------------------- #
# Patterns borrowed from another independent implementation of the same
# problem (github.com/hsukenooi/comic-pipeline, packages/gixen-cli): retry on
# a silent glitch, format-tolerant bid comparison, and HTTP 500 treated as an
# expired session.
# --------------------------------------------------------------------------- #
def test_default_retry_backoff():
    assert GixenClient().retry_backoff == 2.0


def test_bid_matches_tolerates_formatting():
    assert _bid_matches("80.00 USD", "80.00")
    assert _bid_matches(" 80.00 ", "80.00")
    assert not _bid_matches("60.00", "80.00")
    assert not _bid_matches("", "80.00")
    assert not _bid_matches("abc", "80.00")


def test_add_snipe_retries_if_gixen_does_not_confirm(monkeypatch):
    c = _client_logged(monkeypatch, home=FAKE_HOME)
    calls = {"n": 0}

    def fake_post(url, **kw):
        calls["n"] += 1

        class R:
            status_code = 200
            text = (
                "<p>page with no relevant information</p>"
                if calls["n"] == 1
                else "<input name='edit_123456789012'>"  # 2nd attempt: it does show up
            )

        return R()

    monkeypatch.setattr(c, "_post", fake_post)
    res = c.add_snipe("123456789012", 42, dry_run=False)
    assert res.ok
    assert calls["n"] == 2


def test_add_snipe_does_not_retry_on_explicit_rejection(monkeypatch):
    c = _client_logged(monkeypatch, home=FAKE_HOME)
    calls = {"n": 0}

    def fake_post(url, **kw):
        calls["n"] += 1

        class R:
            status_code = 200
            text = "<p>ERROR: item has ended, could not add</p>"

        return R()

    monkeypatch.setattr(c, "_post", fake_post)
    res = c.add_snipe("123456789012", 42, dry_run=False)
    assert not res.ok
    assert calls["n"] == 1  # an explicit rejection is not retried


def test_update_snipe_retries_if_unconfirmed(monkeypatch):
    c = _client_logged(monkeypatch, home=UPDATE_HOME)
    calls = {"n": 0}

    def fake_post(url, **kw):
        calls["n"] += 1
        data = kw.get("data", {})

        class R:
            status_code = 200
            text = (
                UPDATE_HOME  # 1st attempt: doesn't reflect the change
                if calls["n"] == 1
                else UPDATE_HOME.replace('value="60.00"', f'value="{data["newmaxbid"]}"')
            )

        return R()

    monkeypatch.setattr(c, "_post", fake_post)
    res = c.update_snipe("227425016088", 80)
    assert res.ok
    assert calls["n"] == 2


def test_delete_snipe_retries_if_unconfirmed(monkeypatch):
    c = _client_logged(monkeypatch, home=SNIPE_LIST_HOME)
    calls = {"n": 0}

    def fake_post(url, **kw):
        calls["n"] += 1

        class R:
            status_code = 200
            text = SNIPE_LIST_HOME if calls["n"] == 1 else AFTER_DELETE_HOME

        return R()

    monkeypatch.setattr(c, "_post", fake_post)
    res = c.delete_snipe("227420152021")
    assert res.ok
    assert calls["n"] == 2


def test_authed_home_relogins_if_home_html_empty(monkeypatch):
    # _home_html() returns "" when Gixen responds with something other than
    # 200 (e.g. HTTP 500 on an expired session) -- must be treated the same
    # as "not logged in" and relogin, not parse "" as if it were the real
    # panel.
    c = GixenClient(username="u", password="p", retry_backoff=0)
    c._logged_in = True
    calls = {"n": 0}

    def fake_home_html():
        calls["n"] += 1
        return ""

    monkeypatch.setattr(c, "_home_html", fake_home_html)
    logins = {"n": 0}

    def fake_login():
        # login() now returns the panel HTML it fetched to confirm the
        # login worked, instead of _authed_home() fetching it again.
        logins["n"] += 1
        c._logged_in = True
        return FAKE_HOME

    monkeypatch.setattr(c, "login", fake_login)

    assert c._authed_home() == FAKE_HOME
    assert logins["n"] == 1
    # Solo la comprobación inicial de sesión caducada llama a _home_html();
    # login() ya no se vuelve a consultar aparte, devuelve el HTML él mismo.
    assert calls["n"] == 1


def test_authed_home_fails_if_still_empty_after_relogin(monkeypatch):
    c = GixenClient(username="u", password="p", retry_backoff=0)
    c._logged_in = False
    monkeypatch.setattr(c, "_home_html", lambda: "")
    monkeypatch.setattr(c, "login", lambda: setattr(c, "_logged_in", True))

    with pytest.raises(GixenError, match="didn't return the panel"):
        c._authed_home()


# --------------------------------------------------------------------------- #
# Offset / group: field detection in the add form, and support in
# add_snipe/update_snipe.
# --------------------------------------------------------------------------- #
def test_find_snipe_form_detects_offset_and_group():
    f = _find_snipe_form(REAL_HOME)
    assert f.offset_field == "newbidoffset"
    assert f.offset_mirror_field == "newbidoffsetmirror"
    assert f.group_field == "newsnipegroup"


def test_add_snipe_dry_run_with_offset_and_group(monkeypatch):
    c = _client_logged(monkeypatch, home=REAL_HOME)
    res = c.add_snipe("227416916343", 42, offset=9, group=3, dry_run=True)
    assert res.payload["newbidoffset"] == "9"
    assert res.payload["newbidoffsetmirror"] == "9"
    assert res.payload["newsnipegroup"] == "3"


@pytest.mark.parametrize("bad_offset", [0, -1, "abc"])
def test_add_snipe_invalid_offset_raises(monkeypatch, bad_offset):
    c = _client_logged(monkeypatch, home=REAL_HOME)
    with pytest.raises(GixenError):
        c.add_snipe("227416916343", 42, offset=bad_offset, dry_run=True)


@pytest.mark.parametrize("bad_group", [-1, "abc"])
def test_add_snipe_invalid_group_raises(monkeypatch, bad_group):
    c = _client_logged(monkeypatch, home=REAL_HOME)
    with pytest.raises(GixenError):
        c.add_snipe("227416916343", 42, group=bad_group, dry_run=True)


def test_update_snipe_with_no_field_raises(monkeypatch):
    c = _client_logged(monkeypatch, home=UPDATE_HOME)
    with pytest.raises(GixenError, match="at least one"):
        c.update_snipe("227425016088")


def test_update_snipe_offset_only_keeps_bid(monkeypatch):
    c = _client_logged(monkeypatch, home=UPDATE_HOME)
    posts = []

    def fake_post(url, **kw):
        data = kw.get("data", {})
        posts.append(data)

        class R:
            status_code = 200
            text = (
                UPDATE_HOME
                .replace('value="10"', f'value="{data["newbidoffset"]}"')
            )

        return R()

    monkeypatch.setattr(c, "_post", fake_post)
    res = c.update_snipe("227425016088", offset=12)
    assert res.ok
    assert "offset to 12" in res.message
    payload = posts[0]
    assert payload["newmaxbid"] == "60.00"  # keeps the existing bid
    assert payload["newbidoffset"] == "12"
    assert payload["newbidoffsetmirror"] == "12"


def test_update_snipe_group_only(monkeypatch):
    c = _client_logged(monkeypatch, home=UPDATE_HOME)

    def fake_post(url, **kw):
        data = kw.get("data", {})

        class R:
            status_code = 200
            text = UPDATE_HOME.replace('value="2"', f'value="{data["newsnipegroup"]}"')

        return R()

    monkeypatch.setattr(c, "_post", fake_post)
    res = c.update_snipe("227425016088", group=5)
    assert res.ok
    assert "group to 5" in res.message


# --------------------------------------------------------------------------- #
# Active/ended status: Gixen shows "Status (main): <value>" before each
# snipe's form. Only "SCHEDULED" is treated as active.
# --------------------------------------------------------------------------- #
STATUS_HOME = """
<a href="?logout">Log Out</a>
<tr class=d3><td>Max bid: 30.00 USD</td><td>Current bid: 28.50 USD</td></tr>
<tr class=d3><td>Status (main): </td><td>BID UNDER ASKING PRICE</td></tr>
<tr class=d3><td>Status (mirror): </td><td>N/A</td></tr>
<form action="home_2.php?sessionid=1#modifyme" method="post">
  <input name="edititemid" value="111111111111">
  <input name="editmaxbid" value="30.00">
  <input name="username" value="u">
</form>
<tr class=d3><td>Max bid: 45.00 USD</td><td>Current bid: 40.00 USD</td></tr>
<tr class=d3><td>Status (main): </td><td>SCHEDULED</td></tr>
<tr class=d3><td>Status (mirror): </td><td>SCHEDULED</td></tr>
<form action="home_2.php?sessionid=1#modifyme" method="post">
  <input name="edititemid" value="222222222222">
  <input name="editmaxbid" value="45.00">
  <input name="username" value="u">
</form>
"""


def test_snipe_statuses_pairs_by_order():
    statuses = _snipe_statuses(STATUS_HOME)
    assert statuses == {
        "111111111111": "BID UNDER ASKING PRICE",
        "222222222222": "SCHEDULED",
    }


@pytest.mark.parametrize("raw,expected", [
    ("SCHEDULED", "active"),
    ("scheduled", "active"),
    ("WON", "won"),
    ("won", "won"),
    ("BID UNDER ASKING PRICE", "lost"),
    ("LOST", "lost"),
    ("FAILED", "lost"),
    ("OUTBID", "lost"),
    ("SOME NEW STATUS GIXEN INVENTS", "ended"),
    (None, "unknown"),
    ("", "unknown"),
])
def test_normalize_status(raw, expected):
    assert _normalize_status(raw) == expected


def test_parse_snipes_includes_status():
    snipes = {s.item_id: s.status for s in _parse_snipes(STATUS_HOME)}
    assert snipes == {"111111111111": "lost", "222222222222": "active"}


def test_snipe_current_bids_pairs_by_order():
    bids = _snipe_current_bids(STATUS_HOME)
    assert bids == {
        "111111111111": "28.50 USD",
        "222222222222": "40.00 USD",
    }


def test_parse_snipes_includes_current_bid():
    # For an ended snipe (won or lost) this is the auction's actual final
    # price -- eBay no longer serves that listing once it's out of search
    # results, so Gixen's own page is the only place left to read it.
    snipes = {s.item_id: s.current_bid for s in _parse_snipes(STATUS_HOME)}
    assert snipes == {"111111111111": "28.50 USD", "222222222222": "40.00 USD"}


def test_parse_snipes_current_bid_empty_if_missing():
    snipes = _parse_snipes(SNIPE_LIST_HOME)
    assert all(s.current_bid == "" for s in snipes)


def test_parse_snipes_status_unknown_if_missing():
    # With no "Status (main)" marker before it, we don't make up a status.
    snipes = _parse_snipes(SNIPE_LIST_HOME)
    assert all(s.status == "unknown" for s in snipes)


# --------------------------------------------------------------------------- #
# purge_completed: Gixen's native single-field button.
# --------------------------------------------------------------------------- #
PURGE_HOME = STATUS_HOME + """
<form action="home_2.php?sessionid=1" method="post">
  <input type="submit" value="Purge Completed" />
  <input name="purgecompleted" type="hidden" value="1" />
  <input name="gixenlinkcontinue" type="hidden" value="1" />
</form>
"""

AFTER_PURGE_HOME = """
<a href="?logout">Log Out</a>
<tr class=d3><td>Status (main): </td><td>SCHEDULED</td></tr>
<tr class=d3><td>Status (mirror): </td><td>SCHEDULED</td></tr>
<form action="home_2.php?sessionid=1#modifyme" method="post">
  <input name="edititemid" value="222222222222">
  <input name="editmaxbid" value="45.00">
  <input name="username" value="u">
</form>
"""


def test_purge_completed_ok(monkeypatch):
    c = _client_logged(monkeypatch, home=PURGE_HOME)

    def fake_post(url, **kw):
        class R:
            status_code = 200
            text = AFTER_PURGE_HOME

        return R()

    monkeypatch.setattr(c, "_post", fake_post)
    res = c.purge_completed()
    assert res.ok
    assert "purged" in res.message


def test_purge_completed_retries_if_unconfirmed(monkeypatch):
    c = _client_logged(monkeypatch, home=PURGE_HOME)
    calls = {"n": 0}

    def fake_post(url, **kw):
        calls["n"] += 1

        class R:
            status_code = 200
            text = PURGE_HOME if calls["n"] == 1 else AFTER_PURGE_HOME

        return R()

    monkeypatch.setattr(c, "_post", fake_post)
    res = c.purge_completed()
    assert res.ok
    assert calls["n"] == 2


def test_purge_completed_button_not_found(monkeypatch):
    c = _client_logged(monkeypatch, home=FAKE_HOME)
    with pytest.raises(GixenError, match="purge"):
        c.purge_completed()
