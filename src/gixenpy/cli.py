"""gixenpy CLI: manage Gixen snipes from the terminal.

Credentials via the GIXEN_USERNAME / GIXEN_PASSWORD environment variables.
If a `.env` file exists in the current directory, it's loaded before reading
them (see `_load_dotenv`); a variable already exported in the environment
takes priority over the `.env` file.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from . import __version__
from .client import GixenClient, GixenError, Snipe


def _load_dotenv(path: str = ".env") -> None:
    """
    Loads `KEY=value` variables from a simple `.env` file, one per line
    (leading '#' = comment). Doesn't add python-dotenv as a dependency since
    only two variables are needed; doesn't override ones already in the
    environment, so `export GIXEN_USERNAME=...` still wins over the `.env`.
    """
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _client_from_env() -> GixenClient:
    _load_dotenv()
    return GixenClient(
        username=os.environ.get("GIXEN_USERNAME", ""),
        password=os.environ.get("GIXEN_PASSWORD", ""),
        dry_run=False,
    )


def _fail(message: str) -> None:
    click.secho(f"ERROR: {message}", fg="red", err=True)
    sys.exit(1)


def _ok(message: str) -> None:
    click.secho(f"OK: {message}", fg="green")


@click.group()
@click.version_option(__version__, prog_name="gixenpy")
def main() -> None:
    """Manage Gixen.com (eBay sniping) snipes from the terminal.

    Reads credentials from GIXEN_USERNAME / GIXEN_PASSWORD (environment
    variables or a .env file in the current directory).
    """


def _snipe_row(s: Snipe) -> str:
    offset = s.offset or "-"
    group = s.group or "-"
    return f"{s.item_id:<14} {s.max_bid:>10}  offset={offset:<4} group={group:<3}  {s.ebay_url}"


def _print_section(title: str, snipes: list[Snipe]) -> None:
    click.secho(f"\n{title} ({len(snipes)})", bold=True)
    if not snipes:
        click.echo("  (none)")
        return
    for s in snipes:
        click.echo("  " + _snipe_row(s))


@main.command("list")
def list_cmd() -> None:
    """List the account's snipes, split into active/won/lost/ended."""
    client = _client_from_env()
    try:
        snipes = client.list_snipes()
    except GixenError as e:
        _fail(str(e))
        return
    active = [s for s in snipes if s.status == "active"]
    won = [s for s in snipes if s.status == "won"]
    lost = [s for s in snipes if s.status == "lost"]
    ended = [s for s in snipes if s.status == "ended"]
    unknown = [s for s in snipes if s.status == "unknown"]
    _print_section("Active", active)
    _print_section("Won", won)
    _print_section("Lost", lost)
    if ended:
        _print_section("Ended", ended)
    if unknown:
        _print_section("Unknown status", unknown)


@main.command("add")
@click.argument("item")
@click.argument("max_bid", type=float)
@click.option("--offset", type=int, default=None, help="Seconds before the close.")
@click.option("--group", type=int, default=None, help="Bid group (0 = no group).")
@click.option("--dry-run", is_flag=True, default=False,
              help="Don't schedule anything: show what would be sent.")
def add_cmd(item: str, max_bid: float, offset: int | None, group: int | None,
            dry_run: bool) -> None:
    """Schedule a new snipe for ITEM with max bid MAX_BID."""
    client = _client_from_env()
    try:
        res = client.add_snipe(item, max_bid, offset=offset, group=group, dry_run=dry_run)
    except GixenError as e:
        _fail(str(e))
        return
    if dry_run:
        click.echo(res.message)
        click.echo(f"payload: {res.payload}")
        return
    if res.ok:
        _ok(res.message)
    else:
        _fail(res.message)


@main.command("edit")
@click.argument("item")
@click.argument("max_bid", type=float, required=False, default=None)
@click.option("--offset", type=int, default=None, help="Seconds before the close.")
@click.option("--group", type=int, default=None, help="Bid group (0 = no group).")
def edit_cmd(item: str, max_bid: float | None, offset: int | None, group: int | None) -> None:
    """Change the bid/offset/group of an already-scheduled snipe for ITEM.

    MAX_BID is optional: you can change only --offset and/or --group
    without touching it.
    """
    client = _client_from_env()
    try:
        res = client.update_snipe(item, new_max=max_bid, offset=offset, group=group)
    except GixenError as e:
        _fail(str(e))
        return
    if res.ok:
        _ok(res.message)
    else:
        _fail(res.message)


@main.command("remove")
@click.argument("item")
def remove_cmd(item: str) -> None:
    """Delete the scheduled snipe for ITEM."""
    client = _client_from_env()
    try:
        res = client.delete_snipe(item)
    except GixenError as e:
        _fail(str(e))
        return
    if res.ok:
        _ok(res.message)
    else:
        _fail(res.message)


@main.command("purge")
def purge_cmd() -> None:
    """Purge already-ended snipes from the list (doesn't affect active ones)."""
    client = _client_from_env()
    try:
        res = client.purge_completed()
    except GixenError as e:
        _fail(str(e))
        return
    if res.ok:
        _ok(res.message)
    else:
        _fail(res.message)


@main.command("group")
@click.argument("group_id", type=int)
@click.argument("items", nargs=-1, required=True)
def group_cmd(group_id: int, items: tuple[str, ...]) -> None:
    """Assign group GROUP_ID to one or more ITEMS."""
    client = _client_from_env()
    failures = 0
    for item in items:
        try:
            res = client.update_snipe(item, group=group_id)
        except GixenError as e:
            click.secho(f"ERROR: {item}: {e}", fg="red", err=True)
            failures += 1
            continue
        if res.ok:
            click.secho(f"OK: {item}: {res.message}", fg="green")
        else:
            click.secho(f"ERROR: {item}: {res.message}", fg="red", err=True)
            failures += 1
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
