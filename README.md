# Smart Trader — Indian equities paper-trading research tool

> **PAPER TRADING — NO REAL ORDERS.**
> This application simulates trades with virtual money. It has no broker
> integration, handles no broker credentials or API keys, and contains no code
> path that can place, modify or cancel a real order. **That guarantee is
> unconditional** — it does not depend on how you configure the app.
> Simulated fills do not guarantee real-world fills or prices. Nothing here
> is investment advice or a claim of profitability.

> **Network access is a separate question.** Every data source shipped today is
> file-based and makes no network call: CSV files, and NSE UDiFF bhavcopy files
> you download yourself in a browser. Nothing is scraped, streamed, polled or
> quoted. If an optional network data source is ever added and you switch it on,
> that sentence stops being true for your install — but the no-real-orders
> guarantee above still holds. The two are deliberately stated separately so the
> important one is never weakened by the other.

## What works today (Milestones 1–3)

- Loads daily and intraday OHLCV bars from CSV files (bundled synthetic
  samples + your own files).
- Validates data: timezone-aware IST timestamps, OHLC consistency,
  zero/negative prices, duplicate/out-of-order bars, abnormal gaps, staleness.
- **Two** experimental strategies, both returning `NO_TRADE` with a reason
  when data is bad, history is short, or no setup exists:
  - swing (daily): moving-average trend filter
  - intraday: MA trend filter + momentum + volume confirmation + session rules
- Causally-correct backtest: decisions use only bars at or before the decision
  time, and **signals fill at the next bar's open**, never at the close that
  produced them.
- Stop-loss/target enforced intrabar. If both are hit in one bar, the engine
  assumes the **stop** filled first and flags the trade as ambiguous.
- Configurable market session: entry cutoff and forced end-of-day flattening
  (no overnight intraday positions). Holiday list is user-supplied.
- Virtual broker with position sizing, risk limits, kill switch, slippage,
  configurable cost assumptions, and duplicate-signal protection.
- Out-of-sample and segmented multi-period evaluation that tells you when a
  window was too short to warm up indicators instead of printing misleading
  zeros.
- Honest strategy comparison that **refuses to name a winner** on thin
  evidence.
- **Watchlist scanner** that refuses to act on stale or invalid data, skips
  closed sessions and holidays, is idempotent per bar, and isolates a single
  symbol's provider failure instead of aborting the scan.
- **Read-only local dashboard** (FastAPI, localhost-only): overview, trades,
  signals, orders, positions, risk settings, health, event log, DB explorer,
  and CSV export. Every route is a GET and the database is opened `mode=ro` —
  enforced by tests.
- SQLite ledger that survives restarts, plus CSV export of closed trades.
- `scan --db <ledger>` resumes its virtual cash, open positions and
  already-processed bars across runs (without `--db` every run starts flat).
- Run the suite with `python -m pytest tests\` (the count changes often, so it
  is not quoted here).

## Not built yet

Telegram alerts, live data providers, a supervised scheduler, and cloud
deployment. Scanner persistence has caveats (no crash-atomicity across the
order-write and snapshot steps, no locking between concurrent scans) — see
[docs/RISK_AND_LIMITATIONS.md](docs/RISK_AND_LIMITATIONS.md).
See `CLAUDE.md` for the milestone plan.

## Quick start (Windows PowerShell)

```powershell
# 1. Check tooling
python --version
git --version

# 2. Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the tests
python -m pytest tests\ -v

# 5. Regenerate sample data (optional — it is already committed)
python scripts\generate_sample_data.py

# 6. Run a swing backtest end-to-end
python -m app.cli backtest --symbols SAMPLE_TCS,SAMPLE_INFY

# 7. Run the intraday strategy (adds session rules + EOD flattening)
python -m app.cli backtest --strategy intraday --interval 5m `
    --symbols SAMPLE_INTRADAY_TCS

# 8. Evaluate across time periods instead of one headline number
python -m app.cli evaluate --symbols SAMPLE_TCS --mode oos
python -m app.cli evaluate --symbols SAMPLE_TCS --mode segmented --windows 2

# 9. Compare the two strategies (it will refuse to rank thin evidence)
python -m app.cli compare

# 10. Run with a persistent ledger and a CSV export
python -m app.cli backtest --symbols SAMPLE_TCS,SAMPLE_INFY `
    --db data\paper_trading.db --export-csv data\exports\trades.csv

