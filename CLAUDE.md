# CLAUDE.md — Indian Market Paper-Trading System

## 1. Role and working style

Act as a senior Python engineer, quantitative-research engineer, test engineer, and security-conscious application architect.

Build a reliable **paper-trading and strategy-research application for Indian equities**. The user develops on Windows and may deploy to a cloud service later. The user uses Groww for their existing investments, but the initial version must not require Groww API credentials or a paid API subscription.

Work in small, verifiable milestones. Inspect the existing repository before changing anything. Do not generate a huge codebase in one response and claim it works. After each milestone:
1. Explain what changed and why.
2. List exact commands to run in Windows PowerShell.
3. Run relevant automated tests where possible.
4. Report actual test results honestly.
5. State any remaining limitation or decision.
6. Keep a concise `docs/BUILD_LOG.md` updated.

Do not ask the user to share passwords, OTPs, API secrets, broker tokens, or other credentials in chat. Never commit secrets.

## 2. Product goal

Build a system that scans a configurable list of NSE-listed equities, evaluates intraday and swing-trading setups, simulates entries/exits using virtual money, records all decisions, and can send optional alerts. It must be perfectly acceptable for the system to recommend **no trade** when conditions are poor.

This is a research and paper-trading tool, not a promise of profit. No strategy may be described as profitable unless the evidence supports that statement and limitations are clearly explained.

## 3. Hard safety boundaries

- Paper trading only in the initial version.
- **Do not implement, call, or enable live order placement.** No live-order endpoint calls, no live-order buttons, and no code path that can accidentally submit real orders.
- Do not request or store Groww credentials in version 1.
- Do not bypass broker, exchange, data-provider, or regulatory controls.
- Do not assume that paper-trading success predicts live results.
- Add a prominent `PAPER TRADING — NO REAL ORDERS` label in the UI and logs.
- Use a virtual starting balance configurable by the user (default ₹100,000).
- Include position sizing, maximum exposure, daily loss limits, stop-loss simulation, and a kill switch that stops new simulated entries.
- Avoid leverage, derivatives, penny-stock promotion, and high-frequency-trading claims in version 1. Start with cash-equity research.
- Never claim millisecond execution in real markets. Paper fills are simulations and must be labelled as such.

## 4. Initial technical choices

Prefer a small, maintainable Python application:
- Python 3.12 or a currently supported stable Python version available on Windows.
- `pandas` and `numpy` for data analysis.
- `pytest` for tests.
- `pydantic` or dataclasses for validated configuration and models.
- `SQLite` for local persistence in version 1.
- `Streamlit` for a simple local dashboard if it improves usability; keep core trading logic independent of the UI.
- `logging` with rotating log files.
- `python-dotenv` only for local non-secret configuration where appropriate; never commit `.env`.
- Type hints for public functions and clear docstrings for non-obvious logic.
- Pin tested dependencies in `requirements.txt` or a lock file.

Do not introduce a complex web stack, Kubernetes, paid cloud services, or a database server unless a demonstrated requirement justifies it. Do not make cloud deployment a prerequisite for local testing.

## 5. Proposed project structure

Use this as a starting point and adjust only with a clear reason:

    app/
      __init__.py
      config.py
      models.py
      data/
        __init__.py
        base.py
        csv_provider.py
        free_provider.py
        validation.py
      strategies/
        __init__.py
        base.py
        intraday.py
        swing.py
      backtest/
        __init__.py
        engine.py
        metrics.py
        costs.py
      paper/
        __init__.py
        broker.py
        portfolio.py
        risk.py
      scanner/
        __init__.py
        universe.py
        scanner.py
      alerts/
        __init__.py
        base.py
        telegram.py
      storage/
        __init__.py
        repository.py
        sqlite.py
      dashboard/
        __init__.py
        main.py
      services/
        __init__.py
        scheduler.py
        health.py
    tests/
    data/
      samples/
    docs/
      ARCHITECTURE.md
      DATA_SOURCES.md
      STRATEGIES.md
      RISK_AND_LIMITATIONS.md
      WINDOWS_SETUP.md
      BUILD_LOG.md
    .env.example
    .gitignore
    requirements.txt
    README.md
    CLAUDE.md

