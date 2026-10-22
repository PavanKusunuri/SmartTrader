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

## 2026-10-22 — Milestone 2

Fixed two Milestone-1 correctness limitations first, because comparing
strategies on an optimistic fill model would have produced a misleading
comparison:

- **Next-bar-open fills.** A signal on bar N is now queued and filled at bar
  N+1's open plus adverse slippage, never at the close that produced it.
  Queued orders are re-validated against risk limits at fill time, so a stale
  queued order cannot bypass the kill switch or exposure caps. A signal on
  the final bar expires unfilled and is recorded as a cancelled order.
- **Intrabar stop-loss / target enforcement.** Both levels are now checked
  against each bar's low/high. When only one is touched it fills at its
  level. When BOTH are touched in one bar, the engine assumes the STOP filled
  first (the worse outcome) and marks the trade `ambiguous_exit=True`. It
  never assumes the favourable ordering.

Added:
- `app/market_calendar.py` — `MarketSession` (open/close, entry cutoff,
  flatten time) with validation, plus `load_holidays()`. The holiday list is
  EMPTY by default; CLAUDE.md forbids hardcoding an unverified one.
- `app/strategies/intraday.py` — `intraday_ma_momentum`: MA trend filter +
  momentum + volume confirmation + session awareness, with explicit NO_TRADE
  reasons for each failed condition.
- `app/backtest/evaluation.py` — in/out-of-sample split (chronological,
  never shuffled) and segmented multi-window evaluation, plus honest
  comparison helpers (`build_comparison`, `can_declare_winner`).
- `Strategy.required_history` — lets evaluation distinguish "found no setup"
  from "window too short to warm up indicators", which otherwise look
  identical (both show 0 trades).
- CLI `evaluate` and `compare` commands; `backtest` gained
  `--strategy intraday|swing`, `--interval`, `--holidays`.
- 5-minute intraday sample data (1500 bars x 2 symbols, synthetic).

