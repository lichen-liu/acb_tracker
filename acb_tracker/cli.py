#!/usr/bin/env python3
"""
Interactive ACB database tracker.

Database path pattern:
    private_db/<user>/<stock_symbol_name>_db.csv
"""

import argparse
import glob
import os
import readline
import shlex
import sys
import time
from pathlib import Path
from typing import Optional

from .backend import event_identity, load_events, q_money, q_rate, recompute_state, save_events
from .calculate_acb import fetch_boc_rate, print_table
from .etrade import parse_etrade_holdings_csv


COMMANDS = ["merge", "view", "save", "exit", "quit", "help"]
BOC_FX_SOURCE = "Bank of Canada VALET API FXUSDCAD"


def configure_readline() -> None:
    doc = getattr(readline, "__doc__", "") or ""
    if "libedit" in doc:
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")
        readline.parse_and_bind("set editing-mode emacs")
    readline.set_completer(completer)


def quote_completion(path: str) -> str:
    if any(ch.isspace() for ch in path):
        return shlex.quote(path)
    return path


def complete_path(prefix: str) -> list[str]:
    expanded = os.path.expanduser(prefix)
    matches = sorted(glob.glob(expanded + "*"))
    completions: list[str] = []

    for match in matches:
        suffix = "/" if os.path.isdir(match) else " "
        display = match
        if prefix.startswith("~"):
            home = str(Path.home())
            if display.startswith(home):
                display = "~" + display[len(home):]
        completions.append(quote_completion(display) + suffix)

    return completions


def completer(text: str, state: int) -> Optional[str]:
    buffer = readline.get_line_buffer()
    line = buffer.lstrip()
    begin = readline.get_begidx()
    tokens = line.split()

    if begin == 0:
        options = [command + " " for command in COMMANDS if command.startswith(text)]
    elif tokens and tokens[0] == "merge":
        options = complete_path(text)
    else:
        options = []

    if state < len(options):
        return options[state]
    return None


def db_path_for(user: str, stock_symbol_name: str) -> Path:
    return Path("private_db") / user / f"{stock_symbol_name}_db.csv"


def expand_single_path(raw_path: str) -> Path:
    expanded = Path(raw_path).expanduser()
    matches = glob.glob(str(expanded))
    if matches:
        if len(matches) > 1:
            raise ValueError("Path matched multiple files. Narrow the pattern.")
        return Path(matches[0])
    return expanded


