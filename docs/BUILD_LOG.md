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

## 2026-10-23 — Dashboard persistence, correctness fixes, and tests

Follow-up on Milestone 3. The dashboard existed but half its pages had nothing
to read, and two real defects were found by testing it rather than by reading it.

### Persistence the dashboard needed (and the engine was discarding)
Before this, only `trades` and `runs` were written: the `orders` table had a
schema and zero rows, and signals, open positions and the equity curve existed
only in memory for the duration of a run.

- `app/storage/sqlite.py` — additive-only migration (`ALTER TABLE ADD COLUMN`,
  `CREATE TABLE IF NOT EXISTS`); new `signals`, `positions`, `equity_snapshots`
  and `app_events` tables; `run_id` attribution on trades/orders; per-run
  `params_json`/`costs_json`/`risk_json`/date range/interval/data sources, which
  CLAUDE.md section 8 requires alongside every result; `open_readonly()`;
  `reset_ledger()` gated on an exact confirmation token.
- `app/storage/persist.py` — new. Writes a finished `BacktestResult`, linking
  signal → order → trade. Reads a result only; it can never affect a decision.
- `app/backtest/engine.py` — exposes `equity_points`, `open_positions` and
  `final_prices` instead of discarding them, and routes engine-initiated exits
  (stop / target / EOD flatten) through a closure that also records them in the
  signal log. Without that, a stop-loss exit produced an order whose signal_id
  had no signal row — an order with no explanation.
- `app/cli.py` — `backtest --db` now persists everything; new `reset` (interactive
  confirmation, keeps the `app_events` audit trail) and `serve` commands.

Measured on the bundled samples: 3 trades, 6 orders, 362 signals (356 NO_TRADE),
360 equity snapshots. Orders went from 0 rows to 6.

### Two real defects found by testing

1. **IST dates were shifted a day earlier.** Date filters used SQLite's
   `date()`, which parses an ISO8601 string with an offset and converts it to
   UTC: `date('2024-01-10T00:00:00+05:30')` is `2024-01-09`. Every bar in this
   app is IST-stamped and daily bars sit at midnight, so a trade dated the 10th
   fell outside a range starting on the 10th — rows silently missing from the
   user's view. Now compares the first 10 characters of the stored timestamp.
   Regression test: `test_midnight_ist_timestamp_is_not_shifted_into_the_previous_day`.

2. **Intermittent 503s on random pages.** FastAPI runs a sync generator
   dependency and the endpoint consuming it on *different* threadpool threads,
   so the per-request connection was created on one thread and used on another;
   sqlite3's same-thread guard raised `ProgrammingError`, surfacing as "The
   ledger could not be read" on whichever pages happened to land elsewhere.
   Fixed with `check_same_thread=False` (safe: one connection per request, used
   sequentially, never shared) plus a `busy_timeout`. Regression test:
   `test_readonly_connection_is_usable_from_another_thread`.

Also fixed: the SQLite error handler told the reader to "check the server log"
but never wrote one. It now logs the exception and traceback server-side while
still returning a generic message to the browser.

### Charge profile
`Order`/`Trade` gained a `charge_profile` (delivery vs intraday) in the cost
rework. It is now persisted, and `app/api/derive.py` re-prices a leg under the
profile it was actually charged under. A row recorded before that column existed
has none, so the API returns `charge_profile_assumed: true` and the Orders page
labels that cost "assumed" in amber rather than "derived" — delivery and
intraday differ on STT, stamp duty and DP charges.

### Verification actually performed
- `python -m pytest tests/` — **231 passed, 0 failed** (final run, 2026-10-23).
  Mid-work this sat at 207 passed / 4 failed: `tests/test_costs.py` and
  `tests/test_fill_and_exit_model.py` were briefly red because those scenarios
  predate the cost-viability gate and had their entries correctly rejected as
  uneconomic. Both were resolved in the cost-model work itself.
  Two tests in `test_persistence.py` were updated for the same reason: they now
  use a stop 5 below entry (`VIABLE_STOP`), which sizes an order large enough to
  clear its own round-trip charges, so a test that needs a real fill gets one.
- Dashboard, storage and persistence tests in isolation: **77 passed**
  (`test_persistence.py`, `test_dashboard_queries.py`, `test_dashboard_api.py`,
  `test_storage.py`).
