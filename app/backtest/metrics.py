"""Backtest performance metrics.

Reports net and gross separately per CLAUDE.md section 8. Metrics here are
descriptive statistics of one historical run on one dataset — not a
prediction of live performance, and not a claim that any strategy is
profitable.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import Trade


@dataclass
class Metrics:
    trade_count: int
    win_count: int
    loss_count: int
    win_rate: float | None
    avg_win: float | None
    avg_loss: float | None
    expectancy: float | None
    gross_pnl: float
    net_pnl: float
    total_costs: float
    starting_cash: float
    final_equity: float
    total_return_net_pct: float
    total_return_gross_pct: float
    max_drawdown_pct: float
    benchmark_return_pct: float | None


def compute_equity_curve_drawdown(equity_curve: list[float]) -> float:
    """Max peak-to-trough drawdown as a positive fraction (e.g. 0.12 = 12%).
    Returns 0.0 for an empty or single-point curve."""

    if len(equity_curve) < 2:
        return 0.0

    peak = equity_curve[0]
    max_dd = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        if peak > 0:
            dd = (peak - value) / peak
            max_dd = max(max_dd, dd)
    return max_dd


def compute_metrics(
    trades: list[Trade],
    starting_cash: float,
    final_equity: float,
    equity_curve: list[float],
    benchmark_return_pct: float | None = None,
) -> Metrics:
    trade_count = len(trades)
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    win_count, loss_count = len(wins), len(losses)

    win_rate = win_count / trade_count if trade_count else None
    avg_win = sum(t.net_pnl for t in wins) / win_count if win_count else None
    avg_loss = sum(t.net_pnl for t in losses) / loss_count if loss_count else None

    expectancy = None
    if trade_count and win_rate is not None:
        loss_rate = 1 - win_rate
        expectancy = (win_rate * (avg_win or 0)) + (loss_rate * (avg_loss or 0))

    gross_pnl = sum(t.gross_pnl for t in trades)
    net_pnl = sum(t.net_pnl for t in trades)
    total_costs = sum(t.costs for t in trades)

    total_return_net_pct = (final_equity - starting_cash) / starting_cash if starting_cash else 0.0
    gross_final_equity = final_equity + total_costs
    total_return_gross_pct = (
        (gross_final_equity - starting_cash) / starting_cash if starting_cash else 0.0
    )

    max_dd = compute_equity_curve_drawdown(equity_curve)

    return Metrics(
        trade_count=trade_count,
        win_count=win_count,
        loss_count=loss_count,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        expectancy=expectancy,
        gross_pnl=round(gross_pnl, 2),
        net_pnl=round(net_pnl, 2),
        total_costs=round(total_costs, 2),
        starting_cash=starting_cash,
        final_equity=round(final_equity, 2),
        total_return_net_pct=total_return_net_pct,
        total_return_gross_pct=total_return_gross_pct,
        max_drawdown_pct=max_dd,
        benchmark_return_pct=benchmark_return_pct,
    )
