from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.config import SwingTrendConfig
from app.models import Bar, DataQuality, Position, SignalAction
from app.strategies.swing_trend import SwingTrendStrategy

IST = ZoneInfo("Asia/Kolkata")
T0 = datetime(2024, 1, 1, 15, 30, tzinfo=IST)


def make_bars(closes, symbol="X", volumes=None, spread=1.0):
    bars = []
    for i, c in enumerate(closes):
        bars.append(Bar(
            symbol=symbol, timestamp=T0 + timedelta(days=i), open=c, high=c + spread,
            low=c - spread, close=c, volume=(volumes[i] if volumes else 1000),
            source="test", retrieved_at=T0, interval="1d",
        ))
    return bars


def uptrend(n=80, start=100.0, step=0.5):
    return [start + step * i for i in range(n)]


def breakout_series():
    # Gentle uptrend, a flat consolidation, then a close above the range on volume.
    closes = uptrend(70) + [135.0] * 8 + [136.5]
    volumes = [1000] * (len(closes) - 1) + [3000]
    return closes, volumes


def test_breakout_in_uptrend_buys_with_atr_stop_and_target():
    closes, volumes = breakout_series()
    bars = make_bars(closes, volumes=volumes)
    sig = SwingTrendStrategy(SwingTrendConfig(use_regime_filter=False)).evaluate(bars)
    assert sig.action == SignalAction.BUY
    assert sig.stop_loss < bars[-1].close < sig.target
    risk = bars[-1].close - sig.stop_loss
    assert sig.target - bars[-1].close == pytest.approx(2.0 * risk, abs=0.02)
    assert sig.inputs["setup"] == "BREAKOUT"


def test_downtrend_never_buys():
    bars = make_bars(list(reversed(uptrend(80))))
    sig = SwingTrendStrategy(SwingTrendConfig(use_regime_filter=False)).evaluate(bars)
    assert sig.action == SignalAction.NO_TRADE


def test_insufficient_history_and_bad_quality_are_no_trade():
    s = SwingTrendStrategy(SwingTrendConfig(use_regime_filter=False))
    assert s.evaluate(make_bars(uptrend(10))).action == SignalAction.NO_TRADE
    closes, volumes = breakout_series()
    bars = make_bars(closes, volumes=volumes)
    bad = Bar(**{**bars[-1].__dict__, "quality": DataQuality.INVALID})
    assert s.evaluate(bars[:-1] + [bad]).action == SignalAction.NO_TRADE


def test_signal_is_causal_future_bars_do_not_change_it():
    from app.backtest.engine import run_backtest
    from app.config import AppConfig

    closes, volumes = breakout_series()
    tail = [140.0, 120.0, 90.0, 60.0]  # wildly different future

    def run(extra):
        class P:
            def get_bars(self, symbol, interval="1d", start=None, end=None):
                return make_bars(closes + extra, symbol=symbol,
                                 volumes=volumes + [1000] * len(extra))
        res = run_backtest(
            P(), ["X"], SwingTrendStrategy(SwingTrendConfig(use_regime_filter=False)), AppConfig(),
        )
        return {s.signal_id: (s.action, s.stop_loss, s.target) for s in res.signals
                if s.strategy_name == "swing_trend_atr"}

    short, long_ = run([]), run(tail)
    shared = set(short) & set(long_)
    assert len(shared) == len(closes)
    assert any(short[k][0] == SignalAction.BUY for k in shared)
    assert all(short[k] == long_[k] for k in shared)  # future bars changed nothing


def test_bearish_index_regime_blocks_entry():
    closes, volumes = breakout_series()
    bars = make_bars(closes, volumes=volumes)
    index = make_bars(list(reversed(uptrend(len(closes) + 10, start=100.0))), symbol="IDX")
    index = [Bar(**{**b.__dict__, "timestamp": T0 + timedelta(days=i - 10)}) for i, b in enumerate(index)]
    s = SwingTrendStrategy(SwingTrendConfig(), index_bars=index)
    sig = s.evaluate(bars)
    assert sig.action == SignalAction.NO_TRADE
    assert "regime" in sig.reason


def test_regime_gate_only_sees_index_bars_up_to_decision_date():
    closes, volumes = breakout_series()
    bars = make_bars(closes, volumes=volumes)
    s = SwingTrendStrategy(SwingTrendConfig(), index_bars=make_bars(uptrend(200), symbol="IDX"))
    visible = s._index_visible(bars[-1].timestamp.date())
    assert all(b.timestamp.date() <= bars[-1].timestamp.date() for b in visible)


def test_missing_index_is_disclosed_not_silently_passed():
    closes, volumes = breakout_series()
    sig = SwingTrendStrategy().evaluate(make_bars(closes, volumes=volumes))
    assert "NOT_CHECKED" in sig.inputs["regime"]


def test_exit_on_trend_failure_and_time_stop():
    closes = uptrend(80) + [90.0]  # sharp drop below MA20
    bars = make_bars(closes)
    pos = Position(symbol="X", quantity=10, avg_entry_price=130.0, opened_at=bars[-3].timestamp)
    s = SwingTrendStrategy(SwingTrendConfig(use_regime_filter=False))
    sig = s.evaluate(bars, position=pos)
    assert sig.action == SignalAction.SELL and "trend failure" in sig.reason

    held = make_bars(uptrend(80))
    old = Position(symbol="X", quantity=10, avg_entry_price=100.0, opened_at=held[0].timestamp)
    assert s.evaluate(held, position=old).action == SignalAction.SELL
    fresh = Position(symbol="X", quantity=10, avg_entry_price=100.0, opened_at=held[-2].timestamp)
    assert s.evaluate(held, position=fresh).action == SignalAction.NO_TRADE


def test_open_position_never_gets_a_buy():
    closes, volumes = breakout_series()
    bars = make_bars(closes, volumes=volumes)
    pos = Position(symbol="X", quantity=1, avg_entry_price=130.0, opened_at=bars[-2].timestamp)
    sig = SwingTrendStrategy(SwingTrendConfig(use_regime_filter=False)).evaluate(bars, position=pos)
    assert sig.action != SignalAction.BUY


def test_runs_end_to_end_in_the_backtest_engine():
    from app.backtest.engine import run_backtest
    from app.config import AppConfig

    class P:
        def get_bars(self, symbol, interval="1d", start=None, end=None):
            closes, volumes = breakout_series()
            return make_bars(closes + [140.0, 145.0, 100.0, 95.0], symbol=symbol,
                             volumes=volumes + [1500] * 4)

    res = run_backtest(
        P(), ["X"], SwingTrendStrategy(SwingTrendConfig(use_regime_filter=False)), AppConfig(),
    )
    assert res.reconciliation_problems == []
