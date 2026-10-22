# Architecture — Milestone 0

Status: **proposal, not yet implemented**. This document describes the target
shape of the system. Only the pieces listed under "Milestone 1 slice" below
will actually be built next; everything else is intentionally deferred.

## Guiding constraints (from CLAUDE.md)

- Paper trading only. No code path may place a live order in v1.
- Strategy code must not depend on the UI, a specific data provider, or a
  specific broker. All of those are swappable adapters behind interfaces.
- Backtest and paper-fill logic must be causally correct: a decision at time
  T may only use data available at or before T.
- The system must run end-to-end on bundled sample CSVs with no network
  access, no signup, and no paid service.

## Module responsibilities

```
app/config.py        Typed, validated app configuration (pydantic/dataclass).
                      Fails fast with a clear message on invalid config.

app/models.py         Shared value objects: Bar, Signal, Order, Position,
                      Trade — used across strategies, backtest, and paper
                      broker so they share one definition of "what a bar is".

app/data/             DataProvider interface + adapters.
  base.py              Abstract interface: get_bars(), get_latest_quote().
                        Every bar carries source, retrieved_at, interval,
                        and data-quality flags.
  csv_provider.py      Reads user-supplied CSV files. Runs full validation
                        (section below) before handing bars to a strategy.
  free_provider.py      Placeholder only in M1 — not implemented until a
                        provider is verified against current terms (see
                        docs/DATA_SOURCES.md). No network calls in M1.
  validation.py        Timestamp/timezone checks, OHLC consistency, gap and
                        duplicate detection, stale-data detection.

app/strategies/       Pure functions/classes: (bars, config) -> Signal.
  base.py               Strategy interface returning a Signal with an
                        explanation string and the inputs that produced it,
                        or NO_TRADE.
  intraday.py           Milestone 2.
  swing.py               Milestone 1 candidate (see "first strategy" below).

app/backtest/          Event-driven replay: walks bars in time order, calls
                      the strategy only with bars up to "now", simulates
                      fills through paper/broker.py, and never looks ahead.
  engine.py
  metrics.py             Win rate, expectancy, drawdown, exposure, turnover —
                        reported net and gross separately.
  costs.py               Configurable brokerage/STT/GST/slippage model,
                        explicit about which values are verified vs assumed.

app/paper/              Virtual broker, independent of backtest vs scanner.
  broker.py              Order lifecycle (submitted -> filled/rejected/
                        cancelled), unique order/signal IDs, idempotency.
  portfolio.py           Cash, positions, avg price, realized/unrealized P&L.
  risk.py                 Position sizing, per-trade risk, max exposure, max
                        daily loss, max open positions, kill switch.

app/scanner/            Milestone 3. Watchlist iteration + scheduling hook.
app/alerts/              Milestone 4. Telegram adapter, disabled by default.
app/storage/             SQLite persistence: repository interface + sqlite
                        implementation, so paper/backtest code never writes
                        raw SQL directly.
app/dashboard/           Milestone 3. Streamlit UI reading the same
                        repository/broker interfaces used by tests and CLI.
app/services/            Health checks, scheduler loop (market-hours aware).
```

## Data flow (Milestone 1)

```
sample/user CSV --> csv_provider --> validation --> strategies/swing.py
                                                         |
                                                         v
                                                  backtest/engine.py
                                                         |
                                        paper/broker.py (simulated fills)
                                                         |
                                                 paper/portfolio.py (ledger)
                                                         |
                                            storage/sqlite.py (persistence)
```

No dashboard, scanner, or alerting in this slice — those are later
milestones per CLAUDE.md section 17.

## First strategy: swing, not intraday

Milestone 1 will implement the **swing** prototype (daily bars) rather than
the intraday one first:

- Daily bars sidestep intraday session-boundary logic (entry cutoff windows,
  forced end-of-day exit, stale-quote handling), which adds real complexity
  the causal-backtest engine would otherwise need from day one.
- A simple, documented-as-experimental moving-average trend filter with a
  configurable holding period is enough to validate the whole pipeline
  (provider -> validation -> strategy -> backtest -> paper broker ->
  ledger -> SQLite) end-to-end.
- Intraday-specific session logic is added in Milestone 2 once the causal
  backtest core is proven correct against known small datasets.

This is a proposal; see the open question in the plan below.

## Why SQLite, why no server

Matches CLAUDE.md section 4 directly: local file-based persistence, no
database server, no Kubernetes, no paid cloud service in v1. SQLite gives
restart-safe ledger persistence with zero extra moving parts on Windows.

## Testing shape

`pytest` against the modules above, with no network calls (CLAUDE.md
section 13). The backtest and paper broker are the highest-risk modules for
subtle bugs (look-ahead bias, double fills), so they get the first and most
detailed tests.
