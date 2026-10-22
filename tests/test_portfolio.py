from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.paper.portfolio import InsufficientCashError, Portfolio

IST = ZoneInfo("Asia/Kolkata")


def test_starting_state():
    p = Portfolio(starting_cash=100_000)
    assert p.cash == 100_000
    assert p.positions == {}
    assert p.realized_pnl == 0
    assert p.equity({}) == 100_000


def test_buy_reduces_cash_and_opens_position():
    p = Portfolio(starting_cash=100_000)
    p.apply_fill_buy(
        symbol="TCS", quantity=10, fill_price=3500, costs=12.5,
        timestamp=datetime(2024, 1, 1, tzinfo=IST), stop_loss=3300, target=3800, order_id="o1",
    )
    assert p.cash == pytest.approx(100_000 - 10 * 3500 - 12.5)
    assert "TCS" in p.positions
    assert p.positions["TCS"].quantity == 10


def test_buy_rejects_duplicate_symbol():
    p = Portfolio(starting_cash=100_000)
    p.apply_fill_buy("TCS", 10, 3500, 12.5, datetime(2024, 1, 1, tzinfo=IST), 3300, 3800, "o1")
    with pytest.raises(ValueError):
        p.apply_fill_buy("TCS", 5, 3500, 12.5, datetime(2024, 1, 2, tzinfo=IST), 3300, 3800, "o2")


def test_buy_rejects_insufficient_cash():
    p = Portfolio(starting_cash=1000)
    with pytest.raises(InsufficientCashError):
        p.apply_fill_buy("TCS", 10, 3500, 12.5, datetime(2024, 1, 1, tzinfo=IST), 3300, 3800, "o1")


def test_sell_closes_position_and_records_trade():
    p = Portfolio(starting_cash=100_000)
    p.apply_fill_buy("TCS", 10, 3500, 10.0, datetime(2024, 1, 1, tzinfo=IST), 3300, 3800, "o1")
    trade = p.apply_fill_sell("TCS", 3600, 10.0, datetime(2024, 1, 5, tzinfo=IST), "target hit", "o2")

    assert "TCS" not in p.positions
    assert trade.gross_pnl == pytest.approx((3600 - 3500) * 10)
    assert trade.costs == pytest.approx(20.0)
    assert trade.net_pnl == pytest.approx(trade.gross_pnl - 20.0)
    assert p.realized_pnl == pytest.approx(trade.net_pnl)
    assert len(p.trades) == 1


def test_sell_without_position_raises():
    p = Portfolio(starting_cash=100_000)
    with pytest.raises(ValueError):
        p.apply_fill_sell("TCS", 3600, 10.0, datetime(2024, 1, 5, tzinfo=IST), "x", "o2")


def test_equity_includes_unrealized_position_value():
    p = Portfolio(starting_cash=100_000)
    p.apply_fill_buy("TCS", 10, 3500, 10.0, datetime(2024, 1, 1, tzinfo=IST), 3300, 3800, "o1")
    equity = p.equity({"TCS": 3600})
    assert equity == pytest.approx(p.cash + 10 * 3600)


def test_daily_pnl_resets_on_new_day():
    p = Portfolio(starting_cash=100_000)
    p.mark_new_day(date(2024, 1, 1), {})
    assert p.daily_pnl({}) == 0
    p.apply_fill_buy("TCS", 10, 3500, 10.0, datetime(2024, 1, 1, tzinfo=IST), 3300, 3800, "o1")
    p.apply_fill_sell("TCS", 3600, 10.0, datetime(2024, 1, 1, tzinfo=IST), "x", "o2")
    assert p.daily_pnl({}) == pytest.approx(p.realized_pnl)

    p.mark_new_day(date(2024, 1, 2), {})
    assert p.daily_pnl({}) == 0  # new day resets the baseline


def test_reconcile_detects_realized_pnl_mismatch():
    p = Portfolio(starting_cash=100_000)
    p.apply_fill_buy("TCS", 10, 3500, 10.0, datetime(2024, 1, 1, tzinfo=IST), 3300, 3800, "o1")
    p.apply_fill_sell("TCS", 3600, 10.0, datetime(2024, 1, 5, tzinfo=IST), "x", "o2")
    assert p.reconcile({}) == []

    p.realized_pnl += 999  # corrupt the ledger to prove reconcile catches it
    problems = p.reconcile({})
    assert any("mismatch" in msg for msg in problems)
