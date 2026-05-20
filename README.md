# acb_tracker

Beta 0.1 tracker for calculating and staging ACB data from E*TRADE holdings exports.

## Layout

The main entry script is:

```bash
python3 run_acb_tracker.py ...
```

Source code lives under `acb_tracker/`. Secondary tools live under `scripts/`.

```text
run_acb_tracker.py        # main interactive entry
acb_tracker/
  cli.py                 # interactive tracker CLI
  backend.py             # stateless event storage + recomputation
  calculate_acb.py       # shared ACB calculator logic
  etrade.py              # E*TRADE CSV parser
scripts/
  calculate_acb.py       # standalone calculator entry
examples/
  amd_tax_2025_template.csv
private_db/
private_in/
```

## Current scope

- `acb_tracker.py`
  Main interactive tracker entrypoint.
- `scripts/calculate_acb.py`
  Calculates ACB rows from an E*TRADE holdings CSV and can export a calculated CSV.
- `acb_tracker/backend.py`
  Stateless event database and state recomputation logic.
- `acb_tracker/etrade.py`
  Parser for the current input format:
  `etrade - holding - by status - sellable - download expanded`

## Quick start

Start the interactive tracker:

```bash
python3 run_acb_tracker.py new <user> <stock_symbol_name>
python3 run_acb_tracker.py load <user> <stock_symbol_name>
```

Calculate ACB from a CSV:

```bash
python3 scripts/calculate_acb.py --input /path/to/etrade_holdings.csv
python3 scripts/calculate_acb.py --input /path/to/etrade_holdings.csv --output output/acb.csv
```

Database files live at:

```text
private_db/<user>/<stock_symbol_name>_db.csv
```

## Database model

The persistent database is now a stateless event store. Each row is a `buy` or
`sell` event with the raw inputs needed to recompute state:

- `event_type`
- `date`
- `num_shares`
- `price_per_share` plus `currency`, `fx_rate`, and `fx_source`
- `acb_amount_cad` for buys
- `reference` for source-side provenance only

The displayed ACB table is recomputed every time by sorting events oldest to
newest and maintaining cumulative shares, cumulative ACB, and yearly capital
gains.

Only the stateless event format is supported. Older precomputed lot databases
must be migrated before they can be loaded.

## Tracker commands

Inside the interactive tracker:

- `merge <etrade_csv_path>`
- `view`
- `view <upto_year>`
- `save`
- `exit` / `quit`

`merge` supports line editing, history, and Tab path completion in the CLI.

## Notes

- Merge stages changes in memory first and asks for confirmation before saving on exit.
- `save` flushes the in-memory database to disk immediately.
- `view <upto_year>` recomputes ACB state up to and including that year.
- Output rows carry a `source` field. Current import source is E*TRADE only.
- FX rates come from the Bank of Canada VALET API.
- Stored events also carry `fx_source` so the FX provider is preserved in the database.
- Per-share USD cost comes from `Est. Cost Basis (per share):` for RSU rows and `Purchase Date FMV` for ESPP rows.
- `reference` is not used in ACB math. It is a compact optional provenance string in `key=value,key=value` form, assembled from import metadata such as `grant_number`, `vest_date`, `release_date`, and `grant_date`.

## Beta 0.1 limits

- Only one broker input is supported right now: E*TRADE holdings sellable expanded export.
- The database format is still simple and may change.
- There is no automated test suite yet.
