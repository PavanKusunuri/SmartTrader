import pytest

from app.config import RiskLimits
from app.paper.risk_budget import PROFILES, compute_risk_budget, risk_limits_for_profile


def test_rupee_amounts_are_percent_of_capital():
    b = compute_risk_budget(200_000, RiskLimits())
    assert b["limits"]["risk_per_trade"]["rupees"] == 2000.0  # 1% default
    assert b["limits"]["max_daily_loss"]["rupees"] == 6000.0  # 3% default
    assert b["worst_case_all_stopped"]["rupees"] == 10_000.0  # 5 positions x 1%


def test_profiles_are_valid_and_ordered_by_risk():
    risk = [risk_limits_for_profile(p).risk_per_trade_pct
            for p in ("conservative", "moderate", "aggressive")]
    assert risk == sorted(risk) and len(set(risk)) == 3
    for name in PROFILES:
        risk_limits_for_profile(name)


def test_unknown_profile_and_bad_capital_rejected():
    with pytest.raises(ValueError):
        risk_limits_for_profile("yolo")
    with pytest.raises(ValueError):
        compute_risk_budget(0, RiskLimits())


def test_sizing_matches_risk_manager():
    from app.paper.risk import RiskManager

    limits = risk_limits_for_profile("moderate")
    b = compute_risk_budget(100_000, limits)
    swing = next(s for s in b["implied_position_sizes"] if s["style"] == "swing")
    qty = RiskManager(limits).position_size(100_000, 100.0, 95.0)
    assert qty * 100.0 == pytest.approx(swing["position_value"], rel=0.01)


def test_small_capital_warns_about_minimum_order():
    b = compute_risk_budget(10_000, risk_limits_for_profile("conservative"))
    assert any("minimum order value" in w for w in b["warnings"])


def test_worst_case_above_weekly_limit_warns():
    limits = RiskLimits(risk_per_trade_pct=0.02, max_weekly_loss_pct=0.05, max_open_positions=5)
    assert any("weekly limit" in w for w in compute_risk_budget(100_000, limits)["warnings"])