Test results (actually run): **119 passed** via
`.venv\Scripts\python.exe -m pytest tests\` — 107 plus 12 new calendar tests.
New suites: `test_fill_and_exit_model.py` (10), `test_intraday_strategy.py`
(10), `test_evaluation.py` (18), `test_market_calendar.py` (12).

Runs actually executed on synthetic sample data:
- Intraday backtest: 1502 signals, 12 trades, net **-0.71%** (gross -0.61%),
  win rate 8.3%, vs buy-and-hold +2.29%. It LOSES money net of assumed
  costs. Reported, not tuned away — this is the expected result of trading a
  random walk with costs.
- Swing backtest: 3 trades, net +3.47%.
- `compare`: printed **NO WINNER DECLARED** despite the +3.47% vs -0.71%
  spread, because both strategies are far below the 30-trade minimum. This is
  the intended behaviour.
- `evaluate --mode segmented --windows 4` on 180 daily bars: all four windows
  flagged `too short to warm up` (45 bars each vs 51 required) and the run
  reported INCONCLUSIVE instead of presenting four zeros as a result.
- `evaluate --mode oos` on intraday data: in-sample 9 trades -0.68%,
  out-of-sample 2 trades +0.05% — not consistent across periods.

Note: `app/storage/sqlite.py`, `app/storage/persist.py`, `app/cli.py`
(reset/serve commands), `app/api/` and parts of `app/backtest/engine.py` were
extended outside this milestone's work; those changes are additive, pass the
full suite, and were kept. `reset_ledger` requires an exact confirmation
token and has no implicit callers.

**KNOWN BROKEN — `python -m app.cli serve` does not work.** It is partial
Milestone 3 (dashboard) work:
- `app/api/main.py` does not exist, so `cmd_serve`'s
  `from app.api.main import create_app` raises ImportError.
- `uvicorn` and a web framework are not in `requirements.txt`, so the
  "install the dashboard dependencies" message it prints is itself wrong.
- `app/api/derive.py` and `app/api/queries.py` exist and import cleanly but
  have **no test coverage**.
Everything else listed in this milestone works and is tested. The dashboard
is not claimed to be functional; finish it as part of Milestone 3.

Limitations added/retained — see `docs/RISK_AND_LIMITATIONS.md`:
- "Walk-forward" here is segmentation, NOT optimisation (nothing is fitted).
- Next-bar-open fills still assume you get the open price; flat-percentage
  slippage understates gap risk.
- Holiday calendar empty unless the user supplies one.
- Intraday results are the most cost-sensitive and the cost rates remain
  unverified; a small rate error flips the sign.

Still not built: scanner/watchlist scheduling, Telegram alerts, live data
providers, cloud deployment. No live order placement exists or is possible.

## 2026-10-22 — Milestone 3

### Scanner (new)
- `app/scanner/universe.py` — `Watchlist` with normalisation, duplicate
  rejection, a 50-symbol v1 cap, and CSV loading that errors loudly rather
  than degrading to "scan nothing".
- `app/scanner/scanner.py` — `Scanner.scan_once(now)` returning a
  `ScanReport` of per-symbol outcomes. Implements the CLAUDE.md section 10
  safety rules: session gating (provider is not even consulted when the
  market is shut), staleness revalidation (`max_bar_age`) so a recovered
  outage cannot replay old bars into a burst of entries, per-bar idempotency,
  and per-symbol failure isolation for provider/strategy exceptions.
- `now` is injected rather than read from the clock, so scans are
  reproducible and a backfilled scan cannot mistake stale data for current.
- CLI `scan` command: `--watchlist`/`--symbols`, `--max-bar-age-minutes`,
  `--repeat`/`--every` (bounded, not a daemon), `--now`, `--db`. Exits 1 when
  a scan produced warnings, so an OS scheduler can alert on it.

### Dashboard (repaired and tested)
- Added `fastapi`, `uvicorn` and (test-only) `httpx` to `requirements.txt`.
  `serve` was previously broken: it printed "install the dashboard
  dependencies" while `requirements.txt` contained none of them.
- Root-caused the 503s on an existing ledger: `data/paper_trading.db` was
  written by the Milestone 1 schema, and the dashboard opens the ledger
  read-only so it cannot migrate. `serve` now opens it once for write first,
  applying the ADDITIVE-ONLY migration. Verified on a copy: all 4 pre-existing
  trades were preserved and 5 new tables plus 7 new `trades` columns added.
- `/api/health` previously hardcoded `Scanner -> NOT_IMPLEMENTED`. It now
  derives scanner health from `app_events`, so it reports UNKNOWN ("has not
  run against this ledger yet") or HEALTHY with the last scan's timestamp.

### Verification actually performed
- **160 tests pass** via `.venv\Scripts\python.exe -m pytest tests\`
  (119 before + 20 scanner + 21 dashboard API).
- Started the real server (`serve --port 8077`) and issued live HTTP requests
  with curl: `/api/status`, `/api/health`, `/api/overview`, `/api/trades`,
  `/api/positions`, `/api/risk` all returned 200, the index page rendered the
  `PAPER TRADING — NO REAL ORDERS` banner, and the 4 migrated trades were
  returned correctly.
- Ran `scan` three ways: clean scan (NO_TRADE, exit 0), stale-data scan
  (REJECTED_STALE on every symbol with warnings, exit 1), and confirmed the
  ledger recorded the scan event.
- **I did not visually inspect the dashboard in a browser** — no browser
  tooling was available in this environment. HTML was verified by asserting
  on the served markup, not by looking at a rendered page. Layout/visual
  regressions would not have been caught.

### Safety properties now covered by tests
- No dashboard route accepts POST/PUT/PATCH/DELETE.
- No route path contains place/submit/execute/buy/sell/cancel/modify.
- `/api/status` reports `live_orders_possible: false`.
- The read-only connection raises on `DELETE FROM trades`.
- SQL injection in table names and sort columns is rejected, and the trades
  table is verified intact afterwards.
- Missing, empty, and outdated-schema ledgers degrade without a 500.

### Deviation from CLAUDE.md section 4
CLAUDE.md suggests Streamlit. The dashboard here is FastAPI + server-rendered
HTML, which was already substantially built when this milestone started.
Rewriting ~2000 working, read-only, test-covered lines in Streamlit would
have been churn for no user benefit. The requirement that actually matters —
"keep core trading logic independent of the UI" — holds: the dashboard only
reads the SQLite ledger and imports no strategy or broker code.

### Known gaps carried into Milestone 4
- Scanner portfolio is in-memory per process; open positions are NOT restored
  on restart, so repeated single-shot scans each start flat.
- Live scan fills at the latest close (no "next bar" exists yet), which is
  more optimistic than the backtest's next-bar-open model. Scan and backtest
  output are therefore not directly comparable.
- Idempotency is per-process only; a fresh process could re-act on a bar a
  previous process already handled.
- No scheduler, no retry/backoff across invocations, nothing detects a
  missed scan. Telegram alerts and live data providers remain unbuilt.
- `.gitignore` gained `*.db-wal` / `*.db-shm`, needed now that the ledger
  runs in WAL mode.

No live order placement exists or is possible.
