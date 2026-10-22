from app.backtest.costs import compute_order_costs, compute_slippage
from app.config import CostAssumptions
from app.models import SignalAction


def test_buy_slippage_increases_price():
    price = compute_slippage(100.0, SignalAction.BUY, 0.001)
    assert price == 100.1


def test_sell_slippage_decreases_price():
    price = compute_slippage(100.0, SignalAction.SELL, 0.001)
    assert price == 99.9


def test_slippage_rejects_no_trade_side():
    import pytest

    with pytest.raises(ValueError):
        compute_slippage(100.0, SignalAction.NO_TRADE, 0.001)


def test_costs_are_never_negative():
    costs = CostAssumptions()
    buy_cost = compute_order_costs(100.0, 10, SignalAction.BUY, costs)
    sell_cost = compute_order_costs(100.0, 10, SignalAction.SELL, costs)
    assert buy_cost >= 0
    assert sell_cost >= 0


def test_sell_includes_stt_buy_does_not():
    costs = CostAssumptions(brokerage_per_order=0, exchange_txn_rate=0, stamp_duty_buy_rate=0)
    sell_cost = compute_order_costs(100.0, 10, SignalAction.SELL, costs)
    buy_cost = compute_order_costs(100.0, 10, SignalAction.BUY, costs)
    assert sell_cost > buy_cost  # STT only applies on sell leg
    assert buy_cost == 0


def test_buy_includes_stamp_duty_sell_does_not():
    costs = CostAssumptions(brokerage_per_order=0, exchange_txn_rate=0, stt_sell_rate=0)
    buy_cost = compute_order_costs(100.0, 10, SignalAction.BUY, costs)
    sell_cost = compute_order_costs(100.0, 10, SignalAction.SELL, costs)
    assert buy_cost > sell_cost
    assert sell_cost == 0


def test_higher_turnover_means_higher_cost():
    costs = CostAssumptions()
    small = compute_order_costs(100.0, 1, SignalAction.BUY, costs)
    large = compute_order_costs(100.0, 1000, SignalAction.BUY, costs)
    assert large > small
