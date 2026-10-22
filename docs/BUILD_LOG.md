# Build log

## 2026-10-22 — Milestone 0

- Inspected repository: empty except `CLAUDE.md` (no git repo yet).
- Inspected environment: Python 3.14.5 (only interpreter installed via the
  `py` launcher), pip 26.1.1, git 2.54.0.
- Verified (via a throwaway virtualenv, `pip install --dry-run`, deleted
  after) that current stable wheels exist for Python 3.14 on Windows for:
  pandas 3.1.0, numpy 2.5.4, pydantic 2.14.1, pytest 9.1.1, streamlit
  1.66.1, python-dotenv 1.2.4. Older pandas (e.g. 2.2.3) has no prebuilt
  wheel for 3.14 and fails to build from source without Visual Studio
  build tools — so dependency pins must target the versions above, not
  older ones implied by CLAUDE.md's illustrative version numbers.
- Checked data-source options (see `docs/DATA_SOURCES.md`): no compliant
  free real-time/historical NSE API found. NSE's own site could not be
  fetched programmatically (timed out) and its data-usage policy restricts
  redistribution; yfinance is unofficial with gray-area ToS standing;
  Alpha Vantage's free tier has weak NSE coverage and low rate limits.
  Decision: Milestone 1 uses bundled sample CSVs + user-imported CSVs only.
- Wrote `docs/ARCHITECTURE.md` and `docs/DATA_SOURCES.md`.
- No application code written yet. No live order logic exists or is
  planned. No cloud resources or paid APIs used.
- Awaiting user approval of the Milestone 1 plan before implementing it.

## 2026-10-22 — Milestone 1

User approved the plan: swing (daily-bar) strategy first, git initialized.

Implemented:
- `app/models.py` — Bar, Signal, Order, Position, Trade, DataQuality enum.
- `app/config.py` — pydantic-validated config: starting cash (default
  Rs 100,000), risk limits, swing params, cost assumptions. Rejects a
  timezone other than Asia/Kolkata and a slow MA period <= fast.
- `app/data/` — `DataProvider` interface, `CsvProvider` (bundled samples +
  user CSVs, IST-localized), `validation.py` (naive timestamps, zero/negative
  prices, OHLC consistency, duplicate/out-of-order bars, abnormal gaps,
  staleness).
- `app/strategies/swing.py` — MA trend-filter prototype with explicit
  NO_TRADE paths and deterministic signal IDs.
- `app/backtest/` — causal event-driven `engine.py`, `costs.py`
  (slippage always adverse; STT on sell, stamp duty on buy), `metrics.py`
  (net vs gross separately, drawdown, expectancy, buy-and-hold benchmark).
- `app/paper/` — `portfolio.py` (virtual cash/positions/P&L + reconcile),
  `risk.py` (sizing + all limits + kill switch), `broker.py` (order
  lifecycle, duplicate-signal rejection, simulated fills only).
- `app/storage/sqlite.py` — restart-safe ledger (trades, orders, runs).
- `app/cli.py` — `python -m app.cli backtest`, prints the PAPER TRADING
  banner, metrics, reconciliation status; optional SQLite ledger + CSV export.
- `scripts/generate_sample_data.py` + `data/samples/SAMPLE_{TCS,INFY}.csv`
  (180 synthetic daily bars each — NOT real market data).
- Docs: README, WINDOWS_SETUP, STRATEGIES, RISK_AND_LIMITATIONS.

Test results (actually run): **69 passed** via
`.venv\Scripts\python.exe -m pytest tests\`.

End-to-end run (actually run) on both sample symbols: 360 signals evaluated,
3 trades closed, final equity Rs 102,788.55 on Rs 100,000 starting cash,
net return 2.79% vs gross 2.82%, max drawdown 1.54%, ledger reconciliation
OK. Re-running against the same DB grew the ledger from 3 to 4 trades,
confirming restart persistence. These numbers describe SYNTHETIC data and
mean nothing about real markets.

Known limitations carried forward (documented in
`docs/RISK_AND_LIMITATIONS.md`, not hidden):
- Fills are simulated at the same bar's close that produced the signal —
  optimistic vs reality. Next-bar-open fills are the correct fix.
- Stop-loss/target levels are recorded on positions but NOT enforced by the
  engine; exits occur only via strategy SELL signals. Intrabar stop/target
  collision handling is deliberately deferred.
- Cost rates are UNVERIFIED assumptions, not current official rates.
- No market-holiday calendar; no out-of-sample/walk-forward evaluation yet.

Still not built: intraday strategy, scanner, dashboard, alerts, live
providers, cloud deployment. No live order placement exists or is possible.