def confirm(prompt: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        answer = input(f"{prompt} {suffix} ").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Enter 'y' or 'n'.")


def print_progress(current: int, total: int, prefix: str) -> None:
    total = max(total, 1)
    width = 30
    filled = int(width * current / total)
    bar = "#" * filled + "-" * (width - filled)
    print(f"\r{prefix}: [{bar}] {current}/{total}", end="", flush=True)
    if current >= total:
        print()


def shorten_source(source: str) -> str:
    if source == "etrade_holdings_sellable_expanded":
        return "etrade"
    return source


def format_fmv(row: dict) -> str:
    return f"${row['price_per_share']:,.4f} {row['currency']} × {row['fx_rate']:,.4f}"


def format_acb_delta(row: dict) -> str:
    label = "acq" if row["event_type"] == "buy" else "rel"
    return f"{label} ${row['acb_delta_total']:,.2f} / ${row['acb_delta_per_share']:,.4f}"


def build_reference(row: dict) -> str:
    reference_fields = [
        ("grant_number", row.get("grant_number", "")),
        ("vest_date", row.get("vest_date", "")),
        ("release_date", row.get("release_date", "")),
        ("grant_date", row.get("grant_date", "")),
    ]
    parts = []
    for key, value in reference_fields:
        if value:
            parts.append(f"{key}={value}")
    return ",".join(parts)


def display_rows(events: list[dict], title: str, upto_year: Optional[int] = None) -> None:
    print(f"\n{title}")
    if upto_year is not None:
        print(f"  [filtered: ACB calculations up to and including {upto_year}]")
    if not events:
        print("  (empty)")
        return

    try:
        state = recompute_state(events, upto_year=upto_year)
    except ValueError as exc:
        print(f"  state recomputation failed: {exc}")
        return

    if not state.display_rows:
        print("  (empty)")
        return

    columns = [
        ("Type", 4, "l"),
        ("Date", 10, "l"),
        ("Source", 12, "l"),
        ("Plan", 4, "l"),
        ("Shares", 12, "r"),
        ("FMV", 27, "l"),
        ("ACB Delta", 24, "l"),
        ("Cum ACB", 14, "r"),
        ("ACB/Shr", 11, "r"),
        ("Cum Shares", 12, "r"),
        ("Gain/Loss", 12, "r"),
    ]
    table_rows = [{
        "Type": row["event_type"],
        "Date": row["date"],
        "Source": shorten_source(row.get("source", "")),
        "Plan": row.get("plan", ""),
        "Shares": f"{row['num_shares']:,.4f}",
        "FMV": format_fmv(row),
        "ACB Delta": format_acb_delta(row),
        "Cum ACB": f"${row['cum_acb_total']:,.2f}",
        "ACB/Shr": f"${row['cum_acb_per_share']:,.4f}",
        "Cum Shares": f"{row['cum_shares']:,.4f}",
        "Gain/Loss": f"${row['capital_gain']:,.2f}" if row["event_type"] == "sell" else "",
    } for row in state.display_rows]
    print_table(table_rows, columns)
    display_summary(state)


def display_summary(state) -> None:
    print("\nCurrent totals")
    summary_rows = [{
        "Metric": "Open shares",
        "Value": f"{state.cum_shares:,.4f}",
    }, {
        "Metric": "Open ACB (CAD)",
        "Value": f"${state.cum_acb:,.2f}",
    }, {
        "Metric": "Open ACB/share",
        "Value": f"${q_rate(state.cum_acb / state.cum_shares):,.4f}" if state.cum_shares else "$0.0000",
    }]
    print_table(summary_rows, [("Metric", 18, "l"), ("Value", 16, "r")])

    print("\nCapital gains by year")
    if not state.capital_gain_by_year:
        print("  (none)")
        return
    gain_rows = [{
        "Year": str(year),
        "Capital Gain": f"${amount:,.2f}",
    } for year, amount in sorted(state.capital_gain_by_year.items())]
    print_table(gain_rows, [("Year", 6, "r"), ("Capital Gain", 14, "r")])


def calculate_etrade_buy_events(input_path: Path) -> tuple[list[dict], list[str]]:
    rows, skipped, _, _ = parse_etrade_holdings_csv(str(input_path))
    events: list[dict] = []
    total_rows = len(rows)

    if total_rows:
        print_progress(0, total_rows, "Calculating merge rows")

    for index, row in enumerate(rows, start=1):
        try:
            fx_rate, fx_date = fetch_boc_rate(row["date_acquired"])
            time.sleep(0.15)
            total_usd = row["cost_shr_usd"] * row["qty"]
            total_cad = q_money(total_usd * fx_rate)
            events.append({
                "event_type": "buy",
                "date": row["date_acquired"],
                "num_shares": row["qty"],
                "currency": "USD",
                "price_per_share": row["cost_shr_usd"],
                "fx_rate": fx_rate,
                "fx_source": BOC_FX_SOURCE,
                "acb_amount_cad": total_cad,
                "fx_date": fx_date,
                "source": row["source"],
                "plan": row["plan"],
                "symbol": row["symbol"],
                "reference": build_reference(row),
            })
        except RuntimeError as exc:
            skipped.append(f"Row {row['row']}: {exc}")
        finally:
            print_progress(index, total_rows, "Calculating merge rows")

    return events, skipped


def pending_additions(committed_events: list[dict], working_events: list[dict]) -> list[dict]:
    committed_keys = {event_identity(event) for event in committed_events}
    return [event for event in working_events if event_identity(event) not in committed_keys]


def has_pending_changes(committed_events: list[dict], working_events: list[dict]) -> bool:
    if len(committed_events) != len(working_events):
        return True
    return any(
        event_identity(committed) != event_identity(working)
        for committed, working in zip(committed_events, working_events)
    )


def show_pending_diff(committed_events: list[dict], working_events: list[dict], db_path: Path) -> list[dict]:
    additions = pending_additions(committed_events, working_events)
    print(f"\nPending changes for {db_path}")
    if not additions:
        print("  (none)")
        return additions

    display_rows(additions, "Pending additions")
    display_rows(committed_events + additions, "Totals after save")
    return additions


def merge_into_working(working_events: list[dict], import_path: Path) -> tuple[list[dict], list[str]]:
    imported_events, skipped = calculate_etrade_buy_events(import_path)
    existing_keys = {event_identity(event) for event in working_events}
    new_events = [event for event in imported_events if event_identity(event) not in existing_keys]
    return new_events, skipped


def print_help() -> None:
    print(
        "\nCommands:\n"
        "  merge <etrade_csv_path>   Load E*TRADE holdings CSV and stage new buy events in memory\n"
        "                           Source: etrade - holding - by status - sellable - download expanded\n"
        "  view [upto_year]          Recompute and display the current database, optionally through year-end\n"
        "  save                      Flush the in-memory database to disk immediately\n"
        "  exit | quit               Leave the CLI and optionally save pending events\n"
        "  help                      Show this help\n"
    )


def cli_loop(db_path: Path, committed_events: list[dict]) -> int:
    working_events = list(committed_events)

    display_rows(working_events, f"Database: {db_path}")
    print_help()

    while True:
        try:
            line = input("acb_tracker> ").strip()
        except EOFError:
            line = "exit"
            print()
        except KeyboardInterrupt:
            print()
            continue

        if not line:
            continue

        try:
            parts = shlex.split(line)
        except ValueError as exc:
            print(f"Could not parse command: {exc}")
            continue

        command = parts[0].lower()

        if command in {"exit", "quit"}:
            if has_pending_changes(committed_events, working_events):
                additions = show_pending_diff(committed_events, working_events, db_path)
                if additions and confirm(f"Save pending changes to {db_path}?", default=False):
                    save_events(db_path, working_events)
                    print(f"Saved {len(working_events)} events to {db_path}")
                elif additions:
                    print("Pending changes discarded.")
            return 0

        if command == "help":
            print_help()
            continue

        if command == "save":
            if len(parts) != 1:
                print("Usage: save")
                continue
            if not has_pending_changes(committed_events, working_events):
                print(f"No pending changes to save for {db_path}")
                continue
            save_events(db_path, working_events)
            committed_events = list(working_events)
            print(f"Saved {len(working_events)} events to {db_path}")
            continue

        if command == "view":
            upto_year = None
            if len(parts) > 2:
                print("Usage: view [upto_year]")
                continue
            if len(parts) == 2:
                try:
                    upto_year = int(parts[1])
                except ValueError:
                    print("Usage: view [upto_year]")
                    continue
            display_rows(working_events, f"Database: {db_path}", upto_year=upto_year)
            continue

        if command == "merge":
            if len(parts) != 2:
                print("Usage: merge <etrade_csv_path>")
                continue

            try:
                import_path = expand_single_path(parts[1])
            except ValueError as exc:
                print(str(exc))
                continue

            if not import_path.exists():
                print(f"CSV not found: {import_path}")
                continue

            if not import_path.is_file():
                print(f"CSV path is not a file: {import_path}")
                continue

            print(f"\nLoading merge candidate from {import_path}")
            try:
                new_events, skipped = merge_into_working(working_events, import_path)
            except ValueError as exc:
                print(str(exc))
                continue
            except RuntimeError as exc:
                print(str(exc))
                continue

            if skipped and not new_events:
                print(f"Skipped {len(skipped)} row(s) during import:")
                for message in skipped:
                    print(f"  - {message}")

            if not new_events:
                print("No new rows found.")
                continue

            display_rows(new_events, "Merge candidate: new events")
            display_rows(working_events + new_events, "Totals after merge")

            if skipped:
                print(f"\nSkipped {len(skipped)} row(s) during import:")
                for message in skipped:
                    print(f"  - {message}")

            if confirm("Stage these events in the in-memory database?", default=False):
                working_events.extend(new_events)
                print(f"Staged {len(new_events)} new event(s). Not yet written to {db_path}.")
            else:
                print("Merge cancelled.")
            continue

        print("Unknown command. Type 'help' for available commands.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactive ACB database tracker")
    parser.add_argument("mode", choices=["new", "load"], help="Open a new or existing database")
    parser.add_argument("user", help="User name for database path")
    parser.add_argument("stock_symbol_name", help="Stock symbol or tracker name")
    return parser.parse_args()


def main() -> int:
    configure_readline()
    args = parse_args()
    db_path = db_path_for(args.user, args.stock_symbol_name)

    if args.mode == "new":
        if db_path.exists():
            print(f"Database already exists: {db_path}")
            print(f"Use: python run_acb_tracker.py load {args.user} {args.stock_symbol_name}")
            return 1
        return cli_loop(db_path, [])

    if not db_path.exists():
        print(f"Database does not exist: {db_path}")
        print(f"Use: python run_acb_tracker.py new {args.user} {args.stock_symbol_name}")
        return 1

    try:
        committed_events = load_events(db_path)
    except Exception as exc:
        print(f"Failed to load database: {exc}")
        return 1

    return cli_loop(db_path, committed_events)


if __name__ == "__main__":
    sys.exit(main())
