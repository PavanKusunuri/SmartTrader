"""Tests for the engine's fill/exit semantics: next-bar-open fills,
intrabar stop/target handling, same-bar collision ambiguity, and session
flattening. These are the rules most likely to silently flatter a backtest,
so they are pinned down explicitly here.
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.backtest.engine import run_backtest
from app.config import AppConfig, RiskLimits, SwingStrategyConfig
from app.data.base import DataProvider
from app.market_calendar import MarketSession
from app.models import Bar, DataQuality, OrderStatus, Position, Signal, SignalAction
from app.strategies.base import Strategy

IST = ZoneInfo("Asia/Kolkata")


class ListProvider(DataProvider):
    def __init__(self, bars_by_symbol):
        self.bars_by_symbol = bars_by_symbol

    def get_bars(self, symbol, interval="1d", start=None, end=None):
        return list(self.bars_by_symbol.get(symbol, []))


class ScriptedStrategy(Strategy):
    """Emits a preset action for each bar index, so the engine's fill logic
    can be tested without depending on any indicator maths."""

    name = "scripted"

    def __init__(self, actions: dict[int, str], stop=None, target=None):
        self.actions = actions
        self.stop = stop
        self.target = target
        self._calls = 0

    def evaluate(self, bars, position=None):
        idx = len(bars) - 1
        bar = bars[-1]
        action_name = self.actions.get(idx, "NO_TRADE")
        action = SignalAction(action_name)
        return Signal(
            signal_id=f"scripted:{bar.symbol}:{bar.timestamp.isoformat()}",
            symbol=bar.symbol,
            timestamp=bar.timestamp,
            strategy_name=self.name,
            action=action,
            reason=f"scripted {action_name} at index {idx}",
            inputs={},
            stop_loss=self.stop if action == SignalAction.BUY else None,
            target=self.target if action == SignalAction.BUY else None,
        )


def bar(symbol, dt, o, h, l, c, quality=DataQuality.OK) -> Bar:
    return Bar(
        symbol=symbol, timestamp=dt, open=o, high=h, low=l, close=c,
        volume=1000, source="test-double", retrieved_at=dt,
        interval="1d", quality=quality,
    )


def daily(symbol, day, o, h, l, c, **kw):
    return bar(symbol, datetime(2024, 1, day, tzinfo=IST), o, h, l, c, **kw)


def base_config(**risk):
    return AppConfig(
        starting_cash=100_000,
        risk=RiskLimits(**risk) if risk else RiskLimits(max_position_pct=1.0),
        swing=SwingStrategyConfig(fast_ma_period=2, slow_ma_period=4),
    )


def test_buy_fills_at_next_bar_open_not_signal_bar_close():
    bars = [
        daily("X", 1, 100, 101, 99, 100),
        daily("X", 2, 100, 101, 99, 100),   # BUY signalled here, close 100
        daily("X", 3, 105, 106, 104, 105),  # must fill at THIS open (105)
    ]
    result = run_backtest(
        provider=ListProvider({"X": bars}),
        symbols=["X"],
        strategy=ScriptedStrategy({1: "BUY"}, stop=90),
        config=base_config(),
    )
    filled = [o for o in result.orders if o.status == OrderStatus.FILLED]
    assert len(filled) == 1
    # Fill price is bar 3's open (105) plus adverse buy slippage — never 100.
    assert filled[0].filled_price > 105
    assert filled[0].filled_price < 105 * 1.01
    assert filled[0].filled_at == bars[2].timestamp


def test_signal_on_final_bar_expires_unfilled():
    bars = [
        daily("X", 1, 100, 101, 99, 100),
        daily("X", 2, 100, 101, 99, 100),
    ]
    result = run_backtest(
        provider=ListProvider({"X": bars}),
        symbols=["X"],
        strategy=ScriptedStrategy({1: "BUY"}, stop=90),
        config=base_config(),
    )
    assert result.expired_signal_count == 1
    assert result.metrics.trade_count == 0
    assert any(o.status == OrderStatus.CANCELLED for o in result.orders)


def test_stop_loss_exit_fills_at_stop_level():
    bars = [
        daily("X", 1, 100, 101, 99, 100),
        daily("X", 2, 100, 101, 99, 100),   # BUY signalled
        daily("X", 3, 100, 101, 99, 100),   # fills at open 100, stop 95
        daily("X", 4, 99, 100, 90, 92),     # low 90 breaches stop 95
    ]
    result = run_backtest(
        provider=ListProvider({"X": bars}),
        symbols=["X"],
        strategy=ScriptedStrategy({1: "BUY"}, stop=95, target=200),
        config=base_config(),
    )
    assert result.metrics.trade_count == 1
    trade = result.trades[0]
    assert "stop-loss" in trade.exit_reason
    assert not trade.ambiguous_exit
    # Exit price is the stop level minus adverse sell slippage.
    assert trade.exit_price <= 95


def test_target_exit_fills_at_target_level():
    bars = [
        daily("X", 1, 100, 101, 99, 100),
        daily("X", 2, 100, 101, 99, 100),
        daily("X", 3, 100, 101, 99, 100),   # fills at open 100
        daily("X", 4, 101, 115, 100, 112),  # high 115 breaches target 110
    ]
    result = run_backtest(
        provider=ListProvider({"X": bars}),
        symbols=["X"],
        strategy=ScriptedStrategy({1: "BUY"}, stop=90, target=110),
        config=base_config(),
    )
    assert result.metrics.trade_count == 1
    trade = result.trades[0]
    assert "target" in trade.exit_reason
    assert not trade.ambiguous_exit
    assert trade.gross_pnl > 0


def test_stop_and_target_in_same_bar_assumes_stop_and_marks_ambiguous():
    """CLAUDE.md: never assume a favourable intrabar ordering."""
    bars = [
        daily("X", 1, 100, 101, 99, 100),
        daily("X", 2, 100, 101, 99, 100),
        daily("X", 3, 100, 101, 99, 100),   # fills at open 100; stop 95, target 110
        daily("X", 4, 100, 115, 90, 112),   # BOTH touched: low 90 and high 115
    ]
    result = run_backtest(
        provider=ListProvider({"X": bars}),
        symbols=["X"],
        strategy=ScriptedStrategy({1: "BUY"}, stop=95, target=110),
        config=base_config(),
    )
    assert result.metrics.trade_count == 1
    trade = result.trades[0]
    assert trade.ambiguous_exit is True
    assert result.ambiguous_trade_count == 1
    # The conservative (stop) outcome was used, so the trade is a loss.
    assert trade.gross_pnl < 0
    assert "both touched" in trade.exit_reason


def test_queued_order_dropped_when_fill_bar_is_invalid():
    bars = [
        daily("X", 1, 100, 101, 99, 100),
        daily("X", 2, 100, 101, 99, 100),                                  # BUY signalled
        daily("X", 3, 105, 106, 104, 105, quality=DataQuality.INVALID),    # bad fill bar
        daily("X", 4, 105, 106, 104, 105),
    ]
    result = run_backtest(
        provider=ListProvider({"X": bars}),
        symbols=["X"],
        strategy=ScriptedStrategy({1: "BUY"}, stop=90),
        config=base_config(),
    )
    # A broken bar must not become a fill.
    assert not any(o.status == OrderStatus.FILLED for o in result.orders)
    assert result.expired_signal_count == 1


# --- Intraday session rules -------------------------------------------------


def intraday_bar(symbol, hour, minute, o, h, l, c, day=2):
    return bar(
        symbol, datetime(2024, 1, day, hour, minute, tzinfo=IST), o, h, l, c
    )


def test_no_entry_after_session_entry_cutoff():
    session = MarketSession(entry_cutoff=time(14, 30), flatten_time=time(15, 15))
    bars = [
        intraday_bar("X", 14, 25, 100, 101, 99, 100),
        intraday_bar("X", 14, 45, 100, 101, 99, 100),  # BUY signalled AFTER cutoff
        intraday_bar("X", 15, 0, 100, 101, 99, 100),
    ]
    result = run_backtest(
        provider=ListProvider({"X": bars}),
        symbols=["X"],
        strategy=ScriptedStrategy({1: "BUY"}, stop=90),
        config=base_config(),
        interval="5m",
        session=session,
    )
    assert not any(o.status == OrderStatus.FILLED for o in result.orders)


def test_entry_before_cutoff_is_allowed():
    session = MarketSession(entry_cutoff=time(14, 30), flatten_time=time(15, 15))
    bars = [
        intraday_bar("X", 10, 0, 100, 101, 99, 100),
        intraday_bar("X", 10, 5, 100, 101, 99, 100),  # BUY signalled before cutoff
        intraday_bar("X", 10, 10, 100, 101, 99, 100),
    ]
    result = run_backtest(
        provider=ListProvider({"X": bars}),
        symbols=["X"],
        strategy=ScriptedStrategy({1: "BUY"}, stop=90),
        config=base_config(),
        interval="5m",
        session=session,
    )
    assert any(o.status == OrderStatus.FILLED for o in result.orders)


def test_intraday_position_is_flattened_before_session_close():
    """No overnight positions by default."""
    session = MarketSession(entry_cutoff=time(14, 30), flatten_time=time(15, 15))
    bars = [
        intraday_bar("X", 10, 0, 100, 101, 99, 100),
        intraday_bar("X", 10, 5, 100, 101, 99, 100),   # BUY signalled
        intraday_bar("X", 10, 10, 100, 101, 99, 100),  # fills here
        intraday_bar("X", 15, 20, 100, 101, 99, 100),  # at/after flatten time
    ]
    result = run_backtest(
        provider=ListProvider({"X": bars}),
        symbols=["X"],
        strategy=ScriptedStrategy({1: "BUY"}, stop=50, target=500),
        config=base_config(),
        interval="5m",
        session=session,
    )
    assert result.metrics.trade_count == 1
    assert "end-of-session" in result.trades[0].exit_reason


def test_daily_mode_does_not_flatten_positions():
    """With no session supplied, swing positions survive across days."""
    bars = [
        daily("X", 1, 100, 101, 99, 100),
        daily("X", 2, 100, 101, 99, 100),
        daily("X", 3, 100, 101, 99, 100),
        daily("X", 4, 100, 101, 99, 100),
    ]
    result = run_backtest(
        provider=ListProvider({"X": bars}),
        symbols=["X"],
        strategy=ScriptedStrategy({1: "BUY"}, stop=50, target=500),
        config=base_config(),
    )
    assert result.metrics.trade_count == 0  # still open, never force-closed
    assert "X" not in [t.symbol for t in result.trades]
