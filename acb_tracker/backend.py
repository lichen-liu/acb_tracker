#!/usr/bin/env python3
"""
Stateless ACB event database and state recomputation helpers.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Optional


DB_FIELDS = [
    "event_type",
    "date",
    "num_shares",
    "currency",
    "price_per_share",
    "fee_amount",
    "fx_rate",
    "fx_source",
    "acb_amount_cad",
    "fx_date",
    "source",
    "plan",
    "symbol",
    "reference",
]

DECIMAL_FIELDS = {
    "num_shares",
    "price_per_share",
    "fee_amount",
    "fx_rate",
    "acb_amount_cad",
}

MONEY = Decimal("0.01")
RATE = Decimal("0.0001")
SHARES = Decimal("0.0001")


@dataclass(frozen=True)
class RecomputedState:
    display_rows: list[dict]
    capital_gain_by_year: dict[int, Decimal]
    cum_shares: Decimal
    cum_acb: Decimal
    cum_acb_usd: Decimal


def parse_decimal(value: str) -> Decimal:
    cleaned = value.strip()
    if not cleaned:
        return Decimal("0")
    return Decimal(cleaned)


def q_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, ROUND_HALF_UP)


def q_rate(value: Decimal) -> Decimal:
    return value.quantize(RATE, ROUND_HALF_UP)


def q_shares(value: Decimal) -> Decimal:
    return value.quantize(SHARES, ROUND_HALF_UP)


def event_identity(event: dict) -> tuple:
    return (
        event.get("event_type", ""),
        event.get("date", ""),
        str(event.get("num_shares", "")),
        event.get("currency", ""),
        str(event.get("price_per_share", "")),
        str(event.get("fee_amount", "")),
        str(event.get("fx_rate", "")),
        event.get("fx_source", ""),
        str(event.get("acb_amount_cad", "")),
        event.get("source", ""),
        event.get("plan", ""),
        event.get("symbol", ""),
        event.get("reference", ""),
    )


def _parse_event_row(raw: dict) -> dict:
    parsed = {}
    for field in DB_FIELDS:
        value = (raw.get(field) or "").strip()
        if field == "fee_amount" and field not in raw:
            value = "0"
        if field in DECIMAL_FIELDS:
            parsed[field] = parse_decimal(value)
        else:
            parsed[field] = value
    return parsed


def load_events(db_path: Path) -> list[dict]:
    events: list[dict] = []
    with db_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            return events
        fieldnames = [field.strip().lstrip("\ufeff") for field in reader.fieldnames]
        missing_fields = [field for field in DB_FIELDS if field not in fieldnames and field != "fee_amount"]
        if missing_fields:
            raise ValueError(
                "Database is not in the stateless event format. "
                f"Missing columns: {', '.join(missing_fields)}"
            )

        for raw in reader:
            if raw is None:
                continue
            row = {key: (value or "").strip() for key, value in raw.items() if key}
            if not any(row.values()):
                continue
            events.append(_parse_event_row(row))
    return events


def save_events(db_path: Path, events: list[dict]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with db_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=DB_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(events)


def filter_events_upto_year(events: list[dict], upto_year: Optional[int]) -> list[dict]:
    if upto_year is None:
        return list(events)
    filtered: list[dict] = []
    for event in events:
        date = event.get("date", "")
        if date[:4].isdigit() and int(date[:4]) <= upto_year:
            filtered.append(event)
    return filtered


def cad_price_per_share(event: dict) -> Decimal:
    return q_rate(event["price_per_share"] * event["fx_rate"])


def cad_fee_amount(event: dict) -> Decimal:
    return q_money(event["fee_amount"] * event["fx_rate"])


def usd_event_amount(event: dict) -> Decimal:
    return q_money(event["num_shares"] * event["price_per_share"])


def sort_events(events: list[dict]) -> list[dict]:
    # TODO: Persist an explicit same-day sequence/timestamp field.
    # Date + insertion order is not a sufficient long-term tie-breaker when
    # buy and sell events occur on the same day and ACB depends on their order.
    indexed = list(enumerate(events))
    indexed.sort(key=lambda pair: (pair[1].get("date", ""), pair[0]))
    return [event for _, event in indexed]


def recompute_state(events: list[dict], upto_year: Optional[int] = None) -> RecomputedState:
    filtered_events = sort_events(filter_events_upto_year(events, upto_year))
    display_rows: list[dict] = []
    capital_gain_by_year: dict[int, Decimal] = {}
    cum_shares = Decimal("0")
    cum_acb = Decimal("0")
    cum_acb_usd = Decimal("0")

    for event in filtered_events:
        event_type = event.get("event_type", "").lower()
        shares = event["num_shares"]
        price_cad = cad_price_per_share(event)
        fee_cad = cad_fee_amount(event)
        acb_per_share_before = Decimal("0")
        acb_per_share_before_usd = Decimal("0")
        delta_total = Decimal("0")
        delta_total_usd = Decimal("0")
        gain = Decimal("0")

        if event_type == "buy":
            delta_total = q_money(event["acb_amount_cad"])
            delta_total_usd = usd_event_amount(event)
            delta_per_share = q_rate(delta_total / shares) if shares else Decimal("0")
            delta_per_share_usd = q_rate(delta_total_usd / shares) if shares else Decimal("0")
            cum_shares = q_shares(cum_shares + shares)
            cum_acb = q_money(cum_acb + delta_total)
            cum_acb_usd = q_money(cum_acb_usd + delta_total_usd)
        elif event_type == "sell":
            if shares > cum_shares:
                raise ValueError(
                    f"Sell on {event['date']} exceeds current holdings: "
                    f"trying to sell {shares} while only {cum_shares} remain."
                )
            if cum_shares == 0:
                raise ValueError(f"Sell on {event['date']} cannot be applied to zero holdings.")
            acb_per_share_before = q_rate(cum_acb / cum_shares)
            acb_per_share_before_usd = q_rate(cum_acb_usd / cum_shares)
            delta_total = q_money(shares * acb_per_share_before)
            delta_total_usd = q_money(shares * acb_per_share_before_usd)
            delta_per_share = acb_per_share_before
            delta_per_share_usd = acb_per_share_before_usd
            total_selling = q_money((shares * event["price_per_share"] - event["fee_amount"]) * event["fx_rate"])
            gain = q_money(total_selling - delta_total)
            cum_acb = q_money(cum_acb - delta_total)
            cum_acb_usd = q_money(cum_acb_usd - delta_total_usd)
            cum_shares = q_shares(cum_shares - shares)
            year = int(event["date"][:4])
            capital_gain_by_year[year] = q_money(capital_gain_by_year.get(year, Decimal("0")) + gain)
        else:
            raise ValueError(f"Unsupported event type: {event.get('event_type', '')}")

        cum_acb_per_share = q_rate(cum_acb / cum_shares) if cum_shares else Decimal("0")
        cum_acb_per_share_usd = q_rate(cum_acb_usd / cum_shares) if cum_shares else Decimal("0")
        display_rows.append({
            "event_type": event_type,
            "date": event["date"],
            "num_shares": shares,
            "currency": event["currency"],
            "price_per_share": event["price_per_share"],
            "fee_amount": event["fee_amount"],
            "fx_rate": event["fx_rate"],
            "fx_source": event.get("fx_source", ""),
            "cad_price_per_share": price_cad,
            "cad_fee_amount": fee_cad,
            "acb_delta_total": delta_total,
            "acb_delta_per_share": delta_per_share,
            "acb_delta_total_usd": delta_total_usd,
            "acb_delta_per_share_usd": delta_per_share_usd,
            "cum_acb_total": cum_acb,
            "cum_acb_per_share": cum_acb_per_share,
            "cum_acb_total_usd": cum_acb_usd,
            "cum_acb_per_share_usd": cum_acb_per_share_usd,
            "cum_shares": cum_shares,
            "capital_gain": gain,
            "source": event.get("source", ""),
            "plan": event.get("plan", ""),
            "symbol": event.get("symbol", ""),
            "reference": event.get("reference", ""),
            "fx_date": event.get("fx_date", ""),
        })

    return RecomputedState(
        display_rows=display_rows,
        capital_gain_by_year=capital_gain_by_year,
        cum_shares=cum_shares,
        cum_acb=cum_acb,
        cum_acb_usd=cum_acb_usd,
    )
