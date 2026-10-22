"""Command-line entry point.

    python -m app.cli backtest --symbols SAMPLE_TCS,SAMPLE_INFY

PAPER TRADING — NO REAL ORDERS. This program cannot place a real order: it
has no broker integration and makes no network calls.
"""

from __future__ import annotations

import argparse
import csv
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.backtest.engine import BacktestResult, run_backtest
from app.backtest.evaluation import (
    build_comparison,
    can_declare_winner,
    run_in_out_of_sample,
    run_segmented,
)
from app.config import AppConfig
from app.data.csv_provider import CsvProvider
from app.market_calendar import MarketSession, load_holidays
from app.paper.broker import PaperBroker
from app.scanner.scanner import ScanOutcome, Scanner
from app.scanner.universe import InvalidWatchlist, Watchlist
from app.storage.persist import persist_run
from app.storage.sqlite import RESET_CONFIRMATION_TOKEN, SqliteRepository
from app.strategies.intraday import IntradayMomentumStrategy
from app.strategies.swing import SwingMAStrategy

BANNER = "=" * 64 + "\n  PAPER TRADING — NO REAL ORDERS (simulated fills only)\n" + "=" * 64

IST = ZoneInfo("Asia/Kolkata")

DEFAULT_SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"

DISCLAIMER = (
    "\nReminder: simulated fills do not guarantee real-world fills or prices.\n"
    "Sample data under data/samples/ is SYNTHETIC and says nothing about real markets."
)


def _config_from_args(args: argparse.Namespace) -> AppConfig:
    if getattr(args, "starting_cash", None) is not None:
        return AppConfig(starting_cash=args.starting_cash)
    return AppConfig()


def _build_session(args: argparse.Namespace) -> MarketSession | None:
    """Intraday runs get session rules (entry cutoff + forced EOD flatten).
    Swing runs on daily bars get none, so positions are held across days."""

    if getattr(args, "strategy", "swing") != "intraday":
        return None
    holidays = load_holidays(args.holidays) if getattr(args, "holidays", None) else frozenset()
    return MarketSession(holidays=holidays)


def _strategy_factory(args: argparse.Namespace, config: AppConfig):
    if getattr(args, "strategy", "swing") == "intraday":
        session = _build_session(args)
        return lambda: IntradayMomentumStrategy(config.intraday, session=session)
    return lambda: SwingMAStrategy(config.swing)


def _strategy_params(args: argparse.Namespace, config: AppConfig) -> dict:
    if getattr(args, "strategy", "swing") == "intraday":
        return config.intraday.model_dump()
    return config.swing.model_dump()