# 11. Scan a watchlist (pin --now so the 2024 sample data isn't "stale")
python -m app.cli scan --symbols SAMPLE_TCS,SAMPLE_INFY `
    --now 2024-09-10T11:00:00+05:30 --db data\paper_trading.db

# 12. Start the read-only dashboard, then open http://127.0.0.1:8000
python -m app.cli serve --db data\paper_trading.db

# 13. Optional: also serve real NSE end-of-day data on the Market Data page.
#     You download the "CM-UDiFF Common Bhavcopy Final" files yourself from
#     nseindia.com (All Reports) in a browser and put them in one folder; the
#     app never fetches them. Either .csv or .csv.zip works as served.
python -m app.cli serve --db data\paper_trading.db --bhavcopy-dir data\bhavcopy
```

Full setup notes, including common errors, are in
[docs/WINDOWS_SETUP.md](docs/WINDOWS_SETUP.md).

## Your data is persistent — and how to back it up

The ledger is a single SQLite file (default `data/paper_trading.db`). It is a
plain file on disk, so **nothing is wiped when you restart the app, the
dashboard, or your machine.** Re-running a backtest against the same `--db`
*appends* a new run; it never replaces the previous one. Opening an older
ledger with a newer build of the app only ever *adds* columns and tables
(`ALTER TABLE ... ADD COLUMN`); no migration in this project drops, truncates,
or recreates a table.

Three things worth knowing:

1. **The database is not in version control.** `*.db` is in `.gitignore`, so
   deleting or re-cloning the folder loses your simulated history. Back it up
   yourself.
2. **Re-running the same backtest appends duplicate logical trades** under new
   trade IDs. Each row records the `run_id` that produced it, so you can tell
   the runs apart — but the duplication itself is real. Filter by run in the
   dashboard, or reset between experiments.
3. **The dashboard can never delete anything.** It opens the database with
   SQLite's read-only flag and exposes no write, edit, delete, or raw-SQL
   endpoint. Deleting history is possible only from the CLI, interactively.

```powershell
# Back up the ledger (copy all three files if the -wal/-shm sidecars exist)
Copy-Item data\paper_trading.db "data\paper_trading.backup.db"
Get-ChildItem data\paper_trading.db* | Copy-Item -Destination data\backup\

# Export what you care about to CSV (also available from the dashboard)
python -m app.cli backtest --symbols SAMPLE_TCS `
    --db data\paper_trading.db --export-csv data\exports\trades.csv

# Inspect the ledger without the dashboard
python -c "import sqlite3; print(sqlite3.connect('data/paper_trading.db').execute('select count(*) from trades').fetchone())"

# Delete ALL simulated history. Asks you to type a confirmation phrase;
# keeps the app_events audit trail so the reset itself stays on record.
python -m app.cli reset --db data\paper_trading.db
```

To restore a backup, stop the app and copy the file back over
`data\paper_trading.db`.

## Using your own data

Put a CSV per symbol in a directory, named so the filename contains the
symbol (e.g. `TCS.csv`), with this header:

```
date,open,high,low,close,volume
2024-01-02,3500.0,3502.4,3494.5,3496.1,1744492
```

Then point the CLI at it:

```powershell
python -m app.cli backtest --symbols TCS --data-dir C:\path\to\your\csvs
```

Dates are treated as already being in `Asia/Kolkata` (IST) and are
tz-localized, not shifted.

## Important: the bundled sample data is synthetic

`data/samples/SAMPLE_*.csv` are a deterministic random walk generated by
`scripts/generate_sample_data.py`. They are **not real market data** and
must not be used to draw any conclusion about a real stock.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — module layout and data flow
- [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) — what data sources were checked and why CSV-only
- [docs/STRATEGIES.md](docs/STRATEGIES.md) — the swing prototype's rules and parameters
- [docs/RISK_AND_LIMITATIONS.md](docs/RISK_AND_LIMITATIONS.md) — **read this before trusting any number**
- [docs/WINDOWS_SETUP.md](docs/WINDOWS_SETUP.md) — PowerShell commands and troubleshooting
- [docs/BUILD_LOG.md](docs/BUILD_LOG.md) — what changed in each milestone
