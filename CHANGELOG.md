# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.3.1] - 2026-07-17

### Changed
- Cold login is now 2 HTTP requests instead of 4 (measured against a real
  account: ~9.3s → ~5.9s). Two round trips were pure waste:
  - `login()` already fetched the panel HTML to confirm the login worked,
    then threw it away; `_authed_home()` fetched the exact same URL again
    right after. `login()` now returns that HTML so `_authed_home()` reuses
    it instead of re-fetching.
  - The GET to the index page before the login POST (`INDEX_URL`, now
    removed) turned out to feed nothing into the login request — no CSRF
    or session token was ever scraped from it. Verified live that Gixen
    sets the session cookie straight off the login POST itself with no
    prior GET needed.
- `login()`'s return type changed from `None` to `str` (the logged-in
  panel's HTML). Nothing in this package's own code relied on it returning
  `None`; a caller that does `client.login()` and ignores the return value
  is unaffected.

## [0.3.0] - 2026-07-17

### Changed
- `Snipe.status` now distinguishes **won** and **lost** snipes instead of
  collapsing every non-`"SCHEDULED"` status into a single `"ended"` bucket.
  Gixen's own "Status (main): ..." text already carries this: a literal
  `"WON"` maps to `"won"`; `"LOST"`, `"FAILED"`, `"OUTBID"` and the
  confirmed-live `"BID UNDER ASKING PRICE"` all map to `"lost"` (substring
  match, case-insensitive). Any other non-empty, unrecognized terminal text
  still maps to `"ended"` rather than being guessed into won/lost.
  **This changes the set of values `Snipe.status` can take** — code doing
  `status == "ended"` to mean "not active anymore" should switch to
  `status != "active"`.
- `purge_completed()` now checks `status != "active"` (not just
  `== "ended"`) when confirming the purge succeeded, so it correctly
  recognizes `"won"`/`"lost"` snipes as already-terminal too.
- CLI `list` command: splits output into Active / Won / Lost / Ended
  (Ended only shown if non-empty) instead of just Active / Ended.

## [0.2.2] - 2026-07-14

### Changed
- No functional changes. Test release to verify the automated PyPI
  publishing pipeline (GitHub Release → `.github/workflows/publish.yml` →
  Trusted Publishing) end to end after configuring the trusted publisher
  on PyPI.

## [0.2.1] - 2026-07-14

### Added
- `.github/workflows/publish.yml`: publishing a GitHub Release now builds
  and uploads the package to PyPI automatically, using
  [Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC —
  no PyPI token stored as a secret).

### Changed
- README: install instructions simplified to `pip install gixenpy` only
  (the PyPI project page's README is baked in at upload time and doesn't
  update from GitHub on its own, so this needed a new release to show up).

## [0.2.0] - 2026-07-14

### Added
- Optional `offset` and `group` in `add_snipe`/`update_snipe` (seconds
  before the close and bid group). In `update_snipe` the three fields
  (bid/offset/group) are independent: at least one must be passed, and
  whatever isn't passed is kept as-is.
- `purge_completed()`: purges already-ended snipes from the history, using
  Gixen's native "Purge Completed" button.
- `Snipe.status` (`"active"` / `"ended"` / `"unknown"`): status detection
  from the "Status (main): ..." text Gixen prints next to each row.
- CLI rewritten with [`click`](https://click.palletsprojects.com/):
  `gixenpy list/add/edit/remove/purge/group`, with `--help`/`--version` and
  colored output (success in green, error in red on stderr). Replaces the
  previous verification-only CLI (`login`/`dry-run`/`snipe`).
- New dependency: `click>=8.1`.
- `tests/test_cli.py`: offline CLI test suite with `click.testing.CliRunner`.

### Changed
- Compared the set of operations and the CLI rewrite against
  [`gixen-cli`](https://github.com/hsukenooi/comic-pipeline/tree/main/packages/gixen-cli)
  (another author, same problem): same set of basic commands
  (list/add/edit/remove/purge/group), without their FastAPI+SQLite server,
  plugins (`pluggy`) or direct eBay bidding via Playwright — out of scope
  for gixenpy (an embeddable client, not a tool with a server).

## [0.1.0] - Unreleased

### Added
- `GixenClient`: login/session, `add_snipe`, `list_snipes`, `update_snipe`,
  `delete_snipe`, dry-run mode by default.
- `gixenpy` verification CLI (`login`, `dry-run`, `snipe`).
- Dynamic snipe-form detection via field heuristics (no hardcoded field
  names).
- Host/scheme validation (`https://gixen.com` or a subdomain) for any form
  URL extracted from the HTML, before sending requests.
- `delete_snipe` verifies the result by reusing the POST's own response,
  with no extra GET.
- Configurable connect/read timeout (default `(5, 25)` s).
- `py.typed` marker (PEP 561) for consumers with type checking.
- Offline test suite (no network).
- Support for local credentials via `.env` (`.env.example` as a template;
  `cli.py` loads it only if it exists, no new dependency).
- `retry_backoff` (2 s by default, configurable in `GixenClient(...)`):
  `add_snipe`/`update_snipe`/`delete_snipe` retry once if Gixen accepts the
  write (HTTP 200) but doesn't apply it, unless the rejection is explicit
  (e.g. "item has ended").
- Format-tolerant bid comparison (`_bid_matches`, via `Decimal`) when
  verifying changes, instead of comparing Gixen's raw text.
- `HTTP 500` on the panel is treated the same as an expired session
  (automatic relogin), not just expired cookies.

### Changed
- `update_snipe` no longer changes just `editmaxbid` (Gixen silently
  ignores it, confirmed against a real account). It now sends, in a single
  POST, the same payload as `add_snipe` plus `dbidid` (the row's internal
  id) and a hidden `ismodified=1` flag — the marker Gixen's backend uses to
  recognize an edit rather than a fresh add. Replaces two earlier discarded
  implementations (a two-POST "Edit"→"Modify" flow, and before that,
  deleting and rescheduling the snipe as a workaround) — neither was
  needed.
- `add_snipe` explicitly rejects (`GixenError`) scheduling an item that
  already has an active snipe, instead of reporting success: Gixen silently
  ignored the duplicate add and the previous success message was a false
  positive.
