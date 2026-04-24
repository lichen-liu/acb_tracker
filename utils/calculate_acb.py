#!/usr/bin/env python3
"""
ACB (Adjusted Cost Base) Calculator — RSU & ESPP
================================================
Input CSV:
    E*TRADE holdings export parsed by utils/etrade_holdings_csv.py
Cost basis source:
    Total USD cost = Est. Cost Basis (per share): × Sellable Qty.
FX conversion:
    USD → CAD via Bank of Canada VALET API (FXUSDCAD), keyed on Date Acquired.
    If Date Acquired falls on a weekend/holiday, the nearest prior business day
    is used automatically.

Usage:
    python calculate_acb.py --input trades.csv
    python calculate_acb.py --input trades.csv --output acb_results.csv
    python calculate_acb.py --input trades.csv --upto 2024        # excludes 2025+
"""

import argparse
import csv
import json
from pathlib import Path
import sys
import time
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional
from urllib.error import URLError
from urllib.request import urlopen

try:
    from .etrade_holdings_csv import parse_etrade_holdings_csv
except ImportError:
    from etrade_holdings_csv import parse_etrade_holdings_csv

# ── Bank of Canada VALET API ──────────────────────────────────────────────────
BOC_VALET = "https://www.bankofcanada.ca/valet/observations/FXUSDCAD/json"
_fx_cache: dict = {}


def fetch_boc_rate(date_str: str) -> tuple[Decimal, str]:
    """
    Return (rate, actual_date) for the given YYYY-MM-DD.
    Walks back up to 7 calendar days to find the nearest published rate.
    """
    if date_str in _fx_cache:
        return _fx_cache[date_str]

    target = datetime.strptime(date_str, "%Y-%m-%d")
    start  = (target - timedelta(days=7)).strftime("%Y-%m-%d")

    url = f"{BOC_VALET}?start_date={start}&end_date={date_str}"
    try:
        with urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except URLError as exc:
        raise RuntimeError(f"Bank of Canada API unreachable: {exc}") from exc

    observations = data.get("observations", [])
    if not observations:
        raise ValueError(
            f"No FXUSDCAD data from Bank of Canada for {start} → {date_str}. "
            "Verify the date is not in the future or before 2017."
        )

    latest      = observations[-1]
    actual_date = latest["d"]
    rate        = Decimal(str(latest["FXUSDCAD"]["v"]))
    result      = (rate, actual_date)
    _fx_cache[date_str]    = result
    _fx_cache[actual_date] = result
    return result


# ── Table printer ─────────────────────────────────────────────────────────────

def print_table(rows: list[dict], columns: list[tuple[str, int, str]]) -> None:
    """columns: list of (header, width, align)  where align = 'l' or 'r'"""
    sep = "+-" + "-+-".join("-" * w for _, w, _ in columns) + "-+"

    def fmt(val: str, width: int, align: str) -> str:
        s = str(val)
        if len(s) > width:
            s = s[:width - 1] + "…"
        return s.ljust(width) if align == "l" else s.rjust(width)

    print(sep)
    print("| " + " | ".join(fmt(h, w, a) for h, w, a in columns) + " |")
    print(sep)
    for row in rows:
        print("| " + " | ".join(fmt(row.get(h, ""), w, a) for h, w, a in columns) + " |")
    print(sep)


