#!/usr/bin/env python3
"""
Interactive ACB database tracker.

Database path pattern:
    private_db/<user>/<stock_symbol_name>_db.csv
"""

import argparse
import csv
import glob
import os
import readline
import shlex
import sys
import time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Optional

from utils.calculate_acb import fetch_boc_rate, print_acb_report, print_table
from utils.etrade_holdings_csv import parse_etrade_holdings_csv


DB_FIELDS = [
    "row",
    "source",
    "plan",
    "symbol",
    "date_acquired",
    "fx_date",
    "qty",
    "cost_shr_usd",
    "total_usd",
    "fx_rate",
    "total_cad",
    "acb_shr_cad",
    "grant_number",
    "vest_date",
    "release_date",
    "grant_date",
    "est_mkt_value",
]

DECIMAL_FIELDS = {
    "qty",
    "cost_shr_usd",
    "total_usd",
    "fx_rate",
    "total_cad",
    "acb_shr_cad",
}

COMMANDS = ["merge", "view", "exit", "quit", "help"]


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
    pattern = expanded + "*"
    matches = sorted(glob.glob(pattern))
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


def parse_decimal(value: str) -> Decimal:
    cleaned = value.strip()
    if not cleaned:
        return Decimal("0")
    return Decimal(cleaned)


def row_identity(row: dict) -> tuple:
    return (
        row.get("source", ""),
        row.get("plan", ""),
        row.get("symbol", ""),
        row.get("date_acquired", ""),
        str(row.get("qty", "")),
        str(row.get("cost_shr_usd", "")),
        row.get("grant_number", ""),
        row.get("vest_date", ""),
        row.get("release_date", ""),
        row.get("grant_date", ""),
        row.get("est_mkt_value", ""),
    )


def filter_rows_upto_year(rows: list[dict], upto_year: Optional[int]) -> list[dict]:
    if upto_year is None:
        return list(rows)
    return [row for row in rows if row.get("date_acquired", "")[:4].isdigit() and int(row["date_acquired"][:4]) <= upto_year]


def display_rows(rows: list[dict], title: str, upto_year: Optional[int] = None) -> None:
    filtered_rows = filter_rows_upto_year(rows, upto_year)

    print(f"\n{title}")
    if upto_year is not None:
        print(f"  [filtered: ACB calculations up to and including {upto_year}]")
    if not filtered_rows:
        print("  (empty)")
        return

    columns = [
        ("Source",        12, "l"),
        ("Type",           4, "l"),
        ("Symbol",         8, "l"),
        ("Date",          10, "l"),
        ("Qty",           12, "r"),
        ("USD Total",     12, "r"),
        ("CAD Total",     12, "r"),
        ("ACB/Shr CAD",   12, "r"),
        ("Grant Number",  12, "l"),
    ]

    table_rows = [{
        "Source":      shorten_source(row.get("source", "")),
        "Type":        row.get("plan", ""),
        "Symbol":      row.get("symbol", ""),
        "Date":        row.get("date_acquired", ""),
        "Qty":         f"{row['qty']:,.4f}",
        "USD Total":   f"${row['total_usd']:,.2f}",
        "CAD Total":   f"${row['total_cad']:,.2f}",
        "ACB/Shr CAD": f"${row['acb_shr_cad']:,.4f}",
        "Grant Number": row.get("grant_number", ""),
    } for row in filtered_rows]
    print_table(table_rows, columns)
    display_summary(filtered_rows, "Current totals")


def shorten_source(source: str) -> str:
    if source == "etrade_holdings_sellable_expanded":
        return "etrade"
    return source


def display_summary(rows: list[dict], title: str) -> None:
    rsu_rows = [row for row in rows if row.get("plan") == "RSU"]
    espp_rows = [row for row in rows if row.get("plan") == "ESPP"]

    def totals(subset: list[dict]) -> tuple[int, Decimal, Decimal]:
        return (
            len(subset),
            sum((row["total_usd"] for row in subset), Decimal("0")),
            sum((row["total_cad"] for row in subset), Decimal("0")),
        )

    rsu_n, rsu_usd, rsu_cad = totals(rsu_rows)
    espp_n, espp_usd, espp_cad = totals(espp_rows)
    total_n = rsu_n + espp_n
    total_usd = rsu_usd + espp_usd
    total_cad = rsu_cad + espp_cad

    print(f"\n{title}")
    summary_rows = []
    if rsu_n:
        summary_rows.append({"Plan": "RSU", "Lots": str(rsu_n), "USD": f"${rsu_usd:,.2f}", "CAD": f"${rsu_cad:,.2f}"})
    if espp_n:
        summary_rows.append({"Plan": "ESPP", "Lots": str(espp_n), "USD": f"${espp_usd:,.2f}", "CAD": f"${espp_cad:,.2f}"})
    summary_rows.append({"Plan": "TOTAL", "Lots": str(total_n), "USD": f"${total_usd:,.2f}", "CAD": f"${total_cad:,.2f}"})
    print_table(summary_rows, [("Plan", 10, "l"), ("Lots", 5, "r"), ("USD", 14, "r"), ("CAD", 14, "r")])