Do not create empty modules merely to match the tree. Implement working components incrementally.

## 6. Data requirements and provider rules

Create a provider interface so the application can use CSV files first and a lawful data provider later without changing strategy code.

Milestone 1 must work using bundled, clearly labelled sample OHLCV data and user-imported CSV files. Do not make a paid API a requirement.

For all data:
- Validate timestamps, timezone, OHLC consistency, duplicates, missing bars, zero/negative prices, and abnormal gaps.
- Store Indian exchange timestamps consistently using `Asia/Kolkata`.
- Respect market holidays and trading sessions using a configurable calendar; do not hardcode an unverified holiday list.
- Clearly distinguish real historical data, delayed data, sample data, and simulated data.
- Check the current provider terms, redistribution restrictions, API limits, and availability before implementing a provider.
- Do not scrape websites or bypass access controls.
- Do not invent API endpoints or claim a provider is free without verifying current official documentation.
- If no compliant free real-time source is available, say so clearly and keep the app usable with CSV/sample data.
- Record source name, retrieval time, interval, and data-quality warnings for every dataset.
- Never use future data to make a historical decision.

Create a `DataProvider` interface with methods for retrieving historical bars and, optionally, latest quotes. Provider-specific functionality must be isolated behind adapters. A provider that cannot supply real-time streaming must not be represented as real-time.

## 7. Strategies: research prototypes, not promises

Implement strategies as independent modules that produce a signal, explanation, and risk parameters. They must not place real orders.

### A. Intraday prototype
Start with a transparent, configurable, low-complexity strategy such as:
- A trend filter using moving averages.
- A momentum/volume condition.
- A configurable time window for new entries.
- No new entries near the configured market close.
- Optional stop-loss and target rules.
- No overnight positions by default; simulate exit before session end.
- No trade when data is stale, incomplete, or invalid.

Do not assume a particular indicator is profitable. Parameters must be configurable, and defaults must be documented as experimental.

### B. Swing prototype
Start with a transparent daily-bar trend/momentum strategy:
- Trend filter and configurable entry/exit rules.
- Position holding period and maximum open positions configurable.
- Gap risk and stop-loss limitations clearly documented.
- Avoid using today's closing price to generate a signal that pretends to have been tradable earlier that same day.

Do not overfit. Do not optimize parameters against the same data used to report final performance.

### C. No-trade behaviour
The system should return `NO_TRADE` when:
- A signal is weak or contradictory.
- Data is stale or invalid.
- Risk limits would be exceeded.
- The market session is closed.
- A symbol is not in the configured eligible universe.
- The strategy has already generated a duplicate signal for the same bar.

Every signal must include a human-readable explanation and the inputs that caused it.

## 8. Backtesting correctness

Build an event-driven or otherwise causally correct backtest. The backtest must:
- Use only information available at the simulated decision time.
- Avoid look-ahead bias, survivorship bias where feasible, and accidental future leakage.
- Avoid filling an order at a price that was unavailable after the signal.
- Define how market, limit, stop, partial-fill, gap, and end-of-day exits are simulated; keep version 1 simple and document assumptions.
- Include configurable brokerage, taxes, exchange charges, SEBI charges, GST, stamp duty, and slippage estimates where relevant. Verify current values from official sources before presenting them as current. If not verified, make them explicit user-configurable assumptions.
- Never assume every order fills at the candle close without explanation.
- Avoid using the same candle's high/low to imply a known order of stop-loss and target hits. If both are touched within one bar and the order is unknown, use a conservative assumption or mark the outcome ambiguous.
- Report net and gross results separately.
- Compare against a simple benchmark where appropriate.
- Include trade count, win rate, average win/loss, expectancy, total return, maximum drawdown, exposure, turnover, and risk-adjusted metrics only where mathematically meaningful.
- Include out-of-sample and walk-forward evaluation before any performance conclusion.
- Report the date range, symbols, bar interval, data source, costs, and assumptions alongside every result.
- Test for data leakage and edge cases.