def print_acb_report(
    lots: list[dict],
    skipped: list[str],
    total_rows: int,
    filtered_out: int,
    upto_year: Optional[int] = None,
    heading: str = "LOT-BY-LOT DETAIL",
) -> None:
    if not lots and not skipped:
        print("\nNo RSU or ESPP rows found. Ensure 'Plan Type' column contains 'RSU' or 'ESPP'.")
        return

    # ── Lot-by-lot detail table ───────────────────────────────────────────────
    DETAIL_COLS: list[tuple[str, int, str]] = [
        ("Row",            4,  "r"),
        ("Type",           4,  "l"),
        ("Symbol",         6,  "l"),
        ("Date Acquired",  14, "l"),
        ("FX Date",        10, "l"),
        ("Sellable Qty",   12, "r"),
        ("Cost/Shr USD",   13, "r"),
        ("= Total USD",    12, "r"),
        ("× FX USD→CAD",   13, "r"),
        ("= Total CAD",    12, "r"),
        ("ACB/Shr CAD",    13, "r"),
    ]

    detail_rows = [{
        "Row":           str(l["row"]),
        "Type":          l["plan"],
        "Symbol":        l["symbol"],
        "Date Acquired": l["date_acquired"],
        "FX Date":       l["fx_date"],
        "Sellable Qty":  f"{l['qty']:,.4f}",
        "Cost/Shr USD":  f"${l['cost_shr_usd']:,.4f}",
        "= Total USD":   f"${l['total_usd']:,.2f}",
        "× FX USD→CAD":  f"{l['fx_rate']:,.4f}",
        "= Total CAD":   f"${l['total_cad']:,.2f}",
        "ACB/Shr CAD":   f"${l['acb_shr_cad']:,.4f}",
    } for l in lots]

    print("\n" + "=" * 120)
    print(f"  {heading}  (formula per row:  Sellable Qty × Cost/Shr USD × FX Rate = Total CAD)"
          + (f"  [filtered: up to and including {upto_year}]" if upto_year else ""))
    print("=" * 120)
    print_table(detail_rows, DETAIL_COLS)

    # ── Summary by plan type ──────────────────────────────────────────────────
    def subtotals(plan: str):
        sub = [l for l in lots if l["plan"] == plan]
        return len(sub), sum(l["total_usd"] for l in sub), sum(l["total_cad"] for l in sub)

    rsu_n,  rsu_usd,  rsu_cad  = subtotals("RSU")
    espp_n, espp_usd, espp_cad = subtotals("ESPP")
    tot_n   = rsu_n  + espp_n
    tot_usd = rsu_usd  + espp_usd
    tot_cad = rsu_cad  + espp_cad

    SUM_COLS: list[tuple[str, int, str]] = [
        ("Plan Type",  10, "l"),
        ("Lots",        5, "r"),
        ("Total USD",  16, "r"),
        ("Total CAD",  16, "r"),
    ]
    summary_rows = []
    if rsu_n:
        summary_rows.append({"Plan Type": "RSU",  "Lots": str(rsu_n),  "Total USD": f"${rsu_usd:,.2f}",  "Total CAD": f"${rsu_cad:,.2f}"})
    if espp_n:
        summary_rows.append({"Plan Type": "ESPP", "Lots": str(espp_n), "Total USD": f"${espp_usd:,.2f}", "Total CAD": f"${espp_cad:,.2f}"})
    summary_rows.append(  {"Plan Type": "TOTAL", "Lots": str(tot_n),  "Total USD": f"${tot_usd:,.2f}",  "Total CAD": f"${tot_cad:,.2f}"})

    print("\n  SUMMARY BY PLAN TYPE")
    print_table(summary_rows, SUM_COLS)

    # ── Row ingestion summary ─────────────────────────────────────────────────
    used_rows = len(lots)
    error_rows = len(skipped)
    print("\n  ROW INGESTION SUMMARY")
    ING_COLS = [
        ("",                    28, "l"),
        ("Count",                7, "r"),
    ]
    ing_rows = [
        {"": "Total recognised rows in CSV",   "Count": str(total_rows)},
        {"": "  Excluded by --upto filter",    "Count": str(filtered_out)},
        {"": "  Skipped (parse errors)",       "Count": str(error_rows)},
        {"": "  Used in ACB calculation",      "Count": str(used_rows)},
    ]
    print_table(ing_rows, ING_COLS)

    # ── FX rate source note ───────────────────────────────────────────────────
    unique_fx = {}
    for l in lots:
        if l["date_acquired"] not in unique_fx:
            unique_fx[l["date_acquired"]] = (l["fx_rate"], l["fx_date"])
    print("\n  FX RATES USED  (source: Bank of Canada VALET API — FXUSDCAD)")
    FX_COLS = [
        ("Date Acquired",  14, "l"),
        ("BoC Rate Date",  14, "l"),
        ("USD → CAD",      11, "r"),
        ("Note",           36, "l"),
    ]
    fx_table_rows = []
    for acq_date in sorted(unique_fx):
        rate, boc_date = unique_fx[acq_date]
        note = "(weekend/holiday — prior business day used)" if boc_date != acq_date else "exact date match"
        fx_table_rows.append({
            "Date Acquired": acq_date,
            "BoC Rate Date": boc_date,
            "USD → CAD":     f"{rate:,.4f}",
            "Note":          note,
        })
    print_table(fx_table_rows, FX_COLS)

    print(f"\n  ► Grand Total ACB  :  USD ${tot_usd:>13,.2f}")
    print(f"  ► Grand Total ACB  :  CAD ${tot_cad:>13,.2f}")

    if skipped:
        print(f"\n  ⚠  Skipped rows ({error_rows}):")
        for msg in skipped:
            print(f"     • {msg}")

    print()