def load_db_rows(db_path: Path) -> list[dict]:
    rows: list[dict] = []
    with db_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            return rows
        for raw in reader:
            if raw is None:
                continue
            row = {key: (value or "").strip() for key, value in raw.items() if key}
            if not any(row.values()):
                continue
            if row.get("source") == "summary":
                continue
            parsed = {}
            for field in DB_FIELDS:
                value = row.get(field, "")
                if field in DECIMAL_FIELDS:
                    parsed[field] = parse_decimal(value)
                elif field == "row":
                    parsed[field] = int(value) if value else 0
                else:
                    parsed[field] = value
            rows.append(parsed)
    return rows


def save_db_rows(db_path: Path, rows: list[dict]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with db_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=DB_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def calculate_etrade_rows(input_path: Path) -> tuple[list[dict], list[str]]:
    rows, skipped, _, _ = parse_etrade_holdings_csv(str(input_path))
    calculated: list[dict] = []
    total_rows = len(rows)

    if total_rows:
        print_progress(0, total_rows, "Calculating merge rows")

    for index, row in enumerate(rows, start=1):
        try:
            fx_rate, fx_date = fetch_boc_rate(row["date_acquired"])
            time.sleep(0.15)
            total_usd = (row["cost_shr_usd"] * row["qty"]).quantize(Decimal("0.01"), ROUND_HALF_UP)
            total_cad = (total_usd * fx_rate).quantize(Decimal("0.01"), ROUND_HALF_UP)
            acb_shr_cad = (total_cad / row["qty"]).quantize(Decimal("0.0001"), ROUND_HALF_UP)
            calculated.append({
                **row,
                "fx_date": fx_date,
                "total_usd": total_usd,
                "fx_rate": fx_rate,
                "total_cad": total_cad,
                "acb_shr_cad": acb_shr_cad,
            })
        except RuntimeError as exc:
            skipped.append(f"Row {row['row']}: {exc}")
        finally:
            print_progress(index, total_rows, "Calculating merge rows")

    return calculated, skipped


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


def pending_additions(committed_rows: list[dict], working_rows: list[dict]) -> list[dict]:
    committed_keys = {row_identity(row) for row in committed_rows}
    return [row for row in working_rows if row_identity(row) not in committed_keys]


def show_pending_diff(committed_rows: list[dict], working_rows: list[dict], db_path: Path) -> list[dict]:
    additions = pending_additions(committed_rows, working_rows)
    print(f"\nPending changes for {db_path}")
    if not additions:
        print("  (none)")
        return additions

    display_rows(additions, "Pending additions")
    merged_total = committed_rows + additions
    display_summary(merged_total, "Totals after save")
    return additions


def merge_into_working(working_rows: list[dict], import_path: Path) -> tuple[list[dict], list[str]]:
    imported_rows, skipped = calculate_etrade_rows(import_path)
    existing_keys = {row_identity(row) for row in working_rows}
    new_rows = [row for row in imported_rows if row_identity(row) not in existing_keys]
    return new_rows, skipped


def print_help() -> None:
    print(
        "\nCommands:\n"
        "  merge <etrade_csv_path>   Load E*TRADE holdings CSV and stage new rows in memory\n"
        "                           Source: etrade - holding - by status - sellable - download expanded\n"
        "  view [upto_year]          Display the current in-memory database, optionally through year-end\n"
        "  exit | quit               Leave the CLI and optionally save pending rows\n"
        "  help                      Show this help\n"
    )


def cli_loop(db_path: Path, committed_rows: list[dict]) -> int:
    working_rows = list(committed_rows)

    display_rows(working_rows, f"Database: {db_path}")
    print_help()

    while True:
        try:
            line = input("acb_db> ").strip()
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
            additions = show_pending_diff(committed_rows, working_rows, db_path)
            if additions and confirm(f"Save pending changes to {db_path}?", default=False):
                save_db_rows(db_path, working_rows)
                print(f"Saved {len(working_rows)} rows to {db_path}")
            elif additions:
                print("Pending changes discarded.")
            return 0

        if command == "help":
            print_help()
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
            display_rows(working_rows, f"Database: {db_path}", upto_year=upto_year)
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
                new_rows, skipped = merge_into_working(working_rows, import_path)
            except ValueError as exc:
                print(str(exc))
                continue
            except RuntimeError as exc:
                print(str(exc))
                continue

            if skipped and not new_rows:
                print(f"Skipped {len(skipped)} row(s) during import:")
                for message in skipped:
                    print(f"  - {message}")

            if not new_rows:
                print("No new rows found.")
                continue

            print_acb_report(
                new_rows,
                skipped,
                total_rows=len(new_rows),
                filtered_out=0,
                heading="MERGE CANDIDATE DETAIL",
            )
            display_rows(new_rows, "Merge candidate: new rows")
            display_summary(working_rows + new_rows, "Totals after merge")

            if confirm("Stage these rows in the in-memory database?", default=False):
                working_rows.extend(new_rows)
                print(f"Staged {len(new_rows)} new row(s). Not yet written to {db_path}.")
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
            print(f"Use: python acb_db.py load {args.user} {args.stock_symbol_name}")
            return 1
        return cli_loop(db_path, [])

    if not db_path.exists():
        print(f"Database does not exist: {db_path}")
        print(f"Use: python acb_db.py new {args.user} {args.stock_symbol_name}")
        return 1

    try:
        committed_rows = load_db_rows(db_path)
    except Exception as exc:
        print(f"Failed to load database: {exc}")
        return 1

    return cli_loop(db_path, committed_rows)


if __name__ == "__main__":
    sys.exit(main())