## 9. Paper broker and portfolio

Implement a virtual broker with a strict interface separate from strategy code.

Required features:
- Virtual cash, positions, average entry price, realized/unrealized P&L.
- Order lifecycle: submitted, simulated, partially filled if supported, filled, rejected, cancelled.
- Unique order and signal IDs to prevent duplicate processing.
- Configurable slippage and transaction costs.
- Position sizing based on a configured risk budget and stop distance.
- Per-trade risk limit, max position size, max total exposure, max daily loss, and max open positions.
- Persistent ledger so restarting the app does not erase simulated trades.
- Reconciliation checks between cash, positions, orders, and P&L.
- An explicit reset action requiring confirmation; do not erase history silently.

The UI must make clear that simulated fills do not guarantee real-world fills or prices.

## 10. Scanner, scheduling, and notifications

- Start with a small configurable watchlist; do not scan every NSE symbol by default.
- Make the scan interval configurable and respect provider rate limits.
- Use idempotent processing so repeated polling does not create duplicate trades or alerts.
- Use a market-session schedule in `Asia/Kolkata`; do not run equity signal logic outside configured sessions.
- Support an optional Telegram alert adapter, disabled by default.
- Telegram messages should contain symbol, strategy, timestamp, signal reason, simulated entry/stop/target, and a clear paper-trading label.
- Store alerts and failures locally.
- Alert on stale data, provider errors, database failures, and risk-limit triggers.
- A missed scan or network outage must not cause a burst of stale orders after recovery. Revalidate signals against current data before any new simulated entry.
- Do not require a continuously running process outside market hours unless it is performing a clearly documented task.

## 11. UI and user experience

Provide a simple local dashboard with:
- Current mode banner: `PAPER TRADING — NO REAL ORDERS`.
- Data source and last successful update time.
- Watchlist and scan status.
- Current signals with explanations and data timestamps.
- Virtual cash, open positions, realized/unrealized P&L.
- Trade history and downloadable CSV.
- Backtest configuration and results.
- Risk settings and kill switch.
- Visible errors and stale-data warnings.
- No claims such as “guaranteed profit”, “100% accurate”, or “sure-shot calls”.

The core engine must still be usable from tests and command-line commands without the dashboard.

## 12. Reliability and security

- Validate all configuration at startup and fail safely with a useful message.
- Use timeouts and bounded retries for network requests.
- Respect API rate limits and implement backoff.
- Handle market holidays, missing data, network failures, duplicate events, and database errors.
- Do not turn a data error into a buy/sell signal.
- Use structured, timestamped logs; avoid logging secrets or personal data.
- Keep secrets out of source code, Git history, screenshots, and logs.
- Add `.env` and local database files to `.gitignore` where appropriate.
- Do not expose the dashboard publicly by default.
- Add health/status checks and graceful shutdown.
- Provide backup/export instructions for the SQLite database.
- Do not claim the app is fault-tolerant until failure tests demonstrate it.

## 13. Testing requirements

Use `pytest`. Add unit tests for at least:
- OHLCV validation and timestamps.
- Indicator calculations against known small datasets.
- Signal timing and no-look-ahead behaviour.
- Intraday session boundaries and end-of-day exits.
- Swing entry/exit rules.
- Risk sizing and all risk limits.
- Brokerage, tax, and slippage calculations.
- Virtual cash and position accounting.
- Duplicate signal/order prevention.
- Partial/missing/stale data handling.
- Stop-loss and target collision within a candle.
- Restart persistence and ledger reconciliation.
- Market holidays and closed-session behaviour.
- Provider failures and retry limits.
- Telegram disabled/configuration behaviour.
- Regression tests for every bug found.

Tests must not call live broker order endpoints. Network tests should be mocked or explicitly opt-in. Never report tests as passing unless they were actually run.

## 14. Windows developer experience

Provide exact PowerShell commands for:
1. Checking Python and Git versions.
2. Creating and activating a virtual environment.
3. Installing dependencies.
4. Running tests.
5. Loading sample data.
6. Starting a backtest.
7. Starting the dashboard.
8. Stopping the app safely.
9. Exporting paper-trade results.

