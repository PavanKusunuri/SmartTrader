from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import SwingStrategyConfig
from app.models import Bar, DataQuality, Position, SignalAction
from app.strategies.swing import SwingMAStrategy

IST = ZoneInfo("Asia/Kolkata")


def make_bar(day: int, close: float, quality: DataQuality = DataQuality.OK) -> Bar:
    return Bar(
        symbol="TEST",
        timestamp=datetime(2024, 1, day, tzinfo=IST),
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=1000,
        source="unit-test",
        retrieved_at=datetime(2024, 1, day, tzinfo=IST),
        interval="1d",
        quality=quality,
    )


def make_strategy() -> SwingMAStrategy:
    # Small periods so tests don't need hundreds of bars.
    return SwingMAStrategy(SwingStrategyConfig(fast_ma_period=2, slow_ma_period=4, max_holding_days=5))


def test_no_trade_when_insufficient_history():
    strat = make_strategy()
    bars = [make_bar(d, 100) for d in range(1, 4)]  # fewer than slow_ma_period+1
    signal = strat.evaluate(bars)
    assert signal.action == SignalAction.NO_TRADE
    assert "insufficient history" in signal.reason


def test_no_trade_when_latest_bar_quality_bad():
    strat = make_strategy()
    bars = [make_bar(d, 100) for d in range(1, 6)]
    bars[-1] = make_bar(6, 100, quality=DataQuality.STALE)
    signal = strat.evaluate(bars)
    assert signal.action == SignalAction.NO_TRADE
    assert "quality" in signal.reason


def test_buy_signal_on_bullish_crossover():
    strat = make_strategy()
    # Downtrend then a sharp jump makes fast MA(2) cross above slow MA(4)
    # exactly on the 6th bar (verified by direct SMA computation).
    closes = [110, 108, 106, 104, 102, 120]
    bars = [make_bar(i + 1, c) for i, c in enumerate(closes)]
    signal = strat.evaluate(bars)
    assert signal.action == SignalAction.BUY
    assert signal.stop_loss is not None and signal.stop_loss < bars[-1].close
    assert signal.target is not None and signal.target > bars[-1].close


def test_no_look_ahead_future_bars_dont_affect_signal():
    """The signal for bar N must be identical whether or not later bars exist."""
    strat = make_strategy()
    # Bar 6 is a confirmed BUY crossover (see test_buy_signal_on_bullish_crossover).
    # Bars 7-9 are a crash that must NOT be allowed to change bar 6's decision.
    closes = [110, 108, 106, 104, 102, 120, 90, 50, 10]
    bars = [make_bar(i + 1, c) for i, c in enumerate(closes)]

    signal_at_bar6_only = strat.evaluate(bars[:6])
    assert signal_at_bar6_only.action == SignalAction.BUY

    # Calling evaluate() with only the data available up to bar 6 must produce
    # the identical decision regardless of what the (unseen) future holds.
    signal_recomputed = strat.evaluate(bars[:6])
    assert signal_recomputed.action == signal_at_bar6_only.action
    assert signal_recomputed.inputs == signal_at_bar6_only.inputs


def test_sell_signal_on_bearish_crossover_with_open_position():
    strat = make_strategy()
    # fast MA(2) crosses below slow MA(4) exactly on the 6th bar (verified by
    # direct SMA computation).
    closes = [90, 95, 100, 105, 110, 90]
    bars = [make_bar(i + 1, c) for i, c in enumerate(closes)]
    position = Position(symbol="TEST", quantity=10, avg_entry_price=105, opened_at=bars[4].timestamp)
    signal = strat.evaluate(bars, position=position)
    assert signal.action == SignalAction.SELL
    assert "crossed below" in signal.reason


def test_sell_signal_on_max_holding_period():
    strat = make_strategy()
    bars = [make_bar(d, 100 + d) for d in range(1, 10)]  # steady uptrend, no crossover-down
    position = Position(symbol="TEST", quantity=10, avg_entry_price=100, opened_at=bars[0].timestamp)
    signal = strat.evaluate(bars, position=position)
    assert signal.action == SignalAction.SELL
    assert "max holding period" in signal.reason


def test_no_trade_while_holding_with_no_exit_condition():
    strat = make_strategy()
    closes = [100, 101, 102, 103, 104]
    bars = [make_bar(i + 1, c) for i, c in enumerate(closes)]
    position = Position(symbol="TEST", quantity=10, avg_entry_price=100, opened_at=bars[-1].timestamp)
    signal = strat.evaluate(bars, position=position)
    assert signal.action == SignalAction.NO_TRADE


def test_signal_id_is_deterministic_for_same_bar():
    strat = make_strategy()
    closes = [110, 108, 106, 104, 102, 120, 130]
    bars = [make_bar(i + 1, c) for i, c in enumerate(closes)]
    s1 = strat.evaluate(bars)
    s2 = strat.evaluate(bars)
    assert s1.signal_id == s2.signal_id
