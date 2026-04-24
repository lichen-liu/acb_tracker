#!/usr/bin/env python3
"""
E*TRADE holdings CSV parser for ACB inputs.

CSV source:
    etrade - holding - by status - sellable - download expanded
"""

import csv
from datetime import datetime
from decimal import Decimal
from typing import Optional


# Maps raw CSV "Plan Type" values -> canonical internal label
PLAN_TYPE_MAP = {
    "ESPP":        "ESPP",
    "REST. STOCK": "RSU",
    "RSU":         "RSU",
}


def clean(v: str) -> str:
    return v.strip().lstrip("\ufeff").replace("$", "").replace(",", "").strip()


def to_dec(value: str, field: str, row_num: int) -> Decimal:
    cleaned = clean(value)
    if not cleaned:
        raise ValueError(f"Row {row_num}: '{field}' is empty.")
    try:
        return Decimal(cleaned)
    except Exception:
        raise ValueError(f"Row {row_num}: cannot parse '{field}' = '{cleaned}' as a number.")


def parse_date(value: str, field: str, row_num: int) -> str:
    cleaned = clean(value)
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d-%b-%Y", "%B %d, %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Row {row_num}: cannot parse '{field}' date '{cleaned}'.")


def parse_etrade_holdings_csv(input_path: str, upto_year: Optional[int] = None) -> tuple[list[dict], list[str], int, int]:
    lots: list[dict] = []
    skipped: list[str] = []
    total_rows = 0
    filtered_out = 0

    with open(input_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError("ERROR: CSV is empty or has no header row.")
        reader.fieldnames = [field.strip().lstrip("\ufeff") for field in reader.fieldnames]

        for row_num, raw in enumerate(reader, start=2):
            row = {key.strip(): value.strip() for key, value in raw.items() if key}
            if not any(row.values()):
                continue

            raw_plan = clean(row.get("Plan Type", "")).upper()
            plan_type = PLAN_TYPE_MAP.get(raw_plan)
            if plan_type is None:
                continue

            total_rows += 1

            try:
                date_iso = parse_date(row.get("Date Acquired", ""), "Date Acquired", row_num)

                if upto_year is not None and int(date_iso[:4]) > upto_year:
                    filtered_out += 1
                    continue

                qty = to_dec(row.get("Sellable Qty.", ""), "Sellable Qty.", row_num)
                cost_shr_usd = to_dec(
                    row.get("Est. Cost Basis (per share):", ""),
                    "Est. Cost Basis (per share):",
                    row_num,
                )

                lots.append({
                    "row": row_num,
                    "source": "etrade_holdings_sellable_expanded",
                    "plan": plan_type,
                    "symbol": clean(row.get("Symbol", "")),
                    "date_acquired": date_iso,
                    "qty": qty,
                    "cost_shr_usd": cost_shr_usd,
                    "grant_number": clean(row.get("Grant Number", "")),
                    "vest_date": clean(row.get("Vest Date", "")),
                    "release_date": clean(row.get("Release Date", "")),
                    "grant_date": clean(row.get("Grant Date", "")),
                    "est_mkt_value": clean(row.get("Est. Market Value", "")),
                })
            except ValueError as exc:
                skipped.append(str(exc))

    return lots, skipped, total_rows, filtered_out
