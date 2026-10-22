from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.backtest.engine import compute_buy_and_hold_benchmark, run_backtest
from app.backtest.metrics import compute_equity_curve_drawdown
from app.config import AppConfig, RiskLimits, SwingStrategyConfig
from app.data.base import DataProvider
from app.data.csv_provider import CsvProvider
from app.models import Bar, DataQuality, SignalAction
from app.strategies.swing import SwingMAStrategy

IST = ZoneInfo("Asia/Kolkata")
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"


class ListProvider(DataProvider):
    """Test double that serves a fixed in-memory bar list — no network, no files."""

    def __init__(self, bars_by_symbol: dict[str, list[Bar]]):
        self.bars_by_symbol = bars_by_symbol

    def get_bars(self, symbol, interval="1d", start=None, end=None):
        return list(self.bars_by_symbol.get(symbol, []))


def make_bar(symbol: str, day: int, close: float, quality=DataQuality.OK) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=datetime(2024, 1, day, tzinfo=IST),
        open=close, high=close + 1, low=close - 1, close=close,
        volume=1000, source="test-double",
        retrieved_at=datetime(2024, 1, day, tzinfo=IST),
        interval="1d", quality=quality,
    )


def small_config(**risk_overrides) -> AppConfig:
    return AppConfig(
        starting_cash=100_000,
        risk=RiskLimits(**risk_overrides) if risk_overrides else RiskLimits(),
        swing=SwingStrategyConfig(fast_ma_period=2, slow_ma_period=4, max_holding_days=5),
    )


def test_end_to_end_run_on_bundled_sample_data():
    config = AppConfig()
    result = run_backtest(
        provider=CsvProvider(SAMPLES_DIR),
        symbols=["SAMPLE_TCS", "SAMPLE_INFY"],
        strategy=SwingMAStrategy(config.swing),
        config=config,
    )
    assert len(result.signals) > 0
    assert result.metrics.starting_cash == 100_000
    assert result.reconciliation_problems == []
    # Every trade's net P&L must be worse than gross (costs never help).
    for trade in result.trades:
        assert trade.net_pnl < trade.gross_pnl


def test_no_trades_when_no_crossover_ever_occurs():
    config = small_config()
    flat_bars = [make_bar("FLAT", d, 100.0) for d in range(1, 15)]
    result = run_backtest(
        provider=ListProvider({"FLAT": flat_bars}),
        symbols=["FLAT"],
        strategy=SwingMAStrategy(config.swing),
        config=config,
    )
    assert result.metrics.trade_count == 0
    assert all(s.action == SignalAction.NO_TRADE for s in result.signals)


def test_invalid_data_never_becomes_a_trade():
    """A data error must not turn into a buy/sell signal (CLAUDE.md section 12)."""
    config = small_config()
    # Shape that would normally trigger a crossover, but every bar is INVALID.
    closes = [110, 108, 106, 104, 102, 120, 130]
    bars = [make_bar("BAD", i + 1, c, quality=DataQuality.INVALID) for i, c in enumerate(closes)]
    result = run_backtest(
        provider=ListProvider({"BAD": bars}),
        symbols=["BAD"],
        strategy=SwingMAStrategy(config.swing),
        config=config,
    )
    assert result.metrics.trade_count == 0
    assert all(s.action == SignalAction.NO_TRADE for s in result.signals)


def test_kill_switch_prevents_any_entry_in_a_full_run():
    config = small_config(kill_switch_enabled=True)
    closes = [110, 108, 106, 104, 102, 120, 130, 140, 150]
    bars = [make_bar("UP", i + 1, c) for i, c in enumerate(closes)]
    result = run_backtest(
        provider=ListProvider({"UP": bars}),
        symbols=["UP"],
        strategy=SwingMAStrategy(config.swing),
        config=config,
    )
    assert result.metrics.trade_count == 0


def test_positions_open_at_end_are_counted_in_equity_not_as_closed_trades():
    config = small_config()
    closes = [110, 108, 106, 104, 102, 120, 130]  # crossover up at bar 6, no exit after
    bars = [make_bar("UP", i + 1, c) for i, c in enumerate(closes)]
    result = run_backtest(
        provider=ListProvider({"UP": bars}),
        symbols=["UP"],
        strategy=SwingMAStrategy(config.swing),
        config=config,
    )
    assert result.metrics.trade_count == 0  # never closed -> not a completed trade
    assert result.metrics.final_equity > 0


def test_backtest_result_records_data_source():
    config = AppConfig()
    result = run_backtest(
        provider=CsvProvider(SAMPLES_DIR),
        symbols=["SAMPLE_TCS"],
        strategy=SwingMAStrategy(config.swing),
        config=config,
    )
    assert result.source_summary["SAMPLE_TCS"].startswith("csv:")


def test_drawdown_computation():
    assert compute_equity_curve_drawdown([]) == 0.0
    assert compute_equity_curve_drawdown([100]) == 0.0
    assert round(compute_equity_curve_drawdown([100, 110, 100]), 6) == round(10 / 110, 6)
    assert round(compute_equity_curve_drawdown([100, 50]), 4) == 0.5
    assert compute_equity_curve_drawdown([100, 110, 120]) == 0.0


def test_benchmark_returns_none_for_empty_data():
    assert compute_buy_and_hold_benchmark({"X": []}) is None


def test_benchmark_averages_symbol_returns():
    bars_a = [make_bar("A", 1, 100), make_bar("A", 2, 110)]  # +10%
    bars_b = [make_bar("B", 1, 100), make_bar("B", 2, 90)]  # -10%
    benchmark = compute_buy_and_hold_benchmark({"A": bars_a, "B": bars_b})
    assert abs(benchmark) < 1e-9  # +10% and -10% average to ~0
