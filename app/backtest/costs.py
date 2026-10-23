"""Transaction cost model for Indian cash equities.

All rates come from app.config.CostAssumptions, which are explicitly marked
as unverified default assumptions (see that module's docstring). This module
only does the arithmetic; it does not claim the rates are current official
values.

Charge structure modelled, per leg:

    brokerage      = clamp(pct * turnover, floor, cap)   [per order]
    STT            = rate * turnover    (delivery: both legs; intraday: sell)
    exchange txn   = rate * turnover    (both legs)
    SEBI turnover  = rate * turnover    (both legs)
    IPFT           = rate * turnover    (both legs)
    stamp duty     = rate * turnover    (buy leg only)
    DP charge      = flat per sell leg  (delivery only)
    GST            = rate * (brokerage + exchange txn + SEBI + IPFT [+ DP])

The two structural facts that matter most for small trades, and which a
percentage-only cost model hides:

1. **Brokerage has a floor and a cap.** A Rs 380 order does not pay 0.1% of
   Rs 380; it pays the per-order minimum.
2. **The DP charge on a delivery sell is flat.** It costs the same on a
   Rs 380 position as on a Rs 380,000 one.

Together they mean round-trip cost as a percentage of turnover explodes as
order value shrinks. app/paper/viability.py turns that into a NO_TRADE rule.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.config import CostAssumptions, ProfileCharges
from app.models import ChargeProfile, SignalAction


def compute_slippage(price: float, side: SignalAction, slippage_rate: float) -> float:
    """Returns the slipped fill price: buys fill worse (higher), sells fill
    worse (lower) — slippage always works against the trader, never for."""

    if side == SignalAction.BUY:
        return price * (1 + slippage_rate)
    if side == SignalAction.SELL:
        return price * (1 - slippage_rate)
    raise ValueError(f"slippage not defined for side {side}")


@dataclass(frozen=True)
class CostBreakdown:
    """Itemised charges for one order leg, so the dashboard and the viability
    gate can both show *which* charge killed a trade rather than one opaque
    number. Components are rounded to paise and `total` is their exact sum,
    so a displayed breakdown always adds up."""

    side: SignalAction
    profile: ChargeProfile
    turnover: float
    quantity: int
    brokerage: float
    stt: float
    exchange_txn: float
    sebi_turnover: float
    ipft: float
    stamp_duty: float
    dp_charge: float
    gst: float
    total: float

    @property
    def pct_of_turnover(self) -> float:
        """Charges as a fraction of this leg's turnover (0.0 when turnover is 0)."""

        return self.total / self.turnover if self.turnover > 0 else 0.0

    def as_dict(self) -> dict[str, float | str | int]:
        return {
            "side": self.side.value,
            "profile": self.profile.value,
            "quantity": self.quantity,
            "turnover": self.turnover,
            "brokerage": self.brokerage,
            "stt": self.stt,
            "exchange_txn": self.exchange_txn,
            "sebi_turnover": self.sebi_turnover,
            "ipft": self.ipft,
            "stamp_duty": self.stamp_duty,
            "dp_charge": self.dp_charge,
            "gst": self.gst,
            "total": self.total,
            "pct_of_turnover": self.pct_of_turnover,
        }


def compute_brokerage(turnover: float, charges: ProfileCharges) -> float:
    """Brokerage for one leg: percentage of turnover, capped, then floored.

    The floor is what makes small orders uneconomic, so it is applied last
    and is never waived for a tiny order. A zero-turnover leg pays nothing.
    """

    if turnover <= 0:
        return 0.0

    brokerage = turnover * charges.brokerage_pct
    if charges.brokerage_cap > 0:
        brokerage = min(brokerage, charges.brokerage_cap)
    return max(brokerage, charges.brokerage_min)


