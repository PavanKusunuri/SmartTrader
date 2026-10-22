from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import AppConfig
from app.models import OrderStatus, Signal, SignalAction
from app.paper.broker import PaperBroker

IST = ZoneInfo("Asia/Kolkata")


def make_buy_signal(symbol="TCS", price=3500, stop=3300, target=3800, sig_id="s1") -> Signal:
    return Signal(
        signal_id=sig_id,
        symbol=symbol,
        timestamp=datetime(2024, 1, 1, tzinfo=IST),
        strategy_name="test",
        action=SignalAction.BUY,
        reason="test buy",
        inputs={},
        stop_loss=stop,
        target=target,
    )


def make_sell_signal(symbol="TCS", sig_id="s2", ts=datetime(2024, 1, 5, tzinfo=IST)) -> Signal:
    return Signal(
        signal_id=sig_id,
        symbol=symbol,
        timestamp=ts,
        strategy_name="test",
        action=SignalAction.SELL,
        reason="test sell",
        inputs={},
    )


def make_no_trade_signal(sig_id="s0") -> Signal:
    return Signal(
        signal_id=sig_id,
        symbol="TCS",
        timestamp=datetime(2024, 1, 1, tzinfo=IST),
        strategy_name="test",
        action=SignalAction.NO_TRADE,
        reason="nothing to do",
        inputs={},
    )


def test_no_trade_signal_produces_no_order():
    broker = PaperBroker(AppConfig())
    order = broker.process_signal(make_no_trade_signal(), 3500, {})
    assert order is None
    assert broker.orders == []


def test_buy_signal_fills_and_opens_position():
    broker = PaperBroker(AppConfig())
    order = broker.process_signal(make_buy_signal(), 3500, {})
    assert order.status == OrderStatus.FILLED
    assert order.filled_quantity > 0
    assert "TCS" in broker.portfolio.positions


def test_buy_without_stop_loss_is_rejected():
    broker = PaperBroker(AppConfig())
    bad_signal = replace(make_buy_signal(), stop_loss=None)
    order = broker.process_signal(bad_signal, 3500, {})
    assert order.status == OrderStatus.REJECTED
    assert "stop_loss" in order.rejection_reason


def test_sell_without_position_is_rejected():
    broker = PaperBroker(AppConfig())
    order = broker.process_signal(make_sell_signal(), 3600, {})
    assert order.status == OrderStatus.REJECTED
    assert "no open position" in order.rejection_reason


def test_buy_then_sell_round_trip():
    broker = PaperBroker(AppConfig())
    buy_order = broker.process_signal(make_buy_signal(), 3500, {})
    assert buy_order.status == OrderStatus.FILLED

    sell_order = broker.process_signal(make_sell_signal(), 3600, {"TCS": 3600})
    assert sell_order.status == OrderStatus.FILLED
    assert "TCS" not in broker.portfolio.positions
    assert len(broker.portfolio.trades) == 1


def test_duplicate_signal_id_is_rejected_not_double_filled():
    broker = PaperBroker(AppConfig())
    sig = make_buy_signal()
    order1 = broker.process_signal(sig, 3500, {})
    order2 = broker.process_signal(sig, 3500, {})  # same signal_id again

    assert order1.status == OrderStatus.FILLED
    assert order2.status == OrderStatus.REJECTED
    assert "duplicate" in order2.rejection_reason
    # Only one position worth of cash should have been spent, not two.
    assert len([o for o in broker.orders if o.status == OrderStatus.FILLED]) == 1


def test_kill_switch_rejects_new_buy():
    config = AppConfig()
    config.risk.kill_switch_enabled = True
    broker = PaperBroker(config)
    order = broker.process_signal(make_buy_signal(), 3500, {})
    assert order.status == OrderStatus.REJECTED
    assert "kill switch" in order.rejection_reason


def test_sell_at_lower_price_produces_loss_trade():
    broker = PaperBroker(AppConfig())
    broker.process_signal(make_buy_signal(price=3500, stop=3300), 3500, {})
    broker.process_signal(make_sell_signal(), 3200, {"TCS": 3200})
    trade = broker.portfolio.trades[0]
    assert trade.gross_pnl < 0
    assert trade.net_pnl < trade.gross_pnl  # costs make it worse, never better