- Migration run against the real ledger: 4 pre-existing trades preserved, new
  columns added, idempotent across repeated opens. The ledger now holds 7 trades
  (4 legacy with NULL `run_id`, 3 from a new run), which exercises mixed
  old/new rows in every view.
- Read-only enforcement: `DELETE`/`DROP` on the dashboard's connection raise;
  `mode=ro` refuses to create a missing file.
- Hostile input: unknown/injected table names, injected sort columns and
  `' OR 1=1 --` searches are rejected or match nothing; trades table verified
  intact afterwards. POST/PUT/DELETE on real endpoints return 405.
- All 10 pages rendered against the live server under a DOM harness across
  repeated runs: no runtime errors, no `undefined`/`NaN`/`[object Object]` in
  output, zero database errors logged.
- Tailwind is vendored to `app/api/static/vendor/` (451 KB), so the dashboard
  needs no internet connection and makes no external request.

### Honesty properties checked explicitly
Sample data labelled `SAMPLE_SYNTHETIC`; provider reports `streaming: false`;
Telegram and live data feed report `NOT_IMPLEMENTED`; scanner reports `UNKNOWN`
until it has actually run; out-of-sample declared unavailable; per-day P&L
reported as not recorded rather than 0; an open position with no price reports
unknown rather than zero unrealised P&L; derived values labelled as derived.

### Known gaps
- Re-running the same backtest still appends duplicate logical trades. `run_id`
  makes them distinguishable; it does not deduplicate them.
- No per-day P&L is persisted, so the max-daily-loss limit is enforced during a
  run but cannot be shown afterwards.
- Risk settings and the kill switch are read-only in the dashboard. There is no
  long-running trading process for a toggle to affect, so a control would be
  theatre; change `app/config.py` and re-run.
- `data/old_schema_backup.db` is a stray local backup file (gitignored).

No live order placement exists or is possible.

## 2026-10-23 — Real NSE data: UDiFF bhavcopy provider (session smart-trader-42)

Context: this entry covers only the stock-selection workstream. Three other
Claude sessions were editing this repository concurrently; file ownership was
negotiated between them and the split is recorded below.

### Audit first (no code)
Audited the repository before changing anything. Findings that drove the work:
- **No stock-selection logic existed.** `Watchlist` is a validated symbol list
  with no liquidity or tradability filtering; `Scanner` evaluates one strategy
  per symbol with no ranking, so capital goes to whichever symbol sorts first
  rather than to the best setup.
- **All bundled data is synthetic** — a seeded random walk from
  `scripts/generate_sample_data.py`. No strategy can show an edge on a random
  walk, so the Milestone-2 intraday result of −0.71% net measured the cost
  model, not a strategy.
- Missing for the requested pipeline: index/sector series (regime, relative
  strength), breadth, spread/depth, circuit and halt status, corporate-action
  adjustment, point-in-time membership, and any news/event source.

### Correctness fixes
- `app/strategies/base.py` — the `Strategy.evaluate()` ABC declared
  `evaluate(self, bars)` while both implementations and every caller passed
  `position`. Signature corrected before new strategies are written against it.
- `app/paper/portfolio.py` — `existing_exposure_value()` now marks open
  positions to the latest available price, falling back to average entry price
  only where no mark exists. Previously a position that had moved up could grow
  past the max-exposure limit unnoticed.
- `app/paper/risk.py` — passes current prices into that check.
- `docs/RISK_AND_LIMITATIONS.md` item 5 described the old behaviour and was
  made false by the above; updated to FIXED, naming the residual weakness (the
  entry-price fallback still applies when no mark is available).

### New: `app/data/bhavcopy_provider.py`
Real NSE end-of-day data from files the **user downloads manually** from
nseindia.com. The app never fetches and contains no code that could: CLAUDE.md
section 6 forbids scraping NSE, and `docs/DATA_SOURCES.md` already established
that NSE blocks automation and restricts redistribution. Reads `.csv` or the
`.csv.zip` as served.

**Format verified, not guessed.** Column names were read from a real
2025-12-30 CM-UDiFF file. NSE replaced the legacy bhavcopy with UDiFF effective
2024-07-08 (circular 62424 of 2024-06-12), renaming every column — `SYMBOL` to
`TckrSymb`, `TOTTRDQTY` to `TtlTradgVol`. Most online guides still show the
dead names. A legacy file is **rejected with an actionable error** rather than
parsed on a guess, because a wrong guess silently misreads prices. Consequence:
history starts 2024-07-08.

