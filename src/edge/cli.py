"""Explicit research metadata lookup and local simulation commands."""

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path

from edge.domain import SimulationConfig
from edge.errors import EdgeError
from edge.fixtures import demo_dataset
from edge.run_store import RunStore
from edge.simulation_service import engine_version, run
from edge.validation import load_dataset


def _iso_date(value: str) -> date:
    """Accept calendar dates only, not ISO week dates or compact spellings."""
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() == value:
            return parsed
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("must be an ISO calendar date (YYYY-MM-DD)")


def _json_default(value: object) -> str:
    """Encode only explicitly supported values, without float precision loss."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite decimals are not valid JSON values.")
        return format(value, "f")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("JSON timestamps must be timezone-aware.")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON value type: {type(value).__name__}")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="edge",
        description="EDGE local research foundation. Simulation only; no broker or real-money route.",
    )
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check the actual Python and pinned native engine import.")
    simulate = commands.add_parser("simulate", help="Run the explicitly synthetic Nautilus acceptance fixture.")
    simulate.add_argument("--scenario", choices=["round-trip", "risk-rejection"], default="round-trip")
    simulate.add_argument("--dataset", type=Path, help="Strict synthetic quote-dataset JSON; real market data is not accepted yet.")
    for sub in (simulate, commands.add_parser("runs", help="List persisted runs.")):
        sub.add_argument("--output-dir", type=Path, default=Path("data"))
    show = commands.add_parser("show", help="Inspect a persisted run and its evidence.")
    show.add_argument("run_id")
    show.add_argument("--output-dir", type=Path, default=Path("data"))
    filings = commands.add_parser(
        "sec-filings",
        help="Explicit opt-in SEC filing metadata lookup; prints JSON without persisting it.",
        description=(
            "Fetch delayed SEC filing metadata, not prices or financial statements. "
            "Requires the optional sec extra and EDGE_SEC_USER_AGENT; no cache or output files."
        ),
    )
    filings.add_argument("ticker", metavar="TICKER")
    filings.add_argument("--start-date", type=_iso_date, metavar="YYYY-MM-DD", help="Inclusive filing-date lower bound.")
    filings.add_argument("--end-date", type=_iso_date, metavar="YYYY-MM-DD", help="Inclusive filing-date upper bound.")
    filings.add_argument("--form", action="append", dest="form_types", help="Exact allowlisted form; repeat to select multiple forms.")
    backup = commands.add_parser(
        "journal-backup", help="Snapshot only the local journal; no provider access."
    )
    backup.add_argument("destination", type=Path, help="NEW backup ZIP file; parent directory must exist.")
    backup.add_argument("--journal-dir", type=Path, default=Path("data"), help="Source journal directory (default: data).")
    restore = commands.add_parser(
        "journal-restore", help="Restore a backup into a NEW or EMPTY journal directory only."
    )
    restore.add_argument("backup", type=Path, help="Journal backup ZIP file.")
    restore.add_argument("--journal-dir", type=Path, required=True, help="NEW or EMPTY destination directory; parent must exist. Never replaces or merges a journal.")
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            installed = engine_version()
            from nautilus_trader.backtest import BacktestEngine

            result: object = {
                "status": "ready", "python": sys.version, "interpreter": sys.executable,
                "edge_version": version("edge-research"), "engine_version": installed,
                "engine_class": f"{BacktestEngine.__module__}.{BacktestEngine.__name__}",
                "mode": "simulation-only",
                "acceptance": "Import verified; run fixture tests to verify engine behavior.",
            }
        elif args.command == "simulate":
            dataset = load_dataset(args.dataset) if args.dataset else demo_dataset()
            result = run(dataset, SimulationConfig(scenario=args.scenario), args.output_dir)
        elif args.command == "runs":
            result = RunStore(args.output_dir).list_runs()
        elif args.command in ("journal-backup", "journal-restore"):
            from edge.journal_backup import backup_journal, restore_journal

            if args.command == "journal-backup":
                result = backup_journal(args.journal_dir, args.destination)
            else:
                result = restore_journal(args.backup, args.journal_dir)
        elif args.command == "sec-filings":
            # Keep even the lightweight SEC API out of import/help/other commands.
            from edge.data import lookup_sec_filings

            result = asdict(lookup_sec_filings(
                args.ticker, start_date=args.start_date, end_date=args.end_date,
                form_types=args.form_types,
            ))
        else:
            result = RunStore(args.output_dir).get(args.run_id)
        print(json.dumps(result, indent=2, allow_nan=False, default=_json_default))
        return 0
    except (EdgeError, OSError, sqlite3.Error, ImportError) as error:
        message = str(error)
        if args.command in ("journal-backup", "journal-restore"):
            # No exception, path, note content or low-level SQL is printed.
            message = (
                "Journal recovery operation failed. Check the source, supported "
                "backup format, permissions and absent destination; no existing "
                "journal or backup was replaced."
            )
        if args.command == "sec-filings":
            from edge.data.sec_filings import USER_AGENT_ENV_VAR, redact

            secret = os.environ.get(USER_AGENT_ENV_VAR)
            # The API strips configuration whitespace; handle both representations.
            message = redact(redact(message, secret.strip() if secret else None), secret)
        print(f"EDGE error: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