Prefer commands that work in a normal Windows PowerShell terminal. Explain common errors and fixes. Keep setup simple for a user who is comfortable with a Windows computer but may need step-by-step development instructions.

## 15. Cloud deployment — later milestone only

Do not deploy or create paid cloud resources in the initial milestone.

After local tests and paper simulation are stable, document cloud options and estimate cost before asking the user to create anything. Compare a small VPS with a suitable managed service based on whether the application needs continuous polling, scheduled jobs, persistent storage, and outbound notifications. Verify current official pricing and service limitations before recommending a provider.

Cloud deployment must include:
- Secure secret storage.
- Persistent storage or managed database as appropriate.
- Logs, health monitoring, restart policy, and error alerts.
- Timezone and market-session handling.
- Backup and restore plan.
- Cost limits/budget alerts where available.
- A documented rollback and shutdown procedure.

Do not claim that a scale-to-zero service is suitable for continuous WebSocket streaming without checking its current lifecycle and connection constraints.

## 16. Regulatory and broker integration boundary

Version 1 must not connect to live order placement. Before any future live integration, perform a separate review of current SEBI, NSE/BSE, broker, API, static-IP, authentication, order-rate, and other applicable requirements using official current documentation. Do not infer compliance from a successful API call. Document the findings and ask the user to review the plan; do not enable live execution automatically.

A future broker adapter may be researched separately, but never fabricate API endpoints or credentials. The app must continue to work in paper-only mode if no broker API is configured.

## 17. Milestones and acceptance criteria

### Milestone 0 — Repository and feasibility
- Inspect repository and installed Python.
- Propose a short architecture and identify dependencies.
- Verify data-source options without signing up for paid services.
- Create `docs/ARCHITECTURE.md`, `docs/DATA_SOURCES.md`, and a milestone plan.
- Do not implement a large system yet.

### Milestone 1 — Minimal working paper-trading core
- Load bundled sample OHLCV data and CSV input.
- Validate data.
- Implement one simple strategy and a causal backtest.
- Implement virtual broker, costs, risk limits, SQLite persistence.
- Add tests and Windows instructions.
- Demonstrate an end-to-end run with sample data.

### Milestone 2 — Intraday and swing research
- Add separate intraday and swing prototypes.
- Add metrics, costs, no-trade behaviour, and out-of-sample tests.
- Compare strategies honestly; do not choose a winner based only on gross return.

### Milestone 3 — Scanner and dashboard
- Add configurable watchlist scanning, dashboard, trade history, and CSV export.
- Add stale-data and failure warnings.

### Milestone 4 — Alerts and paper-market observation
- Add optional Telegram notifications.
- Run in paper mode across multiple market sessions.
- Record operational issues and strategy results. Do not assume a fixed number of days proves profitability.

### Milestone 5 — Cloud readiness
- Estimate cloud cost using verified current prices.
- Document deployment and shutdown.
- Deploy only after the user explicitly chooses a provider and understands any charges.

### Milestone 6 — Separate review before any live trading
- Out of scope for this project version.
- Requires fresh regulatory/broker research, independent code review, explicit user approval, and a separate implementation plan.

## 18. Definition of done

A milestone is complete only when:
- The code runs with documented commands.
- Relevant tests have actually run and results are reported.
- There are no known critical failures left undisclosed.
- Data assumptions and limitations are documented.
- No live order placement is possible in this version.
- The README and build log reflect the actual repository state.

## 19. First action for Claude Code

Start with **Milestone 0 only**:
1. Inspect the repository and environment.
2. Summarize the proposed architecture and the smallest useful first version.
3. Verify which data sources can be used lawfully and at what cost, using official documentation where current claims are needed.
4. List assumptions and risks.
5. Create a concise implementation plan with exact acceptance tests.
6. Ask only essential questions that block Milestone 1.
7. Do not install cloud resources, subscribe to APIs, request secrets, or implement live order placement.

Once the user approves the plan, implement Milestone 1 and verify it end-to-end.