Design notes:
- A bhavcopy is cross-sectional (one date, all symbols) while `DataProvider` is
  per-symbol, so the provider pivots and caches; `refresh()` picks up new files.
- Filters to `FinInstrmTp=STK` and series `EQ` by default. The same file carries
  gold bonds and other instruments that must not be treated as shares. `BE`
  (trade-for-trade) is opt-in, not mixed in by default.
- `get_latest_quote()` returns `None`. A bhavcopy is published after the close;
  serving its last row as a quote would misrepresent EOD data as live.
- `get_bars(interval="5m")` raises rather than silently returning daily bars.
- Exposes `LiquidityRecord` (`TtlTrfVal` rupee turnover, `TtlNbOfTxsExctd`
  trade count) as the tradability-filter input. Kept **out** of `Bar` so no
  strategy can silently depend on a field CSV sources cannot supply. A row with
  blank turnover produces **no** record rather than a zero — zero would make a
  liquid stock look untradeable to a filter.

**Prices are UNADJUSTED for corporate actions.** A split or bonus appears as a
cliff; `validate_series` flags it `SUSPECT_GAP`, which is a symptom, not a fix.
A backtest spanning a split will read the gap as a crash.

### Test results (actually run)
`tests/test_bhavcopy_provider.py` — **23 passed**. Full suite **307 passed**
(the count includes concurrent work by the other three sessions). Fixtures use
the real 34-column UDiFF header; a fixture inventing a convenient layout would
pass while the parser failed on the only input that matters. One fixture bug
was caught by `validate_series` during development — hand-picked OHLC constants
produced a close outside [low, high] — and the helper now derives consistent
OHLC from the close.

### Concurrent-session file ownership (negotiated, not assumed)
- smart-trader-08: `app/scanner/`, Milestones 1–3, commit `9b49750`.
- smart-trader-34: `app/api/*`, `app/storage/*`, dashboard tests.
- smart-trader-11: cost model and viability — `app/config.py`,
  `app/backtest/costs.py`, `app/paper/viability.py`, `app/models.py`,
  `app/paper/broker.py`, `app/strategies/*`.
- this session: `app/data/*` and (next) `app/selection/*`.

Agreed seam for the universe concept: `app/selection/` performs regime
detection, filtering, discovery and ranking, and emits an eligible set that the
existing `Watchlist`/`Scanner` consumes. No second "eligible universe" concept.

### Not done
yfinance adapter (approved as opt-in, off by default, not yet written);
`app/selection/*` (regime, filters, ranking); wiring the provider into the
dashboard's Market Data page. Nothing here is committed.

No live order placement exists or is possible.

## 2026-10-23 — Cost-aware trade viability and API rate-limit budgeting

Requested by the user: a small trade (20 shares at ₹19, sold at ₹19.05) earns
₹1 gross and pays far more than that in brokerage, STT, DP, exchange, SEBI and
GST charges, so the system should take charges into account when deciding
whether a trade is worth taking at all. Second request: factor in the Groww
Trading API limits.

Full detail, including the provenance of every rate, is in
`docs/COSTS_AND_VIABILITY.md`.

### State found before this work
The **uncommitted working tree** already described a "cost-viability gate" and
`Order`/`Trade` charge profiles in this log, and `tests/test_persistence.py`
already used a `VIABLE_STOP` constant — while the code implemented none of it:
`app/backtest/costs.py` had a single flat `brokerage_per_order` with no DP
charge, SEBI fee or IPFT, there was no viability module, and nothing
distinguished delivery from intraday. The storage and API-derive changes for
charge profiles WERE present, also uncommitted. So the description ran ahead of
the implementation; that gap is now closed by implementing the feature rather
than by deleting the description.

Correction, for the record: an earlier draft of this entry blamed commit
9b49750 for describing a feature it did not implement. That was wrong, and
`smart-trader-08` caught it. `git grep -i viab HEAD` returns nothing, so no
commit ever made that claim — the text came from a parallel session's
uncommitted edit to this file, made while this work was in progress.
Committed history was accurate throughout.
Two other Claude sessions were editing this working tree concurrently
(`smart-trader-42` on data providers and stock selection, `smart-trader-08` on
portfolio persistence); file ownership was agreed by message and no peer edits
were reverted.

