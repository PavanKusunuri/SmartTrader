from datetime import date

from app.config import RiskLimits
from app.paper.portfolio import Portfolio
from app.paper.risk import RiskManager


def make_risk(**overrides) -> RiskManager:
    return RiskManager(RiskLimits(**overrides))


def test_position_size_respects_risk_per_trade():
    risk = make_risk(risk_per_trade_pct=0.01, max_position_pct=1.0)
    # 1% of 100,000 = 1,000 risk budget; stop distance 10 -> 100 shares
    qty = risk.position_size(cash=100_000, entry_price=100, stop_loss=90)
    assert qty == 100


def test_position_size_capped_by_max_position_pct():
    risk = make_risk(risk_per_trade_pct=1.0, max_position_pct=0.1)
    # risk budget huge, but max position value = 10,000 -> 100 shares @ 100
    qty = risk.position_size(cash=100_000, entry_price=100, stop_loss=1)
    assert qty == 100


def test_position_size_zero_for_invalid_stop():
    risk = make_risk()
    assert risk.position_size(cash=100_000, entry_price=100, stop_loss=100) == 0
    assert risk.position_size(cash=100_000, entry_price=100, stop_loss=110) == 0
    assert risk.position_size(cash=100_000, entry_price=0, stop_loss=90) == 0


def test_kill_switch_blocks_new_entries():
    risk = make_risk(kill_switch_enabled=True)
    portfolio = Portfolio(100_000)
    portfolio.mark_new_day(date(2024, 1, 1), {})
    allowed, reason = risk.check_new_entry_allowed(portfolio, "TCS", 10, 100, {})
    assert not allowed
    assert "kill switch" in reason


def test_max_open_positions_enforced():
    risk = make_risk(max_open_positions=1)
    portfolio = Portfolio(100_000)
    portfolio.mark_new_day(date(2024, 1, 1), {})
    from datetime import datetime
    from zoneinfo import ZoneInfo

    portfolio.apply_fill_buy("INFY", 5, 100, 1, datetime(2024, 1, 1, tzinfo=ZoneInfo("Asia/Kolkata")), 90, 110, "o1")
    allowed, reason = risk.check_new_entry_allowed(portfolio, "TCS", 10, 100, {"INFY": 100})
    assert not allowed
    assert "max open positions" in reason


def test_max_total_exposure_enforced():
    risk = make_risk(max_total_exposure_pct=0.05, max_open_positions=10)
    portfolio = Portfolio(100_000)
    portfolio.mark_new_day(date(2024, 1, 1), {})
    # 10 shares @ 100 = 1,000 = 1% of equity, which is within 5% -> allowed
    allowed, _ = risk.check_new_entry_allowed(portfolio, "TCS", 10, 100, {})
    assert allowed
    # 1000 shares @ 100 = 100,000 = 100% of equity, exceeds 5% -> blocked
    allowed, reason = risk.check_new_entry_allowed(portfolio, "TCS", 1000, 100, {})
    assert not allowed
    assert "exposure" in reason


def test_max_daily_loss_enforced():
    risk = make_risk(max_daily_loss_pct=0.01, max_open_positions=10)
    portfolio = Portfolio(100_000)
    portfolio.mark_new_day(date(2024, 1, 1), {})
    # Simulate a loss bigger than 1% of day-start equity by corrupting cash directly
    portfolio.cash -= 2000  # pretend a prior realized loss already happened today
    allowed, reason = risk.check_new_entry_allowed(portfolio, "TCS", 1, 100, {})
    assert not allowed
    assert "daily loss" in reason


def test_duplicate_symbol_blocked():
    risk = make_risk()
    portfolio = Portfolio(100_000)
    portfolio.mark_new_day(date(2024, 1, 1), {})
    from datetime import datetime
    from zoneinfo import ZoneInfo

    portfolio.apply_fill_buy("TCS", 5, 100, 1, datetime(2024, 1, 1, tzinfo=ZoneInfo("Asia/Kolkata")), 90, 110, "o1")
    allowed, reason = risk.check_new_entry_allowed(portfolio, "TCS", 10, 100, {"TCS": 100})
    assert not allowed
    assert "already holding" in reason


def test_zero_quantity_blocked():
    risk = make_risk()
    portfolio = Portfolio(100_000)
    portfolio.mark_new_day(date(2024, 1, 1), {})
    allowed, reason = risk.check_new_entry_allowed(portfolio, "TCS", 0, 100, {})
    assert not allowed
    assert "zero" in reason
