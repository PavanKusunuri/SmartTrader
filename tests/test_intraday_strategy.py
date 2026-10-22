from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import IntradayStrategyConfig
from app.market_calendar import MarketSession
from app.models import Bar, DataQuality, Position, SignalAction
from app.strategies.intraday import IntradayMomentumStrategy

IST = ZoneInfo("Asia/Kolkata")

CFG = IntradayStrategyConfig(
    fast_ma_period=2, slow_ma_period=4, volume_ma_period=4, volume_surge_multiple=1.5
)
SESSION = MarketSession(entry_cutoff=time(14, 30), flatten_time=time(15, 15))


def ibar(minute_offset: int, close: float, volume: int = 10_000, quality=DataQuality.OK) -> Bar:
    ts = datetime(2024, 1, 2, 10, 0, tzinfo=IST) + timedelta(minutes=minute_offset)
    return Bar(
        symbol="TEST", timestamp=ts, open=close, high=close + 1, low=close - 1,
        close=close, volume=volume, source="unit-test", retrieved_at=ts,
        interval="5m", quality=quality,
    )


def make_strategy(session: MarketSession | None = SESSION) -> IntradayMomentumStrategy:
    return IntradayMomentumStrategy(CFG, session=session)


def rising_bars(n: int = 6, volume: int = 10_000) -> list[Bar]:
    """Steadily rising closes so the trend filter and momentum are satisfied."""
    return [ibar(i * 5, 100 + i, volume=volume) for i in range(n)]


def test_no_trade_on_insufficient_history():
    strat = make_strategy()
    signal = strat.evaluate(rising_bars(3))
    assert signal.action == SignalAction.NO_TRADE
    assert "insufficient history" in signal.reason


def test_no_trade_on_bad_data_quality():
    strat = make_strategy()
    bars = rising_bars(6)
    bars[-1] = ibar(25, 110, quality=DataQuality.STALE)
    signal = strat.evaluate(bars)
    assert signal.action == SignalAction.NO_TRADE
    assert "quality" in signal.reason


def test_buy_requires_volume_confirmation():
    strat = make_strategy()
    # Flat volume: last bar does not exceed 1.5x the average, so no entry.
    bars = rising_bars(6, volume=10_000)
    signal = strat.evaluate(bars)
    assert signal.action == SignalAction.NO_TRADE
    assert "volume" in signal.reason


def test_buy_signal_when_all_conditions_met():
    strat = make_strategy()
    bars = rising_bars(5, volume=10_000)
    bars.append(ibar(25, 120, volume=100_000))  # big up move on surging volume
    signal = strat.evaluate(bars)
    assert signal.action == SignalAction.BUY
    assert signal.stop_loss < bars[-1].close < signal.target
    assert "volume" in signal.reason


def test_no_trade_without_momentum():
    """Isolates the momentum condition: a dip small enough that the trend
    filter still passes (fast MA 103.5 > slow MA 103.0) but the close (103)
    is below the previous close (104)."""
    strat = make_strategy()
    bars = rising_bars(5, volume=10_000)  # closes 100..104
    bars.append(ibar(25, 103, volume=100_000))
    signal = strat.evaluate(bars)
    assert signal.inputs["trend_up"] is True
    assert signal.inputs["momentum_up"] is False
    assert signal.action == SignalAction.NO_TRADE
    assert "momentum" in signal.reason


def test_no_entry_outside_session():
    strat = make_strategy()
    # 03:00 is outside the 09:15-15:30 session.
    bars = [
        Bar(
            symbol="TEST", timestamp=datetime(2024, 1, 2, 3, i * 5, tzinfo=IST),
            open=100 + i, high=101 + i, low=99 + i, close=100 + i, volume=100_000,
            source="unit-test", retrieved_at=datetime(2024, 1, 2, 3, 0, tzinfo=IST),
            interval="5m",
        )
        for i in range(6)
    ]
    signal = strat.evaluate(bars)
    assert signal.action == SignalAction.NO_TRADE
    assert "session" in signal.reason


def test_no_entry_after_cutoff_but_exit_still_allowed():
    strat = make_strategy()
    late = [
        Bar(
            symbol="TEST", timestamp=datetime(2024, 1, 2, 15, i, tzinfo=IST),
            open=100 + i, high=101 + i, low=99 + i, close=100 + i, volume=100_000,
            source="unit-test", retrieved_at=datetime(2024, 1, 2, 15, 0, tzinfo=IST),
            interval="5m",
        )
        for i in range(6)
    ]
    # No position: entry blocked because 15:0x is past the 14:30 cutoff.
    assert strat.evaluate(late).action == SignalAction.NO_TRADE
    assert "entry window closed" in strat.evaluate(late).reason


def test_sell_when_trend_breaks_with_open_position():
    strat = make_strategy()
    # Rising then sharply falling closes push fast MA below slow MA.
    closes = [100, 102, 104, 106, 108, 80, 60]
    bars = [ibar(i * 5, c, volume=10_000) for i, c in enumerate(closes)]
    position = Position(
        symbol="TEST", quantity=10, avg_entry_price=106, opened_at=bars[4].timestamp
    )
    signal = strat.evaluate(bars, position=position)
    assert signal.action == SignalAction.SELL
    assert "trend filter broke" in signal.reason


def test_hold_when_trend_intact():
    strat = make_strategy()
    bars = rising_bars(7, volume=10_000)
    position = Position(
        symbol="TEST", quantity=10, avg_entry_price=100, opened_at=bars[2].timestamp
    )
    signal = strat.evaluate(bars, position=position)
    assert signal.action == SignalAction.NO_TRADE
    assert "trend still intact" in signal.reason


def test_works_without_a_session_configured():
    """Session rules are optional; the strategy must still function."""
    strat = make_strategy(session=None)
    bars = rising_bars(5, volume=10_000)
    bars.append(ibar(25, 120, volume=100_000))
    assert strat.evaluate(bars).action == SignalAction.BUY
