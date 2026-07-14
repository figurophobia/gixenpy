"""
CLI tests (`click.testing.CliRunner`), no network: `_client_from_env` is
replaced with a fake client with whatever behavior each test needs.
"""
from click.testing import CliRunner

from gixenpy.cli import main
from gixenpy.client import GixenError, Snipe, SnipeResult


class FakeClient:
    def __init__(self, **behaviors):
        self._behaviors = behaviors

    def _call(self, name, *args, **kwargs):
        behavior = self._behaviors.get(name)
        if behavior is None:
            raise AssertionError(f"{name} was not expected in this test")
        if isinstance(behavior, Exception):
            raise behavior
        if callable(behavior):
            return behavior(*args, **kwargs)
        return behavior

    def list_snipes(self):
        return self._call("list_snipes")

    def add_snipe(self, *a, **kw):
        return self._call("add_snipe", *a, **kw)

    def update_snipe(self, *a, **kw):
        return self._call("update_snipe", *a, **kw)

    def delete_snipe(self, *a, **kw):
        return self._call("delete_snipe", *a, **kw)

    def purge_completed(self):
        return self._call("purge_completed")


def _patch_client(monkeypatch, **behaviors):
    fake = FakeClient(**behaviors)
    monkeypatch.setattr("gixenpy.cli._client_from_env", lambda: fake)
    return fake


def test_list_splits_active_and_ended(monkeypatch):
    snipes = [
        Snipe(item_id="1", max_bid="10.00", status="active"),
        Snipe(item_id="2", max_bid="20.00", status="ended"),
    ]
    _patch_client(monkeypatch, list_snipes=snipes)
    result = CliRunner().invoke(main, ["list"])
    assert result.exit_code == 0
    assert "Active (1)" in result.output
    assert "Ended (1)" in result.output
    assert "1" in result.output and "2" in result.output


def test_list_no_snipes(monkeypatch):
    _patch_client(monkeypatch, list_snipes=[])
    result = CliRunner().invoke(main, ["list"])
    assert result.exit_code == 0
    assert "Active (0)" in result.output
    assert "(none)" in result.output


def test_list_propagates_error(monkeypatch):
    _patch_client(monkeypatch, list_snipes=GixenError("invalid credentials"))
    result = CliRunner().invoke(main, ["list"])
    assert result.exit_code == 1
    assert "ERROR" in result.output
    assert "invalid credentials" in result.output


def test_add_dry_run_shows_payload(monkeypatch):
    res = SnipeResult(ok=True, dry_run=True, item_number="123", payload={"a": "b"}, action="x",
                      message="[dry-run] Would send to x")
    _patch_client(monkeypatch, add_snipe=res)
    result = CliRunner().invoke(main, ["add", "123", "42.5", "--dry-run"])
    assert result.exit_code == 0
    assert "payload:" in result.output


def test_add_ok(monkeypatch):
    res = SnipeResult(ok=True, item_number="123", message="Snipe scheduled on Gixen.")
    _patch_client(monkeypatch, add_snipe=res)
    result = CliRunner().invoke(main, ["add", "123", "42.5"])
    assert result.exit_code == 0
    assert "OK:" in result.output


def test_add_fails(monkeypatch):
    res = SnipeResult(ok=False, item_number="123", message="Gixen didn't schedule it.")
    _patch_client(monkeypatch, add_snipe=res)
    result = CliRunner().invoke(main, ["add", "123", "42.5"])
    assert result.exit_code == 1
    assert "ERROR:" in result.output


def test_add_offset_group_passed_to_client(monkeypatch):
    captured = {}

    def fake_add(item, max_bid, offset=None, group=None, dry_run=None):
        captured.update(item=item, max_bid=max_bid, offset=offset, group=group, dry_run=dry_run)
        return SnipeResult(ok=True, item_number=item, message="ok")

    _patch_client(monkeypatch, add_snipe=fake_add)
    result = CliRunner().invoke(main, ["add", "123", "42.5", "--offset", "9", "--group", "3"])
    assert result.exit_code == 0
    assert captured == {"item": "123", "max_bid": 42.5, "offset": 9, "group": 3, "dry_run": False}


def test_edit_ok(monkeypatch):
    res = SnipeResult(ok=True, item_number="123", message="Bid updated to 80.00.")
    _patch_client(monkeypatch, update_snipe=res)
    result = CliRunner().invoke(main, ["edit", "123", "80"])
    assert result.exit_code == 0
    assert "OK:" in result.output


def test_edit_with_no_field_propagates_error(monkeypatch):
    _patch_client(monkeypatch, update_snipe=GixenError("Provide at least one of: max bid, offset or group."))
    result = CliRunner().invoke(main, ["edit", "123"])
    assert result.exit_code == 1
    assert "at least one" in result.output


def test_edit_offset_only_does_not_require_max_bid(monkeypatch):
    captured = {}

    def fake_update(item, new_max=None, offset=None, group=None):
        captured.update(item=item, new_max=new_max, offset=offset, group=group)
        return SnipeResult(ok=True, item_number=item, message="ok")

    _patch_client(monkeypatch, update_snipe=fake_update)
    result = CliRunner().invoke(main, ["edit", "123", "--offset", "9"])
    assert result.exit_code == 0
    assert captured == {"item": "123", "new_max": None, "offset": 9, "group": None}


def test_remove_ok(monkeypatch):
    res = SnipeResult(ok=True, item_number="123", message="Snipe for item 123 deleted.")
    _patch_client(monkeypatch, delete_snipe=res)
    result = CliRunner().invoke(main, ["remove", "123"])
    assert result.exit_code == 0
    assert "OK:" in result.output


def test_purge_ok(monkeypatch):
    res = SnipeResult(ok=True, message="Ended snipes purged from the list.")
    _patch_client(monkeypatch, purge_completed=res)
    result = CliRunner().invoke(main, ["purge"])
    assert result.exit_code == 0
    assert "OK:" in result.output


def test_purge_fails(monkeypatch):
    res = SnipeResult(ok=False, message="Couldn't confirm the purge.")
    _patch_client(monkeypatch, purge_completed=res)
    result = CliRunner().invoke(main, ["purge"])
    assert result.exit_code == 1


def test_group_bulk_reports_mixed_results(monkeypatch):
    def fake_update(item, group=None):
        if item == "bad":
            raise GixenError("not found")
        return SnipeResult(ok=True, item_number=item, message=f"group {group} applied")

    _patch_client(monkeypatch, update_snipe=fake_update)
    result = CliRunner().invoke(main, ["group", "3", "good", "bad"])
    assert result.exit_code == 1
    assert "OK: good" in result.output
    assert "ERROR: bad" in result.output


def test_version():
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "gixenpy" in result.output


def test_help_lists_commands():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    for cmd in ["list", "add", "edit", "remove", "purge", "group"]:
        assert cmd in result.output