### Charge model (`app/backtest/costs.py`, `app/config.py`)
- Brokerage is now `clamp(pct × turnover, floor, cap)`, so a ₹380 order pays
  the ₹5 per-order minimum rather than ₹0.38. This single change is most of
  why small trades now correctly fail.
- Added the charges the old model omitted entirely: flat DP charge on a
  delivery sell, SEBI turnover fee, IPFT, and STT on the delivery BUY leg.
- Split rates into `ProfileCharges` for delivery vs intraday; shared rates
  (exchange, SEBI, IPFT, GST, slippage) stay at the top level.
- `compute_order_cost_breakdown()` returns every charge itemised; components
  are rounded to paise and the total is their exact sum, so a displayed
  breakdown adds up.
- `breakeven_exit_price()` solves the exit price that nets zero by fixed-point
  iteration against the real cost function (that function has a floor/cap kink
  and per-paise rounding, so a closed form would drift out of step), rounding
  UP to the next paise so the answer truly clears costs.
- A `model_validator` migrates the old flat schema, including stored
  `runs.costs_json`, so historical runs keep the numbers they ran under.

### Viability gate (`app/paper/viability.py`)
- `assess_trade()` prices a proposed round trip and returns `TradeEconomics`:
  itemised entry/exit charges, round-trip cost, cost as a share of turnover,
  gross and net P&L at target, net loss at stop, breakeven price and move,
  reward-to-cost, net reward-to-risk, a verdict and a reason.
- Enforced in `PaperBroker._process_buy` — the single place a signal becomes a
  position — so the backtest and the scanner both inherit it. A failure is a
  rejected order with a reason, never a silent resize.
- Checks are judged on the post-slippage fill price, not the signal clean
  close. `minimum_viable_quantity()` adds "would need at least N shares" to a
  rejection, or `None` when no size can work.
- Economics are retained for rejected entries too (`broker.trade_economics`),
  so a refusal is auditable.
- `ChargeProfile` is declared by the strategy (intraday vs swing), stamped on
  the signal, carried by the position, and recorded on order and trade — so the
  exit leg is costed under the profile the entry was opened with. This also
  populates the `charge_profile` columns the existing storage layer was writing
  as NULL.

### Two design errors the tests caught, and the decisions made
- `min_net_reward_to_risk` was defaulted to 1.0. That is unsatisfiable for a
  gross 1:1 setup once charges exist (charges cut the reward and add to the
  risk), so it would have silently outlawed every symmetric strategy, including
  sound high-win-rate ones. It is a view about trade shape, not cost
  arithmetic, and is **now off by default** with the reasoning recorded in
  `app/config.py`. The capability remains, and remains tested.
- `assess_trade()` crashed on a zero-share position (formatting `None` as
  currency). Zero size now returns early: there is nothing to price, and
  reporting per-position figures for a zero-share trade would be noise.

### API rate-limit budgeting (`app/services/rate_limits.py`)
Planning only. No network call, no credential, no endpoint — CLAUDE.md §16
forbids live integration in v1 and this does not approach it.
- `GrowwApiLimits` carries the published per-category limits with
  `verified=False` and a `source` string, because the official docs page was
  NOT read directly (the fetch was cancelled) and third-party sources
  disagreed. `plan_scan()` appends that caveat to every plan it returns.
- `plan_scan()` answers "does this watchlist at this interval fit?" and gives
  both remedies when it does not (longer interval, or fewer symbols). It checks
  the per-second ceiling separately, because a per-minute average hides bursts.
- `RateLimitBudget` is a sliding-window gate that refuses rather than sleeps,
  with `now` passed in for reproducibility. The scanner consults it before each
  provider call and reports unaffordable symbols as `SKIPPED_RATE_LIMIT` with a
  retry-after, surfaced in `ScanReport.warnings` because a skipped symbol means
  the scan was incomplete. This implements the anti-burst rule in §10.

### Verification actually performed
- `.venv/Scripts/python.exe -m pytest -q` — **306 passed, 1 failed**. The
  single failure is `tests/test_bhavcopy_provider.py::test_bars_are_validated`,
  in another session new, untracked provider (a bar-quality assertion); it is
  unrelated to this work and was left alone.
- My own files in isolation — **all green**: `tests/test_costs.py` 27 passed,
  `tests/test_viability.py` 22 passed, `tests/test_rate_limits.py` 25 passed,
  `tests/test_scanner.py` 26 passed, `tests/test_fill_and_exit_model.py` 10
  passed.
