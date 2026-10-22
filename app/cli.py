"""Command-line entry point.

    python -m app.cli backtest --symbols SAMPLE_TCS,SAMPLE_INFY

PAPER TRADING — NO REAL ORDERS. This program cannot place a real order: it
has no broker integration and makes no network calls.
"""

from __future__ import annotations

import argparse
import csv
import uuid
from dataclasses import asdict
from pathlib import Path

from app.backtest.engine import run_backtest
from app.config import AppConfig
from app.data.csv_provider import CsvProvider
from app.models import OrderStatus
from app.storage.sqlite import SqliteRepository
from app.strategies.swing import SwingMAStrategy

BANNER = "=" * 64 + "\n  PAPER TRADING — NO REAL ORDERS (simulated fills only)\n" + "=" * 64

DEFAULT_SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"


def cmd_backtest(args: argparse.Namespace) -> int:
    print(BANNER)

    config = AppConfig()
    if args.starting_cash is not None:
        config = AppConfig(starting_cash=args.starting_cash)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    provider = CsvProvider(args.data_dir)
    strategy = SwingMAStrategy(config.swing)

    result = run_backtest(
        provider=provider, symbols=symbols, strategy=strategy, config=config,
        start=args.start, end=args.end,
    )

    m = result.metrics
    print(f"\nStrategy      : {strategy.name} (EXPERIMENTAL defaults, not a profit claim)")
    print(f"Symbols       : {', '.join(result.symbols)}")
    print(f"Data sources  : {result.source_summary}")
    print(f"Interval      : {result.interval}")
    print(f"Date range    : {args.start or 'all'} .. {args.end or 'all'}")
    print(f"\nSignals evaluated : {len(result.signals)}")
    print(f"Trades closed     : {m.trade_count}")
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
        for trade in result.trades:
            repo.save_trade(trade)
        repo.save_run_summary(
            run_id=run_id, symbols=result.symbols, strategy_name=strategy.name,
            starting_cash=m.starting_cash, final_equity=m.final_equity,
            metrics_dict=asdict(m),
        )
        print(f"\nPersisted {len(result.trades)} trade(s) to {args.db} (run_id={run_id})")
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
                    t.net_pnl, t.exit_reason,
                ])
        print(f"Exported trades to {export_path}")

    print("\nReminder: simulated fills do not guarantee real-world fills or prices.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="smart-trader",
        description="PAPER TRADING ONLY — simulated fills, no real orders.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    bt = sub.add_parser("backtest", help="Run a causal backtest on CSV data")
    bt.add_argument("--symbols", default="SAMPLE_TCS,SAMPLE_INFY")
    bt.add_argument("--data-dir", default=str(DEFAULT_SAMPLES_DIR))
    bt.add_argument("--start", default=None, help="YYYY-MM-DD inclusive")
    bt.add_argument("--end", default=None, help="YYYY-MM-DD inclusive")
    bt.add_argument("--starting-cash", type=float, default=None)
    bt.add_argument("--db", default=None, help="SQLite path to persist the ledger")
    bt.add_argument("--export-csv", default=None, help="Write closed trades to this CSV")
    bt.set_defaults(func=cmd_backtest)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
