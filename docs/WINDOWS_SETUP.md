# Windows setup (PowerShell)

Verified on Windows 11 with Python 3.14.5 and git 2.54.0.

## 1. Check your tooling

```powershell
python --version
git --version
```

Expect Python 3.12 or newer. This project was built and tested on 3.14.5.

## 2. Create and activate a virtual environment

```powershell
cd C:\Users\teja\Documents\projects\smart-trader
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Your prompt should now start with `(.venv)`.

## 3. Install dependencies

```powershell
pip install -r requirements.txt
```

## 4. Run the tests

```powershell
python -m pytest tests\ -v
```

Expected: 69 passed.

## 5. Load / regenerate sample data

The sample CSVs are committed, so this is optional:

```powershell
python scripts\generate_sample_data.py
```

## 6. Run a backtest

```powershell
python -m app.cli backtest --symbols SAMPLE_TCS,SAMPLE_INFY
```

With a date range, custom starting cash, persistent ledger and CSV export:

```powershell
python -m app.cli backtest --symbols SAMPLE_TCS,SAMPLE_INFY `
    --start 2024-02-01 --end 2024-06-30 `
    --starting-cash 100000 `
    --db data\paper_trading.db `
    --export-csv data\exports\trades.csv
```

## 7. Use your own CSV data

```powershell
python -m app.cli backtest --symbols TCS --data-dir C:\path\to\your\csvs
```

## 7b. Scan a watchlist

A scan is one decision point: it reads the newest bars, refuses to act on
stale or invalid data, respects the market session, and records what it did.

```powershell
# One scan against the sample data (pin "now" so sample dates are fresh)
python -m app.cli scan --symbols SAMPLE_TCS,SAMPLE_INFY `
    --now 2024-09-10T11:00:00+05:30

# Use a watchlist file and record orders/events to the ledger
python -m app.cli scan --watchlist my_watchlist.csv --db data\paper_trading.db

# Intraday scanning: tighten the staleness limit to match 5-minute bars
python -m app.cli scan --strategy intraday --interval 5m `
    --symbols SAMPLE_INTRADAY_TCS --max-bar-age-minutes 15

# A bounded series of scans, 60s apart (respects provider rate limits)
python -m app.cli scan --repeat 5 --every 60
```

`my_watchlist.csv` needs a `symbol` column:

```
symbol
SAMPLE_TCS
SAMPLE_INFY
```

**Exit codes:** `0` = clean scan, `1` = the scan produced warnings (stale
data, provider error, invalid data). Useful for scheduled runs — have Task
Scheduler alert you on a non-zero exit.

### Scheduling it

There is deliberately no daemon. Use Windows Task Scheduler:

```powershell
# Every 15 minutes on weekdays; adjust to your session and interval
schtasks /Create /TN "SmartTraderScan" /SC MINUTE /MO 15 /D MON,TUE,WED,THU,FRI `
  /TR "C:\Users\teja\Documents\projects\smart-trader\.venv\Scripts\python.exe -m app.cli scan --db data\paper_trading.db" `
  /ST 09:15 /ET 15:30 /K
```

## 7c. Start the dashboard

```powershell
python -m app.cli serve --db data\paper_trading.db
# then open http://127.0.0.1:8000
```

It is **read-only** and binds to localhost only. It has no authentication —
do not change `--host` unless you understand that it exposes your ledger to
your network.

On startup `serve` opens the ledger once for write to apply any additive
schema migration (a ledger written by an older build is otherwise missing
tables the dashboard reads). Back the file up first if you want to be safe:

```powershell
Copy-Item data\paper_trading.db data\paper_trading.db.bak
```

## 8. Stopping the app safely

The CLI is a short-lived process; it exits on its own. If you need to stop it
mid-run, press `Ctrl+C`. SQLite writes are committed per trade, so an
interrupted run leaves a consistent ledger (already-written trades are kept).

## 9. Export paper-trade results

Either use `--export-csv` as above, or query the SQLite ledger directly:

```powershell
python -c "import sqlite3; c=sqlite3.connect('data/paper_trading.db'); [print(r) for r in c.execute('SELECT symbol, entry_at, exit_at, net_pnl, exit_reason FROM trades ORDER BY exit_at')]"
```

## 10. Back up the ledger

The ledger is a single file. Copy it while the app is not running:

```powershell
Copy-Item data\paper_trading.db "data\backup_paper_trading_$(Get-Date -Format yyyyMMdd_HHmmss).db"
```

## Common errors and fixes

**`Activate.ps1 cannot be loaded because running scripts is disabled`**

PowerShell's execution policy is blocking the activation script. Allow
signed/local scripts for your user only:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

**`ModuleNotFoundError: No module named 'app'`**

Run commands from the project root (the folder containing `app\`), and use
`python -m app.cli ...` rather than `python app\cli.py`.

**`ModuleNotFoundError: No module named 'pandas'` / wrong Python**

Your virtual environment probably isn't active. Re-run
`.\.venv\Scripts\Activate.ps1`, or call the venv interpreter explicitly:
`.\.venv\Scripts\python.exe -m pytest tests\`.

**`pip install` tries to build pandas from source and fails on `vswhere.exe`**

You are installing a pandas version with no prebuilt wheel for your Python
version. Use the pinned versions in `requirements.txt` — they were chosen
because wheels exist for Python 3.14 on Windows. Do not downgrade pandas to
2.x on Python 3.14; it has no wheel and needs Visual Studio build tools.

**Dashboard shows "The ledger could not be read" / HTTP 503**

Your ledger was written by an older build and is missing tables the dashboard
reads. Starting `serve` normally migrates it automatically. If it still
fails, run any backtest against that file to force the migration:

```powershell
python -m app.cli backtest --symbols SAMPLE_TCS --db data\paper_trading.db
```

**Every scanned symbol reports `REJECTED_STALE`**

Expected when scanning the bundled sample data, whose newest bar is dated
2024. Either pin `--now` to a date near the data
(`--now 2024-09-10T11:00:00+05:30`) or raise `--max-bar-age-minutes`. Do not
raise it in real use — that guard is what stops a post-outage burst of
entries.

**`ModuleNotFoundError: No module named 'fastapi'`**

The dashboard dependencies are in `requirements.txt`; re-run
`pip install -r requirements.txt` inside the activated virtual environment.

**`No CSV file found for symbol 'X'`**

The provider matches filenames containing the symbol. `--symbols TCS` will
match `TCS.csv` or `SAMPLE_TCS.csv`. If two files match, it errors rather
than guessing — rename one.
