"""Cost-viability gate: refuse trades whose charges eat the profit.

The motivating case, which a percentage-only cost model gets wrong:

    Buy 20 shares at Rs 19.00  = Rs 380 turnover
    Sell 20 shares at Rs 19.05 = Rs 381 turnover
    Gross profit               = Rs 1.00
    Round-trip charges         = tens of rupees (brokerage floor on both
                                 legs + flat DP charge + STT + GST)
    Net result                 = a loss, every time

No amount of strategy accuracy fixes that, because the problem is arithmetic,
not prediction. So this module computes the full economics of a proposed
trade *before* it is filled and returns a verdict with a human-readable
reason, which the paper broker turns into a rejected order and the scanner
reports as a no-trade.

Design notes:

- It evaluates the plan, not the outcome: entry, stop and target as the
  strategy proposed them. A trade that cannot pay for itself even when the
  target is hit cannot be worth taking.
- It is a hard gate, never a silent size adjustment. Like the risk limits in
  risk.py, failing a check means NO_TRADE, not "trade smaller".
- Every threshold is configurable (app.config.ViabilityPolicy) and every
  failure names the number that failed, so a rejection is auditable rather
  than mysterious.
- Costs come from app.backtest.costs, so this module has no cost arithmetic
  of its own to drift out of sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.backtest.costs import (
    CostBreakdown,
    breakeven_exit_price,
    compute_order_cost_breakdown,
)
from app.config import CostAssumptions, ViabilityPolicy
from app.models import ChargeProfile, SignalAction


@dataclass(frozen=True)
class TradeEconomics:
    """Full cost economics of one proposed round trip, with a verdict.

    All P&L figures are per the whole position (not per share), and "net"
    always means after every charge in the cost model on both legs.
    """

    quantity: int
    entry_price: float
    stop_loss: Optional[float]
    target: Optional[float]
    profile: ChargeProfile

    entry_turnover: float
    entry_costs: CostBreakdown
    # Exit-leg charges priced at the target and at the stop. They differ
    # because most charges scale with the exit turnover.
    exit_costs_at_target: Optional[CostBreakdown]
    exit_costs_at_stop: Optional[CostBreakdown]

    # Exit price at which the round trip nets exactly zero.
    breakeven_price: Optional[float]
    # How far price must move from entry just to break even, as a fraction.
    breakeven_move_pct: Optional[float]

    round_trip_costs_at_target: Optional[float]
    gross_profit_at_target: Optional[float]
    net_profit_at_target: Optional[float]
    net_loss_at_stop: Optional[float]

    # Round-trip charges as a fraction of entry turnover.
    cost_pct_of_turnover: Optional[float]
    # Gross profit at target / round-trip charges. Below 1.0 the charges eat
    # the entire move.
    reward_to_cost_ratio: Optional[float]
    # Net profit at target / net loss at stop, both after charges.
    net_reward_to_risk: Optional[float]

    viable: bool
    reason: str
    failed_checks: tuple[str, ...] = ()
    # Checks that could not be evaluated (e.g. no target supplied).
    skipped_checks: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        """Flat, JSON-safe summary for storage, the API and the dashboard."""

        return {
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "target": self.target,
            "profile": self.profile.value,
            "entry_turnover": self.entry_turnover,
            "entry_costs": self.entry_costs.as_dict(),
            "exit_costs_at_target": (
                self.exit_costs_at_target.as_dict() if self.exit_costs_at_target else None
            ),
            "exit_costs_at_stop": (
                self.exit_costs_at_stop.as_dict() if self.exit_costs_at_stop else None
            ),
            "breakeven_price": self.breakeven_price,
            "breakeven_move_pct": self.breakeven_move_pct,
            "round_trip_costs_at_target": self.round_trip_costs_at_target,
            "gross_profit_at_target": self.gross_profit_at_target,
            "net_profit_at_target": self.net_profit_at_target,
            "net_loss_at_stop": self.net_loss_at_stop,
            "cost_pct_of_turnover": self.cost_pct_of_turnover,
            "reward_to_cost_ratio": self.reward_to_cost_ratio,
            "net_reward_to_risk": self.net_reward_to_risk,
            "viable": self.viable,
            "reason": self.reason,
            "failed_checks": list(self.failed_checks),
            "skipped_checks": list(self.skipped_checks),
        }


def assess_trade(
    entry_price: float,
    quantity: int,
    costs: CostAssumptions,
    policy: ViabilityPolicy,
    stop_loss: Optional[float] = None,
    target: Optional[float] = None,
    profile: ChargeProfile = ChargeProfile.DELIVERY,
) -> TradeEconomics:
    """Prices a proposed long round trip and decides whether it can pay for itself.

    Returns a TradeEconomics whose `viable` flag the caller must honour. A
    disabled policy still computes every figure (so the numbers remain
    visible in the UI and the ledger) but always returns viable=True.

    Checks that need a target are skipped — not failed — when no target is
    supplied, and recorded in `skipped_checks`, because a strategy that
    exits on a signal rather than a fixed level has no target to price.
    """

    entry_turnover = round(entry_price * quantity, 2)
    entry_costs = compute_order_cost_breakdown(
        entry_price, quantity, SignalAction.BUY, costs, profile
    )
    breakeven = breakeven_exit_price(entry_price, quantity, costs, profile)
    breakeven_move_pct = (
        (breakeven - entry_price) / entry_price
        if breakeven is not None and entry_price > 0
        else None
    )

    exit_at_target: Optional[CostBreakdown] = None
    exit_at_stop: Optional[CostBreakdown] = None
    round_trip_at_target: Optional[float] = None
    gross_at_target: Optional[float] = None
    net_at_target: Optional[float] = None
    net_loss_at_stop: Optional[float] = None
    cost_pct: Optional[float] = None
    reward_to_cost: Optional[float] = None
    net_rr: Optional[float] = None

    if target is not None and quantity > 0:
        exit_at_target = compute_order_cost_breakdown(
            target, quantity, SignalAction.SELL, costs, profile
        )
        round_trip_at_target = round(entry_costs.total + exit_at_target.total, 2)
        gross_at_target = round((target - entry_price) * quantity, 2)
        net_at_target = round(gross_at_target - round_trip_at_target, 2)
        if entry_turnover > 0:
            cost_pct = round_trip_at_target / entry_turnover
        if round_trip_at_target > 0:
            reward_to_cost = gross_at_target / round_trip_at_target

    if stop_loss is not None and quantity > 0:
        exit_at_stop = compute_order_cost_breakdown(
            stop_loss, quantity, SignalAction.SELL, costs, profile
        )
        gross_loss = (entry_price - stop_loss) * quantity
        # Positive number = rupees lost, charges included.
        net_loss_at_stop = round(gross_loss + entry_costs.total + exit_at_stop.total, 2)
        if net_at_target is not None and net_loss_at_stop > 0:
            net_rr = net_at_target / net_loss_at_stop

    failed: list[str] = []
    skipped: list[str] = []

    if quantity <= 0:
        # Nothing else can be priced: every figure below is per-position, so
        # reporting them for a zero-share "trade" would be noise, not information.
        failed.append("position size is zero, so there is no trade to price")
        skipped.append("no economics priced for a zero-share position")
        return _verdict(
            quantity=quantity, entry_price=entry_price, stop_loss=stop_loss,
            target=target, profile=profile, entry_turnover=entry_turnover,
            entry_costs=entry_costs, exit_at_target=None, exit_at_stop=None,
            breakeven=breakeven, breakeven_move_pct=breakeven_move_pct,
            round_trip_at_target=None, gross_at_target=None, net_at_target=None,
            net_loss_at_stop=None, cost_pct=None, reward_to_cost=None, net_rr=None,
            policy=policy, failed=failed, skipped=skipped,
        )

    if entry_turnover < policy.min_order_value:
        failed.append(
            f"order value Rs {entry_turnover:,.2f} is below the minimum "
            f"Rs {policy.min_order_value:,.2f}: flat per-order charges "
            f"(brokerage floor, DP charge) dominate an order this small"
        )

    if target is None:
        skipped.append(
            "no target price supplied, so reward-based checks could not be evaluated"
        )
    else:
        if policy.require_net_profit_at_target and (net_at_target or 0.0) <= 0:
            failed.append(
                f"net profit at target is Rs {net_at_target:,.2f} after "
                f"Rs {round_trip_at_target:,.2f} of round-trip charges "
                f"(gross Rs {gross_at_target:,.2f}): hitting the target still loses money"
                + (
                    f"; price must reach Rs {breakeven:,.2f} just to break even"
                    if breakeven is not None
                    else ""
                )
            )
        if (
            policy.min_reward_to_cost_ratio > 0
            and reward_to_cost is not None
            and reward_to_cost < policy.min_reward_to_cost_ratio
        ):
            failed.append(
                f"reward-to-cost ratio {reward_to_cost:.2f} is below the minimum "
                f"{policy.min_reward_to_cost_ratio:.2f}: gross profit at target "
                f"Rs {gross_at_target:,.2f} vs charges Rs {round_trip_at_target:,.2f}"
            )
        if (
            cost_pct is not None
            and policy.max_cost_pct_of_turnover > 0
            and cost_pct > policy.max_cost_pct_of_turnover
        ):
            failed.append(
                f"round-trip charges are {cost_pct:.2%} of order value, above the "
                f"{policy.max_cost_pct_of_turnover:.2%} limit"
            )

    if stop_loss is None:
        skipped.append("no stop-loss supplied, so net reward-to-risk could not be evaluated")
    elif net_rr is None:
        if net_at_target is not None:
            skipped.append("net reward-to-risk undefined (no net loss at stop)")
    elif policy.min_net_reward_to_risk > 0 and net_rr < policy.min_net_reward_to_risk:
        failed.append(
            f"net reward-to-risk {net_rr:.2f} is below the minimum "
            f"{policy.min_net_reward_to_risk:.2f}: net Rs {net_at_target:,.2f} at target "
            f"vs net Rs {net_loss_at_stop:,.2f} at stop, charges included"
        )

    return _verdict(
        quantity=quantity, entry_price=entry_price, stop_loss=stop_loss,
        target=target, profile=profile, entry_turnover=entry_turnover,
        entry_costs=entry_costs, exit_at_target=exit_at_target,
        exit_at_stop=exit_at_stop, breakeven=breakeven,
        breakeven_move_pct=breakeven_move_pct,
        round_trip_at_target=round_trip_at_target, gross_at_target=gross_at_target,
        net_at_target=net_at_target, net_loss_at_stop=net_loss_at_stop,
        cost_pct=cost_pct, reward_to_cost=reward_to_cost, net_rr=net_rr,
        policy=policy, failed=failed, skipped=skipped,
    )


def _verdict(
    *,
    quantity: int,
    entry_price: float,
    stop_loss: Optional[float],
    target: Optional[float],
    profile: ChargeProfile,
    entry_turnover: float,
    entry_costs: CostBreakdown,
    exit_at_target: Optional[CostBreakdown],
    exit_at_stop: Optional[CostBreakdown],
    breakeven: Optional[float],
    breakeven_move_pct: Optional[float],
    round_trip_at_target: Optional[float],
    gross_at_target: Optional[float],
    net_at_target: Optional[float],
    net_loss_at_stop: Optional[float],
    cost_pct: Optional[float],
    reward_to_cost: Optional[float],
    net_rr: Optional[float],
    policy: ViabilityPolicy,
    failed: list[str],
    skipped: list[str],
) -> TradeEconomics:
    """Turns the computed figures and the list of failed checks into a verdict
    with a readable reason. Kept separate so every exit path from
    assess_trade() produces the same shape of answer."""

    if not policy.enabled:
        reason = "viability gate disabled in configuration; economics computed but not enforced"
        viable = True
        failed_checks: tuple[str, ...] = ()
    elif failed:
        reason = "not cost-viable: " + "; ".join(failed)
        viable = False
        failed_checks = tuple(failed)
    else:
        parts = [f"charges Rs {entry_costs.total:,.2f} on entry"]
        if round_trip_at_target is not None:
            parts.append(
                f"Rs {round_trip_at_target:,.2f} round trip "
                f"({cost_pct:.2%} of order value)"
                if cost_pct is not None
                else f"Rs {round_trip_at_target:,.2f} round trip"
            )
        if net_at_target is not None:
            parts.append(f"net Rs {net_at_target:,.2f} if target is hit")
        if breakeven is not None:
            parts.append(f"breakeven at Rs {breakeven:,.2f}")
        reason = "cost-viable: " + ", ".join(parts)
        viable = True
        failed_checks = ()

    return TradeEconomics(
        quantity=quantity,
        entry_price=entry_price,
        stop_loss=stop_loss,
        target=target,
        profile=profile,
        entry_turnover=entry_turnover,
        entry_costs=entry_costs,
        exit_costs_at_target=exit_at_target,
        exit_costs_at_stop=exit_at_stop,
        breakeven_price=breakeven,
        breakeven_move_pct=breakeven_move_pct,
        round_trip_costs_at_target=round_trip_at_target,
        gross_profit_at_target=gross_at_target,
        net_profit_at_target=net_at_target,
        net_loss_at_stop=net_loss_at_stop,
        cost_pct_of_turnover=cost_pct,
        reward_to_cost_ratio=reward_to_cost,
        net_reward_to_risk=net_rr,
        viable=viable,
        reason=reason,
        failed_checks=failed_checks,
        skipped_checks=tuple(skipped),
    )


def minimum_viable_quantity(
    entry_price: float,
    costs: CostAssumptions,
    policy: ViabilityPolicy,
    stop_loss: Optional[float] = None,
    target: Optional[float] = None,
    profile: ChargeProfile = ChargeProfile.DELIVERY,
    max_quantity: int = 1_000_000,
) -> Optional[int]:
    """Smallest quantity at which this trade plan would pass the gate, or None
    if no size up to `max_quantity` passes.

    Useful for explaining a rejection ("this setup needs at least N shares to
    clear its own charges") rather than only reporting it. Flat charges make
    viability monotonic in size, so this binary-searches on quantity.
    """

    if entry_price <= 0:
        return None

    def passes(qty: int) -> bool:
        return assess_trade(
            entry_price=entry_price,
            quantity=qty,
            costs=costs,
            policy=policy,
            stop_loss=stop_loss,
            target=target,
            profile=profile,
        ).viable

    if not passes(max_quantity):
        return None

    low, high = 1, max_quantity
    while low < high:
        mid = (low + high) // 2
        if passes(mid):
            high = mid
        else:
            low = mid + 1
    return low
