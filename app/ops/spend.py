"""Inspect and reset the persistent LLM spend ledger.

    python -m app.ops.spend show                # current window
    python -m app.ops.spend show --all-windows  # full retained history
    python -m app.ops.spend reset               # zero the current window
    python -m app.ops.spend reset --window 2026-07 --yes

Reads the same environment the API reads, so it always reports on whichever
database `DATABASE_URL` points at. Run it against production with the
production connection string and nothing else set.
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from decimal import Decimal

from app.db import PostgresDatabase
from app.llm.client import LIFETIME_WINDOW, MONTHLY_WINDOW, spend_budget_key
from app.runtime_config import RuntimeSettings, load_runtime_settings


def _resolve_key(settings: RuntimeSettings, window: str | None) -> str:
    """Map a CLI window argument onto a ledger key."""
    if window is None:
        return spend_budget_key(settings.spend_window.value)
    if window in {LIFETIME_WINDOW, "global"}:
        return "global"
    try:
        datetime.strptime(window, "%Y-%m")
    except ValueError:
        raise SystemExit(
            f"--window must be YYYY-MM or 'lifetime', not {window!r}"
        ) from None
    return f"global:{window}"


def _format_row(key: str, spent: Decimal, cap: Decimal, updated: object) -> str:
    ratio = float(spent / cap) if cap else 0.0
    flag = "  <-- CAP REACHED" if spent >= cap else ("  <-- 80%+" if ratio >= 0.8 else "")
    return f"  {key:<20} ${spent:>10.6f}  {ratio * 100:>6.1f}% of cap  {updated}{flag}"


async def _show(settings: RuntimeSettings, args: argparse.Namespace) -> int:
    cap = settings.hard_spend_cap_usd
    database = PostgresDatabase(settings.database_url.get_secret_value())
    await database.open()
    try:
        if args.all_windows:
            rows = await database.fetch_all(
                "SELECT budget_key, spent_usd, updated_at FROM llm_spend_totals"
                " ORDER BY budget_key DESC"
            )
        else:
            key = _resolve_key(settings, args.window)
            rows = await database.fetch_all(
                "SELECT budget_key, spent_usd, updated_at FROM llm_spend_totals"
                " WHERE budget_key = %s",
                (key,),
            )
            if not rows:
                rows = [{"budget_key": key, "spent_usd": 0, "updated_at": "never"}]
    finally:
        await database.close()

    now = datetime.now(timezone.utc)
    print(f"window mode : {settings.spend_window.value}")
    print(f"current key : {spend_budget_key(settings.spend_window.value, now=now)}")
    if settings.spend_window.value == MONTHLY_WINDOW:
        boundary = now.replace(
            year=now.year + (now.month == 12),
            month=1 if now.month == 12 else now.month + 1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        print(f"resets at   : {boundary:%Y-%m-%d %H:%M:%S} UTC")
    else:
        print("resets at   : never (lifetime window; reset manually)")
    print(f"cap         : ${cap}")
    print("")
    for row in rows:
        print(
            _format_row(
                str(row["budget_key"]),
                Decimal(str(row["spent_usd"])),
                cap,
                row["updated_at"],
            )
        )
    return 0


async def _reset(settings: RuntimeSettings, args: argparse.Namespace) -> int:
    key = _resolve_key(settings, args.window)
    database = PostgresDatabase(settings.database_url.get_secret_value())
    await database.open()
    try:
        row = await database.fetch_one(
            "SELECT spent_usd FROM llm_spend_totals WHERE budget_key = %s", (key,)
        )
        if row is None:
            print(f"no ledger row for {key!r}; nothing to reset")
            return 0
        current = Decimal(str(row["spent_usd"]))
        if not args.yes:
            print(f"About to reset {key!r} from ${current} to $0.")
            if input("Type the window key to confirm: ").strip() != key:
                print("aborted")
                return 1
        await database.execute(
            "UPDATE llm_spend_totals SET spent_usd = 0, updated_at = now()"
            " WHERE budget_key = %s",
            (key,),
        )
        print(f"reset {key!r}: ${current} -> $0")
    finally:
        await database.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run one subcommand."""
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.spend", description=__doc__.split("\n")[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="report spend for a window")
    show.add_argument("--window", help="YYYY-MM or 'lifetime'; defaults to current")
    show.add_argument(
        "--all-windows", action="store_true", help="list every retained window"
    )

    reset = sub.add_parser("reset", help="zero the spend recorded for a window")
    reset.add_argument("--window", help="YYYY-MM or 'lifetime'; defaults to current")
    reset.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    args = parser.parse_args(argv)
    settings = load_runtime_settings()
    handler = _show if args.command == "show" else _reset
    return asyncio.run(handler(settings, args))


if __name__ == "__main__":
    sys.exit(main())
