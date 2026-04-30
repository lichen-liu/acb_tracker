# acb_tracker

Beta 0.1 tracker for calculating and staging ACB data from E*TRADE holdings exports.

## Current scope

- `utils/calculate_acb.py`
  Calculates ACB rows from an E*TRADE holdings CSV and can export a calculated CSV.
- `acb_db.py`
  Interactive tracker for loading or staging a per-user per-symbol CSV database.
- `utils/etrade_holdings_csv.py`
  Parser for the current input format:
  `etrade - holding - by status - sellable - download expanded`

## Quick start

Calculate ACB from a CSV:

```bash
python3 utils/calculate_acb.py --input /path/to/etrade_holdings.csv
python3 utils/calculate_acb.py --input /path/to/etrade_holdings.csv --output output/acb.csv
```

Open a new in-memory tracker session:

```bash
python3 acb_db.py new <user> <stock_symbol_name>
```

Load an existing tracker database:

```bash
python3 acb_db.py load <user> <stock_symbol_name>
```

Database files live at:

```text
private_db/<user>/<stock_symbol_name>_db.csv
```

## Tracker commands

Inside `acb_db.py`:

- `merge <etrade_csv_path>`
- `view`
- `view <upto_year>`
- `exit` / `quit`

`merge` supports line editing, history, and Tab path completion in the CLI.

## Notes

- Merge stages changes in memory first and asks for confirmation before saving on exit.
- `view <upto_year>` shows ACB calculations up to and including that year.
- Output rows carry a `source` field. Current import source is E*TRADE only.
- FX rates come from the Bank of Canada VALET API.
- Per-share USD cost comes from `Est. Cost Basis (per share):` for RSU rows and `Purchase Date FMV` for ESPP rows.

## Beta 0.1 limits

- Only one broker input is supported right now: E*TRADE holdings sellable expanded export.
- The database format is still simple and may change.
- There is no automated test suite yet.