- Mid-work the suite was red in two places, both the gate working correctly:
  `test_costs.py` asserted against the removed flat schema (rewritten), and two
  fill-model tests had their synthetic ₹2,000 orders rejected as uneconomic.
  `base_config()` in `test_fill_and_exit_model.py` now disables the gate
  explicitly, with the reason in its docstring, because those tests are about
  fill mechanics.
- Worked example re-run end to end and reported in
  `docs/COSTS_AND_VIABILITY.md`: the ₹19.00 → ₹19.05 trade is refused, breakeven
  ₹20.72 delivery / ₹19.60 intraday.
- Verified the gate is economics-based, not price-based: the same ₹19 stock
  fills when the target is wide enough to pay the charges
  (`test_gate_judges_economics_not_share_price`).

### Known gaps
- **No charge rate and no API limit here is verified.** Groww charge pages
  returned 404/JS-only and the API docs page was not read directly. The
  modelled structure is the durable part; the numbers are placeholders.
  `verified=False` and the module docstrings say so in code.
- Slippage is still a flat percentage, which is the weakest cost assumption in
  the repo. The bhavcopy provider added by `smart-trader-42` now exposes
  per-symbol-day traded value and trade counts (`get_liquidity()`), which would
  support a size- and liquidity-aware slippage estimate. Not attempted here.
- DP charge is applied once per sell leg, not per scrip per day. The paper
  broker exits in one order, so this does not currently arise.
- The gate prices the planned trade (entry/stop/target). A trade that exits for
  some other reason pays whatever that exit actually costs.
- `plan_scan` is not wired into the dashboard or CLI; it is library-only so far.

No live order placement exists or is possible.

## 2026-10-23 — Market Data sources and separating the safety claims

Prompted by a coordination exchange with the session building the selection
pipeline: a real NSE bhavcopy provider had landed but the dashboard could not
see it, and an opt-in network data source is planned, which would make a claim
in the README, `/api/status` and the UI footer false.

### Two claims, deliberately not merged
The old wording bundled "no broker integration, makes no network calls, and
cannot place a real order" into one sentence. Those are different kinds of
claim and conflating them weakens the one that matters:

- **Unconditional**: no broker integration, no broker credentials or API keys,
  no code path that can place, modify or cancel a real order. True regardless
  of configuration. This is now stated on its own and first.
- **Conditional on configuration**: every data source shipped today is
  file-based and makes no network call. If an optional network source is added
  and enabled, that stops being true for that install — and the guarantee above
  still holds.

Changed in README.md (banner), `/api/status` (`data_provider.network_calls`,
with a comment explaining why it is scoped to configured sources rather than
asserted permanently) and the UI footer (two separate paragraphs).

Verified at the time of writing: `grep` for network imports across `app/` finds
none, and `BhavcopyProvider` reads only local files — it documents that it
"deliberately contains no code that could" fetch. So the network claim is still
accurate today; it was not pre-emptively weakened for a feature that does not
exist yet.

### NSE bhavcopy surfaced on the Market Data page
`/api/marketdata/*` previously constructed `CsvProvider` directly, so the
bhavcopy provider was invisible. Now:

- New `/api/marketdata/sources` lists both sources, whether each is configured,
  and per-source caveats. An unconfigured bhavcopy directory returns
  instructions rather than an error.
- `watchlist` and `bars/{symbol}` take `?source=csv|bhavcopy`. An unknown
  source is a 400; asking for bhavcopy when it is not configured is a 404 with
  the flag to pass; a legacy-format file or an intraday interval returns the
  provider's own explanation, not a 500.
- `python -m app.cli serve --bhavcopy-dir <folder>`.
- Providers are constructed per request, because `BhavcopyProvider` caches every
  parsed file in memory and a long-lived instance would keep serving a stale
  cache after the user drops in a new day's file.

### A third data_kind, because two would have been wrong
`_data_kind` returned only `SAMPLE_SYNTHETIC` or `HISTORICAL_CSV`. Bhavcopy data
is neither: it is real exchange-published data, not generated, and not merely
"a CSV the user supplied". It is now `NSE_BHAVCOPY_EOD`, and the UI gives each
kind its own tone and an explanation of what that data is *not* — synthetic in
amber (acting on it would be a mistake), bhavcopy in blue (real but unadjusted
for corporate actions, and end-of-day only). `live`, `delayed` and `real-time`
remain impossible return values, and the bars response reports
`quote_available: false` for bhavcopy because `get_latest_quote()` returns None
by design.