def compute_order_cost_breakdown(
    price: float,
    quantity: int,
    side: SignalAction,
    costs: CostAssumptions,
    profile: ChargeProfile = ChargeProfile.DELIVERY,
) -> CostBreakdown:
    """Itemised charges for one order leg. Every component is >= 0, so total
    cost always reduces the trader's P&L on both legs."""

    if side not in (SignalAction.BUY, SignalAction.SELL):
        raise ValueError(f"costs not defined for side {side}")
    if quantity < 0:
        raise ValueError("quantity must not be negative")
    if price < 0:
        raise ValueError("price must not be negative")

    charges = costs.for_profile(profile)
    turnover = price * quantity

    brokerage = compute_brokerage(turnover, charges)
    exchange_txn = turnover * costs.exchange_txn_rate
    sebi_turnover = turnover * costs.sebi_turnover_rate
    ipft = turnover * costs.ipft_rate

    if side == SignalAction.BUY:
        stt = turnover * charges.stt_buy_rate
        stamp_duty = turnover * charges.stamp_duty_buy_rate
        dp_charge = 0.0
    else:
        stt = turnover * charges.stt_sell_rate
        stamp_duty = 0.0
        # Flat, per scrip per day. Charged once per sell leg here because the
        # paper broker exits a position in a single order.
        dp_charge = charges.dp_charge_per_sell if quantity > 0 else 0.0

    gst_base = brokerage + exchange_txn + sebi_turnover + ipft
    if charges.dp_charge_gst_applicable:
        gst_base += dp_charge
    gst = gst_base * costs.gst_rate

    # Round each component to paise, then sum, so the itemisation the UI
    # shows is exactly the total that is charged to the portfolio.
    brokerage = round(brokerage, 2)
    stt = round(stt, 2)
    exchange_txn = round(exchange_txn, 2)
    sebi_turnover = round(sebi_turnover, 2)
    ipft = round(ipft, 2)
    stamp_duty = round(stamp_duty, 2)
    dp_charge = round(dp_charge, 2)
    gst = round(gst, 2)

    total = round(
        brokerage + stt + exchange_txn + sebi_turnover + ipft + stamp_duty + dp_charge + gst,
        2,
    )

    return CostBreakdown(
        side=side,
        profile=profile,
        turnover=round(turnover, 2),
        quantity=quantity,
        brokerage=brokerage,
        stt=stt,
        exchange_txn=exchange_txn,
        sebi_turnover=sebi_turnover,
        ipft=ipft,
        stamp_duty=stamp_duty,
        dp_charge=dp_charge,
        gst=gst,
        total=total,
    )


def compute_order_costs(
    price: float,
    quantity: int,
    side: SignalAction,
    costs: CostAssumptions,
    profile: ChargeProfile = ChargeProfile.DELIVERY,
) -> float:
    """Total cost for one order leg. Thin wrapper over
    compute_order_cost_breakdown for callers that only need the number."""

    return compute_order_cost_breakdown(price, quantity, side, costs, profile).total


def compute_round_trip_costs(
    entry_price: float,
    exit_price: float,
    quantity: int,
    costs: CostAssumptions,
    profile: ChargeProfile = ChargeProfile.DELIVERY,
) -> float:
    """Total charges for a complete buy-then-sell round trip."""

    entry = compute_order_costs(entry_price, quantity, SignalAction.BUY, costs, profile)
    exit_ = compute_order_costs(exit_price, quantity, SignalAction.SELL, costs, profile)
    return round(entry + exit_, 2)


def breakeven_exit_price(
    entry_price: float,
    quantity: int,
    costs: CostAssumptions,
    profile: ChargeProfile = ChargeProfile.DELIVERY,
    max_iterations: int = 100,
) -> float | None:
    """The exit price at which a round trip nets exactly zero.

    Sell-leg charges depend on the exit price, which depends on the charges,
    so this is solved by fixed-point iteration rather than a closed form —
    the cost function has a floor/cap kink and per-paise rounding, and
    iterating against the real function keeps this in step with it instead of
    duplicating its algebra.

    The result is rounded UP to the next paise: rounding down would report a
    breakeven that is a fraction short of breaking even. Returns None for a
    zero/invalid position, where breakeven is undefined.
    """

    if quantity <= 0 or entry_price <= 0:
        return None

    entry_costs = compute_order_costs(
        entry_price, quantity, SignalAction.BUY, costs, profile
    )

    exit_price = entry_price
    for _ in range(max_iterations):
        exit_costs = compute_order_costs(
            exit_price, quantity, SignalAction.SELL, costs, profile
        )
        next_price = entry_price + (entry_costs + exit_costs) / quantity
        if abs(next_price - exit_price) < 1e-9:
            exit_price = next_price
            break
        exit_price = next_price

    # Round up to the next paise so the returned price truly clears costs.
    return math.ceil(exit_price * 100 - 1e-9) / 100
