"""Writes a completed BacktestResult into the SQLite ledger.

Separated from app/cli.py so the signal -> order -> trade linkage can be
tested directly, and separated from app/backtest/engine.py so persistence
cannot influence a trading decision. This module only reads a finished
result; it never calls a strategy, a broker, or a risk check.

PAPER TRADING — NO REAL ORDERS. Everything stored here is simulated.
"""

from __future__ import annotations

from app.backtest.engine import BacktestResult
from app.config import AppConfig
from app.models import Order, OrderStatus, SignalAction
from app.storage.sqlite import SqliteRepository

# When several orders share one signal_id (the broker records a rejected
# duplicate alongside the original), the signal's recorded outcome should be
# the decisive one, not whichever happened to be appended last.
_STATUS_PRECEDENCE = {
    OrderStatus.FILLED: 4,
    OrderStatus.PARTIALLY_FILLED: 3,
    OrderStatus.REJECTED: 2,
    OrderStatus.CANCELLED: 1,
    OrderStatus.SUBMITTED: 0,
}

# How an order's terminal status maps onto the signal-level outcome shown in
# the dashboard's Signals page.
_OUTCOME_BY_STATUS = {
    OrderStatus.FILLED: "FILLED",
    OrderStatus.PARTIALLY_FILLED: "PARTIALLY_FILLED",
    OrderStatus.REJECTED: "REJECTED",
    OrderStatus.CANCELLED: "EXPIRED",
    OrderStatus.SUBMITTED: "SUBMITTED",
}


def _decisive_order(orders: list[Order]) -> Order:
    return max(orders, key=lambda o: _STATUS_PRECEDENCE.get(o.status, -1))


def persist_run(
    repo: SqliteRepository,
    run_id: str,
    result: BacktestResult,
    config: AppConfig,
    strategy_name: str,
    strategy_params: dict | None = None,
) -> dict[str, int]:
    """Stores every record from one backtest run and returns row counts.

    Recorded here, in one transaction-per-table batch:
      - every signal, INCLUDING NO_TRADE ones (the audit trail for why the
        system did not trade), with its resulting order/trade where one exists
      - every simulated order, linked to its run and trade
      - every closed trade, attributed to this run
      - positions still open at the end, marked with the last price the
        provider actually supplied (never a fabricated price)
      - the equity curve
      - the run summary, with the date range, interval, data sources, strategy
        parameters, cost assumptions and risk limits that produced it
    """

    # order_id -> trade_id, for both legs of every closed round-trip.
    trade_by_order: dict[str, str] = {}
    for trade in result.trades:
        trade_by_order[trade.entry_order_id] = trade.trade_id
        trade_by_order[trade.exit_order_id] = trade.trade_id

    orders_by_signal: dict[str, list[Order]] = {}
    for order in result.orders:
        orders_by_signal.setdefault(order.signal_id, []).append(order)

    for trade in result.trades:
        repo.save_trade(trade, run_id=run_id, strategy_name=strategy_name)

    for order in result.orders:
        repo.save_order(order, run_id=run_id, trade_id=trade_by_order.get(order.order_id))

    signal_rows = []
    for signal in result.signals:
        linked = orders_by_signal.get(signal.signal_id)
        if not linked:
            # No order exists. For NO_TRADE that is the expected, correct
            # outcome; for an actionable signal it means the engine never
            # queued it (e.g. the session entry window had closed).
            outcome = "NO_TRADE" if signal.action == SignalAction.NO_TRADE else "NOT_QUEUED"
            signal_rows.append((signal, run_id, outcome, None, None))
            continue
        order = _decisive_order(linked)
        signal_rows.append((
            signal,
            run_id,
            _OUTCOME_BY_STATUS.get(order.status, order.status.value),
            order.order_id,
            trade_by_order.get(order.order_id),
        ))
    signal_count = repo.save_signals(signal_rows)

    for position in result.open_positions:
        last_price = result.final_prices.get(position.symbol)
        repo.save_open_position(
            position,
            run_id=run_id,
            last_price=last_price,
            last_price_source=result.source_summary.get(position.symbol),
        )

    equity_rows = repo.save_equity_curve(
        run_id,
        [(ts, equity, cash, count) for ts, equity, cash, count in result.equity_points],
    )

    metrics = result.metrics
    repo.save_run_summary(
        run_id=run_id,
        symbols=result.symbols,
        strategy_name=strategy_name,
        starting_cash=metrics.starting_cash,
        final_equity=metrics.final_equity,
        metrics_dict=_metrics_to_dict(metrics),
        interval=result.interval,
        start_date=result.start_date.isoformat() if result.start_date else None,
        end_date=result.end_date.isoformat() if result.end_date else None,
        data_sources=result.source_summary,
        params=strategy_params or {},
        costs=config.costs.model_dump(),
        risk=config.risk.model_dump(),
        reconciliation_problems=result.reconciliation_problems,
        signal_count=len(result.signals),
        expired_signal_count=result.expired_signal_count,
        ambiguous_trade_count=result.ambiguous_trade_count,
    )

    counts = {
        "trades": len(result.trades),
        "orders": len(result.orders),
        "signals": signal_count,
        "open_positions": len(result.open_positions),
        "equity_snapshots": equity_rows,
    }

    repo.log_event(
        severity="INFO",
        component="backtest",
        message=f"persisted backtest run for {', '.join(result.symbols)}",
        run_id=run_id,
        detail=counts,
    )
    if result.reconciliation_problems:
        repo.log_event(
            severity="ERROR",
            component="portfolio",
            message="ledger reconciliation reported problems",
            run_id=run_id,
            detail={"problems": result.reconciliation_problems},
        )

    return counts


def _metrics_to_dict(metrics) -> dict:
    """dataclasses.asdict equivalent that tolerates the Metrics dataclass
    gaining fields, so persistence does not need updating in lockstep."""

    from dataclasses import asdict, is_dataclass

    if is_dataclass(metrics):
        return asdict(metrics)
    return dict(metrics)