### Verification actually performed
- `tests/test_dashboard_marketdata.py` — new, **20 tests, all passing**,
  including a synthetic UDiFF bhavcopy fixture (both `.csv` and `.csv.zip`, as
  NSE serves them) parsed through the real provider. Tests assert that real
  bhavcopy data is never labelled synthetic, that no `data_kind` can ever claim
  live/delayed/real-time, that the caveats state what the data is not, and that
  the footer keeps the two claims separate.
- `python -m pytest tests/` — **331 passed, 0 failed, 6.14s**. One earlier run
  of the same suite took 123s; `--durations=15` showed the slowest test at
  0.11s, so that was contention from several sessions working the repo at once,
  not a regression.
- Live server with 18 generated bhavcopy files: sources endpoint reports both
  providers and `network_calls: false`; the watchlist pivots 18 per-date files
  into 3 symbols labelled `NSE_BHAVCOPY_EOD`; bars return 18 validated bars with
  `quote_available: false`. All 10 pages rendered with no runtime errors and no
  database errors logged.

No live order placement exists or is possible.

## 2026-10-23 — Scanner persistence and broker-refusal reporting (smart-trader-08)

Closes two documented Milestone-3 scanner limitations and one bug.

**Bug fixed — a refused order was reported as `TRADED`.** `Scanner` mapped any
non-None order to `ScanOutcome.TRADED`, including orders the broker had
REJECTED (kill switch, risk limit, cash, and now the cost-viability gate). The
operator would have read "TRADED" for a trade that never happened. Added
`ScanOutcome.REJECTED_BROKER`, included in `ScanReport.warnings`; `TRADED` now
means the order was actually FILLED. Regression test:
`test_broker_refusal_is_reported_as_rejected_broker_not_traded`.

