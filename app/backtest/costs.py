"""Transaction cost model.

All rates come from app.config.CostAssumptions, which are explicitly marked
as unverified default assumptions (see that module's docstring). This module
only does the arithmetic; it does not claim the rates are current official
values.
"""

from __future__ import annotations

from app.config import CostAssumptions
from app.models import SignalAction


def compute_slippage(price: float, side: SignalAction, slippage_rate: float) -> float:
    """Returns the slipped fill price: buys fill worse (higher), sells fill
    worse (lower) — slippage always works against the trader, never for."""

    if side == SignalAction.BUY:
        return price * (1 + slippage_rate)
    if side == SignalAction.SELL:
        return price * (1 - slippage_rate)
    raise ValueError(f"slippage not defined for side {side}")


def compute_order_costs(
    price: float,
    quantity: int,
    side: SignalAction,
    costs: CostAssumptions,
) -> float:
    """Returns total cost (brokerage + STT + exchange txn charge + GST +
    stamp duty, as applicable) for one order leg. Costs are always >= 0 and
    always reduce the trader's P&L, on both buy and sell legs where the
    corresponding charge applies."""

    turnover = price * quantity
    total = costs.brokerage_per_order
    exchange_txn_charge = turnover * costs.exchange_txn_rate
    total += exchange_txn_charge
    total += exchange_txn_charge * costs.gst_rate  # GST applies on exchange charges + brokerage
    total += costs.brokerage_per_order * costs.gst_rate

    if side == SignalAction.SELL:
        total += turnover * costs.stt_sell_rate
    elif side == SignalAction.BUY:
        total += turnover * costs.stamp_duty_buy_rate
    else:
        raise ValueError(f"costs not defined for side {side}")

    return round(total, 2)
