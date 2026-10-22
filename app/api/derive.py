"""Values the dashboard shows that are DERIVED, not stored.

The Order record does not carry the pre-slippage price, the slippage amount,
or its own transaction costs — the broker applies slippage to produce
`filled_price` and rolls costs up onto the Trade. Rather than leave those
columns blank or, worse, invent numbers, this module recomputes them exactly
from two things that ARE recorded: the order's filled price/quantity/side, and
the cost assumptions stored on that order's run (`runs.costs_json`).

The arithmetic is the inverse of app/backtest/costs.compute_slippage and a
direct re-use of compute_order_costs, so there is one source of truth for the
cost model. Everything returned here is flagged `derived: True` and the UI
labels it as derived, because a derived value is only as good as the recorded
assumptions it came from.

If a run has no recorded cost assumptions (an older row written before those
columns existed), these functions return None and the UI shows "not recorded"
instead of a guess.
"""

from __future__ import annotations

import json
from typing import Any

from app.backtest.costs import compute_order_costs
from app.config import CostAssumptions
from app.models import SignalAction


def cost_assumptions_from_run(run_row: dict[str, Any] | None) -> CostAssumptions | None:
    """Parses `runs.costs_json` into a CostAssumptions, or None when the run
    did not record one. Never substitutes the current defaults for a run's
    missing assumptions — that would report today's model as if it had been
    used historically."""

    if not run_row:
        return None
    raw = run_row.get("costs_json")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not data:
        return None
    try:
        return CostAssumptions.model_validate(data)
    except Exception:  # noqa: BLE001 - a malformed stored blob must not 500 the page
        return None


def derive_order_economics(
    order: dict[str, Any], costs: CostAssumptions | None
) -> dict[str, Any]:
    """Returns the derived fill economics for one order.

    Keys:
      fill_price_basis  the pre-slippage price the fill was simulated from
      slippage_per_share  filled_price - basis (positive = paid away)
      slippage_total      slippage_per_share * filled_quantity
      costs               simulated transaction costs for this single leg
      derived             always True
      unavailable_reason  set instead of the numbers when they cannot be derived
    """

    unavailable = {"derived": True, "fill_price_basis": None, "slippage_per_share": None,
                   "slippage_total": None, "costs": None}

    filled_price = order.get("filled_price")
    filled_qty = order.get("filled_quantity") or 0
    side_raw = (order.get("side") or "").upper()

    if filled_price is None or filled_qty <= 0:
        # Rejected, cancelled and expired orders never filled, so there is no
        # slippage or cost to report. That is an accurate zero-information
        # answer, not a missing one.
        return {**unavailable, "unavailable_reason": "order never filled"}
    if costs is None:
        return {**unavailable,
                "unavailable_reason": "run did not record its cost assumptions"}
    if side_raw not in ("BUY", "SELL"):
        return {**unavailable, "unavailable_reason": f"unsupported side {side_raw!r}"}

    side = SignalAction.BUY if side_raw == "BUY" else SignalAction.SELL
    rate = costs.slippage_rate

    # Inverse of compute_slippage: a BUY filled higher than basis, a SELL lower.
    if side == SignalAction.BUY:
        basis = filled_price / (1 + rate) if rate != -1 else None
    else:
        basis = filled_price / (1 - rate) if rate != 1 else None
    if basis is None:
        return {**unavailable, "unavailable_reason": "degenerate slippage rate"}

    per_share = filled_price - basis
    return {
        "derived": True,
        "fill_price_basis": round(basis, 4),
        "slippage_per_share": round(per_share, 4),
        "slippage_total": round(abs(per_share) * filled_qty, 2),
        "costs": compute_order_costs(filled_price, filled_qty, side, costs),
        "unavailable_reason": None,
    }


def position_marks(position: dict[str, Any], portfolio_value: float | None) -> dict[str, Any]:
    """Mark-to-market figures for one open position.

    Returns None-valued marks with a reason when the provider supplied no
    price. Falling back to the entry price would report unrealised P&L of
    exactly zero, which reads as "flat" when the truth is "unknown" — that is
    the difference between a missing price and a safe one.
    """

    quantity = position.get("quantity") or 0
    avg_entry = position.get("avg_entry_price")
    last_price = position.get("last_price")
    cost_basis = (quantity * avg_entry) if avg_entry is not None else None

    if last_price is None:
        return {
            "cost_basis": cost_basis,
            "market_value": None,
            "unrealized_pnl": None,
            "unrealized_pnl_pct": None,
            "exposure_pct": None,
            "price_available": False,
            "price_note": "no price supplied by the data provider for this symbol",
        }

    market_value = quantity * last_price
    unrealized = market_value - (cost_basis or 0.0)
    return {
        "cost_basis": cost_basis,
        "market_value": market_value,
        "unrealized_pnl": unrealized,
        "unrealized_pnl_pct": (unrealized / cost_basis) if cost_basis else None,
        "exposure_pct": (market_value / portfolio_value) if portfolio_value else None,
        "price_available": True,
        "price_note": None,
    }