**Scanner portfolio persistence.** `scan --db <ledger>` saves virtual cash,
open positions, realized P&L and the max-daily-loss baseline after every scan
and resumes from them on the next invocation.
- New `app/storage/scanner_state.py` (`ScannerStateStore`) with three
  scanner-only tables, created with `IF NOT EXISTS` and never dropped. It does
  NOT edit `app/storage/sqlite.py`, per the agreement with the other sessions.
  Kept separate from the backtest `positions` table (which means "open at the
  end of run X") so backtest leftovers never read as the scanner's holdings.
- `Portfolio.export_state()` / `Portfolio.from_state()` plus
  `realized_pnl_carried_forward`, so `reconcile()` still balances when closed
  trades are not reloaded.
- `charge_profile` is persisted and restored per position; a restored
  INTRADAY position that came back as DELIVERY would have its first exit
  charged under the wrong schedule (requested by smart-trader-11).
- Cross-process idempotency: processed `(symbol, bar)` pairs are saved, so a
  restarted scan reports `SKIPPED_DUPLICATE` instead of re-trading a bar.
- `reset` now also clears the scanner tables. Without that, a "reset" would
  have erased history while the scanner kept trading from the old cash and
  positions.
- The snapshot save is a single transaction (all-or-nothing); verified by a
  test that injects a mid-save failure.

**Verification actually run:** full suite 345 passed
(`.venv\Scripts\python.exe -m pytest tests\`), including 14 new tests in
`tests/test_scanner_persistence.py`, two of which drive the real CLI in
separate subprocesses (resume across processes; `reset` erases scanner state
and the next scan starts fresh).

**Not verified / residual:**
- The CLI subprocess tests use sample data on which the swing strategy takes no
  trade, so an *open position* surviving a real two-process run is covered by
  unit tests (restart simulated in-process) rather than by the CLI test.
- Not crash-atomic across steps: orders/trades are written, then the snapshot.
  A process killed between them leaves the two out of step and nothing
  detects it.
- No locking: two simultaneous `scan` processes on one ledger, last save wins.
- A bar is marked processed even on NO_TRADE, so changing strategy parameters
  does not re-evaluate already-seen bars until `reset`.

**Provenance note (additive; no history was rewritten).** Commits `9759572`
and `9b49750` were staged with `git add -A` while other sessions had
uncommitted work on disk, so they contain code this session did not write: in
`9b49750`, `app/api/main.py`, `app/api/static/*` (~2,970 of 4,243 insertions);
in `9759572`, `app/api/queries.py`, `app/api/derive.py`,
`app/storage/persist.py`, the `app/storage/sqlite.py` expansion and most of the
`app/backtest/engine.py` change (~1,600 insertions). Authorship of those
files is not reflected by the commit author. Figures are from `git show
--stat`.

No live order placement exists or is possible.

## 2026-10-23 — Strategy research and pre-check gate

Requested: web research on the best swing/intraday strategies, added as
pre-checks, favouring the most winning ones.

- Researched via web search (secondary sources/abstracts only; no primary paper
  read in full). Findings and links in `docs/STRATEGY_RESEARCH.md`.
- **Did not rank by win rate**: no verified "most winning" strategy exists
  (SEBI: 71% of individual NSE cash intraday traders lost money in FY23), and
  win rate does not measure profit. Candidates are ranked by evidence quality.
- New `app/prechecks/` (`candidates.py` registry, `gate.py` gate). The gate judges
  net results in-sample and out-of-sample on non-synthetic data.
- `tests/test_prechecks.py` — 10 passed (run in isolation; full suite not re-run).
- Not wired into CLI/scanner; no new strategies implemented; thresholds are
  judgement calls.

No live order placement exists or is possible.

## 2026-10-23 — Single-trade loss hardening

Request: never lose the whole capital on one trade.

Already present: 1% risk-per-trade sizing, 20% max position, 60% max exposure,
3% daily loss limit, kill switch. Reviewing them found real gaps:

- **Gap bug (engine).** A stop was filled at its level even when the bar opened
  below it, understating the loss on exactly the event that causes large single-trade
  losses. Now filled at `min(stop, open)`. Regression test added.
- **Sizing assumed a perfect stop.** New `gap_buffer_pct` (default 1%) is added to
  the stop distance when sizing, so a stop that fills a bit worse still stays within
  the risk budget. Consequence: smaller positions, so small accounts more often fail
  the cost-viability gate. Intended, but it will reduce trade count.
- **No cumulative brake.** The daily limit resets each day. New `max_drawdown_pct`
  (default 10%) halts new entries once equity is that far below its peak.
- `Portfolio.reconcile()` tolerance now scales with trade count (per-trade paise
  rounding drift); the flat 0.01 failed once positions sized differently.
- Two test fixtures pin `gap_buffer_pct=0`, because they test persistence/sizing
  arithmetic rather than risk.

Limits: a gap can still exceed the buffer (overnight news); the stop cannot be
guaranteed. Sizing uses cash, not equity. Drawdown peak is held in memory and
resets on process restart (not persisted). Defaults are judgement calls.
Full suite: see run below.

## 2026-10-23 — Daily-data import, volume analytics, universe report

- `app/data/ingest.py` + `import-bhavcopy` CLI: manual CSV/ZIP import to raw/processed/manifests.
  No automatic NSE download: the existing project policy forbids bypassing NSE access controls and
  I did not verify that NSE terms permit automation, so none was written.
- `app/selection/volume.py`: avg/relative volume, spike, breakout+volume, traded value, zero-volume
  runs, `evaluate_universe` (explains every symbol); `universe` CLI saves a CSV.
- Swing volume filter (off by default) + `compare --volume-variant`; `max_weekly_loss_pct`.
- `tests/test_ingest_and_volume.py`: 13 tests. Full suite: 409 passed.
- Not done: the dashboard capital panel was not changed, universe scans are saved as CSV not SQLite,
  news/events and surveillance flags have no data source, volume-vs-price-only comparison on real
  data awaits imported files (only synthetic samples were run).

## 2026-10-23 — Risk budget calculator

- `app/paper/risk_budget.py`: capital + profile (conservative/moderate/aggressive rules of thumb) ->
  percent and rupee limits, worst case if all positions stop out, implied position sizes, warnings.
- Surfaces: `python -m app.cli risk-budget --capital 100000 --profile moderate`, `GET /api/risk-budget`,
  and a calculator panel on the dashboard Risk page. It changes no setting; profiles are not advice.
- `tests/test_risk_budget.py`: 6 tests. Full suite: 419 passed. The dashboard JS was syntax-checked
  with `node --check` but not exercised in a browser.