def cmd_backtest(args: argparse.Namespace) -> int:
    print(BANNER)

    config = _config_from_args(args)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    provider = CsvProvider(args.data_dir)
    strategy = _strategy_factory(args, config)()

    result = run_backtest(
        provider=provider, symbols=symbols, strategy=strategy, config=config,
        start=args.start, end=args.end,
        interval=getattr(args, "interval", "1d"),
        session=_build_session(args),
    )

    m = result.metrics
    print(f"\nStrategy      : {strategy.name} (EXPERIMENTAL defaults, not a profit claim)")
    print(f"Symbols       : {', '.join(result.symbols)}")
    print(f"Data sources  : {result.source_summary}")
    print(f"Interval      : {result.interval}")
    print(f"Date range    : {args.start or 'all'} .. {args.end or 'all'}")
    print(f"\nSignals evaluated : {len(result.signals)}")
    print(f"Trades closed     : {m.trade_count}")
    print(f"Signals expired   : {result.expired_signal_count} (no next bar / bad fill bar)")
    if result.ambiguous_trade_count:
        print(
            f"AMBIGUOUS exits   : {result.ambiguous_trade_count} "
            "(stop & target both hit in one bar; conservative outcome assumed)"
        )
    print(f"Starting cash     : Rs {m.starting_cash:,.2f}")
    print(f"Final equity      : Rs {m.final_equity:,.2f}")
    print(f"Gross P&L         : Rs {m.gross_pnl:,.2f}")
    print(f"Total costs       : Rs {m.total_costs:,.2f}  (UNVERIFIED rate assumptions)")
    print(f"Net P&L           : Rs {m.net_pnl:,.2f}")
    print(f"Return (net)      : {m.total_return_net_pct:.2%}")
    print(f"Return (gross)    : {m.total_return_gross_pct:.2%}")
    print(f"Max drawdown      : {m.max_drawdown_pct:.2%}")
    if m.win_rate is not None:
        print(f"Win rate          : {m.win_rate:.1%} ({m.win_count}W / {m.loss_count}L)")
        print(f"Avg win / loss    : {m.avg_win} / {m.avg_loss}")
        print(f"Expectancy/trade  : Rs {m.expectancy:,.2f}" if m.expectancy is not None else "")
    if m.benchmark_return_pct is not None:
        print(f"Benchmark (B&H)   : {m.benchmark_return_pct:.2%} (equal split, costs ignored)")

    if result.reconciliation_problems:
        print("\nRECONCILIATION PROBLEMS:")
        for problem in result.reconciliation_problems:
            print(f"  - {problem}")
    else:
        print("\nLedger reconciliation: OK")

    if args.db:
        repo = SqliteRepository(args.db)
        run_id = str(uuid.uuid4())
        counts = persist_run(
            repo=repo, run_id=run_id, result=result, config=config,
            strategy_name=strategy.name,
            strategy_params=_strategy_params(args, config),
        )
        print(f"\nPersisted to {args.db} (run_id={run_id}):")
        for name, count in counts.items():
            print(f"  {name:<18}: {count}")
        print(f"Total trades stored in ledger: {repo.trade_count()}")
        repo.close()

    if args.export_csv:
        export_path = Path(args.export_csv)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        with export_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "trade_id", "symbol", "entry_at", "exit_at", "quantity",
                "entry_price", "exit_price", "gross_pnl", "costs", "net_pnl", "exit_reason",
            ])
            for t in result.trades:
                writer.writerow([
                    t.trade_id, t.symbol, t.entry_at.isoformat(), t.exit_at.isoformat(),
                    t.quantity, t.entry_price, t.exit_price, t.gross_pnl, t.costs,
                    t.net_pnl, t.exit_reason, t.ambiguous_exit,
                ])
        print(f"Exported trades to {export_path}")

    print(DISCLAIMER)
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    """Out-of-sample / segmented evaluation.

    Exists because a single headline backtest number is close to meaningless.
    CLAUDE.md section 8 requires this before any performance conclusion.
    """

    print(BANNER)
    config = _config_from_args(args)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    common = dict(
        provider=CsvProvider(args.data_dir), symbols=symbols,
        strategy_factory=_strategy_factory(args, config), config=config,
        interval=getattr(args, "interval", "1d"), session=_build_session(args),
        start=args.start, end=args.end,
    )

    if args.mode == "oos":
        report = run_in_out_of_sample(**common, split_fraction=args.split_fraction)
        print(f"\nOut-of-sample evaluation — {report.strategy_name}")
    else:
        report = run_segmented(**common, num_windows=args.windows)
        print(f"\nSegmented (walk-forward-STYLE) evaluation — {report.strategy_name}")

    print(f"Symbols: {', '.join(symbols)}   Interval: {common['interval']}\n")
    header = f"{'segment':<22}{'dates':<26}{'trades':>7}{'net ret':>10}{'max dd':>9}"
    print(header)
    print("-" * len(header))
    for seg in report.segments:
        dates = f"{seg.start} .. {seg.end}"
        flag = "  <- too short to warm up" if seg.warmup_starved else ""
        print(
            f"{seg.label:<22}{dates:<26}{seg.trade_count:>7}"
            f"{seg.net_return_pct:>9.2%}{seg.result.metrics.max_drawdown_pct:>9.2%}{flag}"
        )

    print(f"\nTotal trades across segments : {report.total_trades}")
    print(
        f"Usable segments              : {len(report.usable_segments)}/{len(report.segments)}"
    )
    print(f"Segments net positive        : {report.segments_profitable}/{len(report.segments)}")

    if report.is_inconclusive:
        print("\nINCONCLUSIVE: this run did not actually test the strategy. See the")
        print("caveats below for the specific reason.")
    elif report.is_consistent:
        print("\nEvery usable segment was net positive — read the caveats below before")
        print("treating that as evidence of anything.")
    else:
        print("\nNOT consistent across segments: performance varies by period. That is")
        print("the normal outcome, and a reason not to trust a single headline number.")

    print("\nCaveats (these limit what the table above can mean):")
    for caveat in report.caveats:
        print(f"  - {caveat}")
    print(DISCLAIMER)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Compares strategies on NET return and refuses to rank them when the
    evidence is too thin (CLAUDE.md section 17: never pick a winner on gross
    return alone)."""

    print(BANNER)
    config = _config_from_args(args)
    provider = CsvProvider(args.data_dir)
    results: dict[str, BacktestResult] = {}

    if args.swing_symbols:
        syms = [s.strip().upper() for s in args.swing_symbols.split(",") if s.strip()]
        strategy = SwingMAStrategy(config.swing)
        results[strategy.name] = run_backtest(
            provider=provider, symbols=syms, strategy=strategy, config=config,
            interval="1d", session=None,
        )

    if args.intraday_symbols:
        syms = [s.strip().upper() for s in args.intraday_symbols.split(",") if s.strip()]
        session = MarketSession()
        strategy = IntradayMomentumStrategy(config.intraday, session=session)
        results[strategy.name] = run_backtest(
            provider=provider, symbols=syms, strategy=strategy, config=config,
            interval=args.intraday_interval, session=session,
        )

    if not results:
        print("\nNothing to compare: pass --swing-symbols and/or --intraday-symbols.")
        return 2

    rows, caveats = build_comparison(results)

    print("\nStrategy comparison — NET first, because net is what a trader keeps\n")
    header = (
        f"{'strategy':<24}{'net ret':>9}{'gross':>9}{'trades':>8}"
        f"{'win%':>7}{'max dd':>9}{'costs':>11}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        win = f"{row.win_rate:.0%}" if row.win_rate is not None else "n/a"
        print(
            f"{row.strategy_name:<24}{row.net_return_pct:>8.2%}{row.gross_return_pct:>9.2%}"
            f"{row.trade_count:>8}{win:>7}{row.max_drawdown_pct:>9.2%}{row.total_costs:>11,.2f}"
        )

    ok, reason = can_declare_winner(rows)
    print("\nVerdict:")
    if ok:
        print(f"  {reason}")
    else:
        print(f"  NO WINNER DECLARED — {reason}.")
        print("  Refusing to rank these is the honest reading of this data.")

    print("\nCaveats:")
    for caveat in caveats:
        print(f"  - {caveat}")
    print(DISCLAIMER)
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    """Runs one watchlist scan (or a bounded series of them).

    Single-shot by default. CLAUDE.md section 10 says not to require a
    continuously running process outside market hours, so scheduling is left
    to the OS (Task Scheduler / cron) rather than a daemon living in here.
    """

    print(BANNER)
    config = _config_from_args(args)

    try:
        watchlist = (
            Watchlist.from_csv(args.watchlist)
            if args.watchlist
            else Watchlist.from_list(args.symbols.split(","))
        )
    except InvalidWatchlist as exc:
        print(f"\nInvalid watchlist: {exc}")
        return 2

    session = _build_session(args)
    strategy = _strategy_factory(args, config)()
    broker = PaperBroker(config)
    scanner = Scanner(
        provider=CsvProvider(args.data_dir),
        strategy=strategy,
        broker=broker,
        watchlist=watchlist,
        session=session,
        interval=args.interval,
        max_bar_age=timedelta(minutes=args.max_bar_age_minutes),
    )

    repo = SqliteRepository(args.db) if args.db else None
    print(f"\nWatchlist   : {', '.join(watchlist)} ({len(watchlist)} symbols)")
    print(f"Strategy    : {strategy.name}")
    print(f"Session     : {'enforced' if session else 'none (daily/swing mode)'}")
    print(f"Max bar age : {args.max_bar_age_minutes} minutes")
    print(
        "\nNOTE: the scanner's virtual cash and open positions live in memory for\n"
        "this process only. Closed trades are written to the ledger, but open\n"
        "positions are NOT restored on restart (see docs/RISK_AND_LIMITATIONS.md)."
    )

    exit_code = 0
    for scan_number in range(1, args.repeat + 1):
        now = datetime.now(tz=IST) if args.now is None else datetime.fromisoformat(args.now)
        report = scanner.scan_once(now)

        print(f"\n--- scan {scan_number}/{args.repeat} at {report.scanned_at.isoformat()} ---")
        for r in report.results:
            bar_ts = r.bar_timestamp.isoformat() if r.bar_timestamp else "-"
            print(f"  {r.symbol:<22}{r.outcome.value:<20}{bar_ts}")
            print(f"      {r.detail}")

        if report.warnings:
            exit_code = 1
            print("\n  WARNINGS:")
            for warning in report.warnings:
                print(f"    ! {warning}")

        if repo is not None:
            for order in report.orders_created:
                repo.save_order(order)
            for trade in broker.portfolio.trades:
                repo.save_trade(trade)
            for warning in report.warnings:
                repo.log_event(
                    severity="WARNING", component="scanner", message=warning,
                    detail={"scanned_at": report.scanned_at.isoformat()},
                )
            repo.log_event(
                severity="INFO", component="scanner",
                message=f"scan completed: {len(report.results)} symbols",
                detail={
                    "traded": report.count(ScanOutcome.TRADED),
                    "no_trade": report.count(ScanOutcome.NO_TRADE),
                    "skipped_session": report.count(ScanOutcome.SKIPPED_SESSION),
                    "rejected_stale": report.count(ScanOutcome.REJECTED_STALE),
                    "errors": report.count(ScanOutcome.ERROR),
                },
            )

        if scan_number < args.repeat:
            time.sleep(args.every)

    if repo is not None:
        print(f"\nLedger: {args.db} (total trades stored: {repo.trade_count()})")
        repo.close()

    print(DISCLAIMER)
    return exit_code


def cmd_reset(args: argparse.Namespace) -> int:
    """Deletes all simulated history. Interactive and explicit by design:
    CLAUDE.md section 9 requires a reset to be confirmed, never silent."""

    print(BANNER)
    repo = SqliteRepository(args.db)
    counts = repo.table_counts()
    print(f"\nLedger: {Path(args.db).resolve()}")
    for name, count in counts.items():
        print(f"  {name:<18}: {count}")

    if sum(v for k, v in counts.items() if k != "app_events") == 0:
        print("\nNothing to delete; the ledger is already empty.")
        repo.close()
        return 0

    print(
        "\nThis permanently deletes the simulated trades, orders, signals,\n"
        "positions and equity history above. It cannot be undone. Back up\n"
        "the file first by copying it:\n"
        f"  Copy-Item '{args.db}' '{args.db}.bak'"
    )
    if not args.yes:
        print(f"\nType exactly: {RESET_CONFIRMATION_TOKEN}")
        try:
            typed = input("> ").strip()
        except EOFError:
            typed = ""
        if typed != RESET_CONFIRMATION_TOKEN:
            print("Confirmation did not match. Nothing was deleted.")
            repo.close()
            return 1

    deleted = repo.reset_ledger(RESET_CONFIRMATION_TOKEN)
    print("\nDeleted:")
    for name, count in deleted.items():
        if name != "app_events":
            print(f"  {name:<18}: {count}")
    print("\nThe app_events table is kept, so the reset itself stays on record.")
    repo.close()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Starts the read-only local dashboard."""

    print(BANNER)
    try:
        import uvicorn
    except ImportError:
        print(
            "\nuvicorn is not installed. Install the dashboard dependencies:\n"
            "  pip install -r requirements.txt"
        )
        return 1

    from app.api.main import create_app

    db_path = Path(args.db).resolve()
    print(f"\nLedger       : {db_path}")
    if not db_path.exists():
        print(
            "WARNING: that ledger file does not exist yet. The dashboard will\n"
            "         start and show empty states. Create it with:\n"
            "           python -m app.cli backtest --db data\\paper_trading.db"
        )
    else:
        # The dashboard opens the ledger read-only and therefore cannot migrate
        # it. A ledger written by an older build is missing tables/columns the
        # dashboard reads, which otherwise surfaces as opaque "could not be
        # read" errors. Opening it for write here applies the ADDITIVE-ONLY
        # migration (ALTER TABLE ADD COLUMN / CREATE TABLE IF NOT EXISTS), which
        # never drops or rewrites existing rows.
        try:
            repo = SqliteRepository(db_path)
            trade_count = repo.trade_count()
            repo.close()
            print(f"Schema       : up to date ({trade_count} trades on record)")
        except Exception as exc:  # noqa: BLE001
            print(
                f"WARNING: could not open the ledger for a schema check: {exc}\n"
                "         The dashboard will start but may show errors. Back up\n"
                f"         the file before investigating:  Copy-Item '{args.db}' '{args.db}.bak'"
            )
    print(f"Dashboard    : http://{args.host}:{args.port}")
    print("Access       : read-only; bound to localhost only by default.")
    print("\nPress Ctrl+C to stop.\n")

    uvicorn.run(
        create_app(db_path=str(db_path), samples_dir=args.data_dir),
        host=args.host, port=args.port, log_level="info",
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="smart-trader",
        description="PAPER TRADING ONLY — simulated fills, no real orders.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_shared_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--data-dir", default=str(DEFAULT_SAMPLES_DIR))
        p.add_argument("--start", default=None, help="YYYY-MM-DD inclusive")
        p.add_argument("--end", default=None, help="YYYY-MM-DD inclusive")
        p.add_argument("--starting-cash", type=float, default=None)
        p.add_argument(
            "--strategy", choices=["swing", "intraday"], default="swing",
            help="swing = daily bars, held across days; intraday = session rules "
                 "(entry cutoff) plus forced end-of-day flattening",
        )
        p.add_argument("--interval", default="1d", help="bar interval label, e.g. 1d or 5m")
        p.add_argument(
            "--holidays", default=None,
            help="CSV with a 'date' column of market holidays. Without it, only "
                 "weekends are treated as non-trading days.",
        )

    bt = sub.add_parser("backtest", help="Run a causal backtest on CSV data")
    bt.add_argument("--symbols", default="SAMPLE_TCS,SAMPLE_INFY")
    add_shared_args(bt)
    bt.add_argument("--db", default=None, help="SQLite path to persist the ledger")
    bt.add_argument("--export-csv", default=None, help="Write closed trades to this CSV")
    bt.set_defaults(func=cmd_backtest)

    ev = sub.add_parser(
        "evaluate",
        help="Out-of-sample or segmented evaluation across multiple time periods",
    )
    ev.add_argument("--symbols", default="SAMPLE_TCS")
    add_shared_args(ev)
    ev.add_argument(
        "--mode", choices=["oos", "segmented"], default="oos",
        help="oos = single chronological split; segmented = N contiguous windows",
    )
    ev.add_argument("--split-fraction", type=float, default=0.7)
    ev.add_argument("--windows", type=int, default=4)
    ev.set_defaults(func=cmd_evaluate)

    cm = sub.add_parser("compare", help="Compare swing vs intraday on net return, honestly")
    cm.add_argument("--swing-symbols", default="SAMPLE_TCS,SAMPLE_INFY")
    cm.add_argument("--intraday-symbols", default="SAMPLE_INTRADAY_TCS")
    cm.add_argument("--intraday-interval", default="5m")
    cm.add_argument("--data-dir", default=str(DEFAULT_SAMPLES_DIR))
    cm.add_argument("--starting-cash", type=float, default=None)
    cm.set_defaults(func=cmd_compare)

    sc = sub.add_parser("scan", help="Run one watchlist scan (session- and staleness-aware)")
    sc.add_argument("--symbols", default="SAMPLE_TCS,SAMPLE_INFY")
    sc.add_argument(
        "--watchlist", default=None,
        help="CSV with a 'symbol' column; overrides --symbols",
    )
    add_shared_args(sc)
    sc.add_argument("--db", default=None, help="SQLite path to record orders and events")
    sc.add_argument(
        "--max-bar-age-minutes", type=int, default=7200,
        help="Refuse to trade if the newest bar is older than this. Default 7200 "
             "(5 days) suits daily bars; use ~15 for 5-minute intraday scanning.",
    )
    sc.add_argument("--repeat", type=int, default=1, help="Number of scans to run")
    sc.add_argument(
        "--every", type=int, default=60,
        help="Seconds between scans when --repeat > 1 (respect provider rate limits)",
    )
    sc.add_argument(
        "--now", default=None,
        help="ISO timestamp to treat as 'now' (for testing against sample data)",
    )
    sc.set_defaults(func=cmd_scan)

    rs = sub.add_parser("reset", help="Delete all simulated history (asks for confirmation)")
    rs.add_argument("--db", default="data/paper_trading.db")
    rs.add_argument(
        "--yes", action="store_true",
        help="Skip the interactive prompt. Intended for scripted use; still destructive.",
    )
    rs.set_defaults(func=cmd_reset)

    sv = sub.add_parser("serve", help="Start the read-only local dashboard")
    sv.add_argument("--db", default="data/paper_trading.db")
    sv.add_argument("--data-dir", default=str(DEFAULT_SAMPLES_DIR))
    sv.add_argument(
        "--host", default="127.0.0.1",
        help="Default 127.0.0.1 (localhost only). Changing this exposes the "
             "dashboard on your network; it has no authentication.",
    )
    sv.add_argument("--port", type=int, default=8000)
    sv.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
