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

**`No CSV file found for symbol 'X'`**

The provider matches filenames containing the symbol. `--symbols TCS` will
match `TCS.csv` or `SAMPLE_TCS.csv`. If two files match, it errors rather
than guessing — rename one.
