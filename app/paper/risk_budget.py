"""Risk-budget calculator: "how much of my capital can I put at risk?"

Turns a capital figure and a risk profile into concrete percentages AND rupee
amounts for every limit RiskManager enforces. It is a planning aid only: it
changes no setting and places nothing. The profiles are common rules of thumb
(risk a small fixed fraction of capital per trade), NOT researched optima and
NOT personal financial advice. The right number depends on your income, other
holdings and tolerance for losses.
"""

from __future__ import annotations

from typing import Any

from app.config import RiskLimits, ViabilityPolicy

# Rules-of-thumb presets. Keys mirror RiskLimits fields.
PROFILES: dict[str, dict[str, float | int]] = {
    "conservative": {
        "risk_per_trade_pct": 0.005, "max_daily_loss_pct": 0.01,
        "max_weekly_loss_pct": 0.03, "max_drawdown_pct": 0.05,
        "max_open_positions": 3, "max_position_pct": 0.15,
        "max_total_exposure_pct": 0.40,
    },
    "moderate": {
        "risk_per_trade_pct": 0.01, "max_daily_loss_pct": 0.02,
        "max_weekly_loss_pct": 0.05, "max_drawdown_pct": 0.10,
        "max_open_positions": 5, "max_position_pct": 0.20,
        "max_total_exposure_pct": 0.60,
    },
    "aggressive": {
        "risk_per_trade_pct": 0.02, "max_daily_loss_pct": 0.04,
        "max_weekly_loss_pct": 0.08, "max_drawdown_pct": 0.20,
        "max_open_positions": 5, "max_position_pct": 0.25,
        "max_total_exposure_pct": 0.80,
    },
}

# Illustrative stop distance used to show what size a risk budget implies.
TYPICAL_STOPS = {"intraday": 0.01, "swing": 0.05}


def risk_limits_for_profile(profile: str) -> RiskLimits:
    """RiskLimits built from a named preset (validated by pydantic)."""

    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}; choose from {sorted(PROFILES)}")
    return RiskLimits(**PROFILES[profile])


def compute_risk_budget(
    capital: float,
    limits: RiskLimits,
    policy: ViabilityPolicy | None = None,
) -> dict[str, Any]:
    """Percent and rupee risk budget for `capital` under `limits`."""

    if capital <= 0:
        raise ValueError("capital must be positive")
    policy = policy or ViabilityPolicy()

    def rupees(frac: float) -> float:
        return round(capital * frac, 2)

    per_trade = rupees(limits.risk_per_trade_pct)
    all_stopped = limits.max_open_positions * limits.risk_per_trade_pct

    sizing = []
    for style, stop in TYPICAL_STOPS.items():
        distance = stop + limits.gap_buffer_pct  # same buffer RiskManager uses
        by_risk = per_trade / distance
        value = min(by_risk, capital * limits.max_position_pct)
        sizing.append({
            "style": style,
            "stop_distance_pct": stop,
            "position_value": round(value, 2),
            "capped_by": "max_position_pct" if by_risk > capital * limits.max_position_pct
                         else "risk_per_trade_pct",
        })

    warnings: list[str] = []
    if all_stopped > limits.max_weekly_loss_pct:
        warnings.append(
            f"If all {limits.max_open_positions} positions hit their stops you lose "
            f"{all_stopped:.1%} of capital, more than the weekly limit "
            f"({limits.max_weekly_loss_pct:.1%}). The limit would stop new entries, "
            "but the open positions could still lose that much."
        )
    if limits.max_daily_loss_pct < limits.risk_per_trade_pct:
        warnings.append("Daily loss limit is smaller than the per-trade risk.")
    for s in sizing:
        if s["position_value"] < policy.min_order_value:
            warnings.append(
                f"At a {s['stop_distance_pct']:.0%} stop ({s['style']}) this budget "
                f"gives a Rs {s['position_value']:,.0f} position, below the Rs "
                f"{policy.min_order_value:,.0f} minimum order value, so such trades "
                "would be rejected as cost-unviable. Raise capital or risk per trade."
            )
    if limits.max_total_exposure_pct < limits.max_open_positions * limits.max_position_pct:
        warnings.append(
            "Total exposure cap is lower than max positions x max position size, "
            "so you cannot fill every slot at full size."
        )

    return {
        "capital": capital,
        "limits": {
            "risk_per_trade": {"pct": limits.risk_per_trade_pct, "rupees": per_trade},
            "max_daily_loss": {"pct": limits.max_daily_loss_pct,
                               "rupees": rupees(limits.max_daily_loss_pct)},
            "max_weekly_loss": {"pct": limits.max_weekly_loss_pct,
                                "rupees": rupees(limits.max_weekly_loss_pct)},
            "max_drawdown": {"pct": limits.max_drawdown_pct,
                             "rupees": rupees(limits.max_drawdown_pct)},
            "max_position_size": {"pct": limits.max_position_pct,
                                  "rupees": rupees(limits.max_position_pct)},
            "max_total_exposure": {"pct": limits.max_total_exposure_pct,
                                   "rupees": rupees(limits.max_total_exposure_pct)},
            "max_open_positions": limits.max_open_positions,
        },
        "worst_case_all_stopped": {"pct": all_stopped, "rupees": rupees(all_stopped)},
        "implied_position_sizes": sizing,
        "warnings": warnings,
        "disclaimer": (
            "Planning aid based on rules of thumb, not personal financial advice and "
            "not a prediction. Stops can fill worse than their level (gaps), so real "
            "losses can exceed these figures. Never risk money you cannot afford to lose."
        ),
    }