def process(input_path: str, output_path: Optional[str], upto_year: Optional[int] = None) -> None:
    try:
        lots, skipped, total_rows, filtered_out = parse_etrade_holdings_csv(input_path, upto_year)
    except ValueError as exc:
        sys.exit(str(exc))

    enriched_lots: list[dict] = []
    for lot in lots:
        try:
            fx_rate, fx_date = fetch_boc_rate(lot["date_acquired"])
            time.sleep(0.15)  # be polite to the API

            total_usd = (lot["cost_shr_usd"] * lot["qty"]).quantize(Decimal("0.01"), ROUND_HALF_UP)
            total_cad = (total_usd * fx_rate).quantize(Decimal("0.01"), ROUND_HALF_UP)
            acb_shr_cad = (total_cad / lot["qty"]).quantize(Decimal("0.0001"), ROUND_HALF_UP)

            enriched_lots.append({
                **lot,
                "fx_date": fx_date,
                "total_usd": total_usd,
                "fx_rate": fx_rate,
                "total_cad": total_cad,
                "acb_shr_cad": acb_shr_cad,
            })
        except RuntimeError as exc:
            skipped.append(f"Row {lot['row']}: {exc}")

    lots = enriched_lots
    print_acb_report(lots, skipped, total_rows, filtered_out, upto_year=upto_year)

    # ── Optional CSV export ───────────────────────────────────────────────────
    if output_path:
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "row", "source", "plan", "symbol", "date_acquired", "fx_date",
            "qty", "cost_shr_usd", "total_usd", "fx_rate",
            "total_cad", "acb_shr_cad",
            "grant_number", "vest_date", "release_date", "grant_date", "est_mkt_value",
        ]
        with output_file.open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(lots)
            writer.writerow({})
            writer.writerow({"source": "summary", "plan": "RSU TOTAL",   "total_usd": rsu_usd,  "total_cad": rsu_cad})
            writer.writerow({"source": "summary", "plan": "ESPP TOTAL",  "total_usd": espp_usd, "total_cad": espp_cad})
            writer.writerow({"source": "summary", "plan": "GRAND TOTAL", "total_usd": tot_usd,  "total_cad": tot_cad})
        print(f"  ✓  CSV saved → {output_file}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Calculate ACB (CAD) for RSU & ESPP lots.")
    parser.add_argument(
        "--input",
        "-i",
        required=True,
        help="Input CSV path (E*TRADE Holdings > By Status > Sellable > Download Expanded export)",
    )
    parser.add_argument("--output", "-o", default=None,  help="Optional output CSV path")
    parser.add_argument("--upto",   "-y", type=int, default=None,
                        help="Include only lots with Date Acquired ≤ Dec 31 of this year "
                             "(e.g. --upto 2024 excludes 2025 and later)")
    args = parser.parse_args()
    process(args.input, args.output, args.upto)


if __name__ == "__main__":
    main()
