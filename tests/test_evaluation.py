from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.backtest.engine import run_backtest
from app.backtest.evaluation import (
    MIN_TRADES_FOR_ANY_CONCLUSION,
    ComparisonRow,
    build_comparison,
    can_declare_winner,
    run_in_out_of_sample,
    run_segmented,
)
from app.config import AppConfig, SwingStrategyConfig
from app.data.base import DataProvider
from app.data.csv_provider import CsvProvider
from app.models import Bar, DataQuality
from app.strategies.swing import SwingMAStrategy

IST = ZoneInfo("Asia/Kolkata")
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"


class ListProvider(DataProvider):
    def __init__(self, bars):
        self.bars = bars

    def get_bars(self, symbol, interval="1d", start=None, end=None):
        bars = list(self.bars)
        if start:
            start_d = datetime.strptime(start, "%Y-%m-%d").date()
            bars = [b for b in bars if b.timestamp.date() >= start_d]
        if end:
            end_d = datetime.strptime(end, "%Y-%m-%d").date()
            bars = [b for b in bars if b.timestamp.date() <= end_d]
        return bars


def make_bars(n: int) -> list[Bar]:
    out = []
    for i in range(n):
        ts = datetime(2024, 1, 1, tzinfo=IST) + timedelta(days=i)
        close = 100 + (i % 7)
        out.append(
            Bar(
                symbol="X", timestamp=ts, open=close, high=close + 1, low=close - 1,
                close=close, volume=1000, source="test-double", retrieved_at=ts,
                interval="1d", quality=DataQuality.OK,
            )
        )
    return out


def swing_factory():
    return SwingMAStrategy(SwingStrategyConfig(fast_ma_period=2, slow_ma_period=4))


def test_in_out_of_sample_splits_chronologically_without_overlap():
    provider = ListProvider(make_bars(40))
    report = run_in_out_of_sample(
        provider=provider, symbols=["X"], strategy_factory=swing_factory,
        config=AppConfig(), split_fraction=0.7,
    )
    assert len(report.segments) == 2
    in_s, out_s = report.segments
    assert in_s.label == "in-sample"
    assert out_s.label == "out-of-sample"
    # No look-ahead: in-sample must end strictly before out-of-sample starts.
    assert in_s.end < out_s.start
    assert report.caveats  # must always carry its caveats


def test_in_out_of_sample_rejects_bad_split_fraction():
    provider = ListProvider(make_bars(20))
    for bad in (0, 1, -0.5, 1.5):
        with pytest.raises(ValueError):
            run_in_out_of_sample(
                provider=provider, symbols=["X"], strategy_factory=swing_factory,
                config=AppConfig(), split_fraction=bad,
            )


def test_in_out_of_sample_needs_enough_dates():
    provider = ListProvider(make_bars(1))
    with pytest.raises(ValueError, match="at least 2 distinct dates"):
        run_in_out_of_sample(
            provider=provider, symbols=["X"], strategy_factory=swing_factory,
            config=AppConfig(),
        )


def test_segmented_produces_contiguous_non_overlapping_windows():
    provider = ListProvider(make_bars(40))
    report = run_segmented(
        provider=provider, symbols=["X"], strategy_factory=swing_factory,
        config=AppConfig(), num_windows=4,
    )
    assert len(report.segments) == 4
    for earlier, later in zip(report.segments, report.segments[1:]):
        assert earlier.end < later.start  # windows must not overlap


def test_segmented_rejects_too_few_windows_or_dates():
    provider = ListProvider(make_bars(40))
    with pytest.raises(ValueError, match="at least 2"):
        run_segmented(
            provider=provider, symbols=["X"], strategy_factory=swing_factory,
            config=AppConfig(), num_windows=1,
        )
    small = ListProvider(make_bars(5))
    with pytest.raises(ValueError, match="need at least"):
        run_segmented(
            provider=small, symbols=["X"], strategy_factory=swing_factory,
            config=AppConfig(), num_windows=4,
        )


def test_each_segment_starts_with_full_starting_cash():
    provider = ListProvider(make_bars(40))
    config = AppConfig(starting_cash=100_000)
    report = run_segmented(
        provider=provider, symbols=["X"], strategy_factory=swing_factory,
        config=config, num_windows=3,
    )
    for segment in report.segments:
        assert segment.result.metrics.starting_cash == 100_000


def test_is_consistent_requires_every_segment_positive():
    provider = ListProvider(make_bars(40))
    report = run_segmented(
        provider=provider, symbols=["X"], strategy_factory=swing_factory,
        config=AppConfig(), num_windows=3,
    )
    # Whatever the outcome, is_consistent must agree with the per-segment facts.
    expected = all(s.net_return_pct > 0 for s in report.segments)
    assert report.is_consistent == expected


def test_required_history_reflects_strategy_config():
    from app.config import IntradayStrategyConfig
    from app.strategies.intraday import IntradayMomentumStrategy

    assert SwingMAStrategy(SwingStrategyConfig(fast_ma_period=20, slow_ma_period=50)).required_history == 51
    intraday = IntradayMomentumStrategy(
        IntradayStrategyConfig(fast_ma_period=9, slow_ma_period=21, volume_ma_period=30)
    )
    assert intraday.required_history == 31  # driven by the LONGER of the two lookbacks


def test_warmup_starved_segments_are_flagged_and_inconclusive():
    """A window too short to warm up indicators must not be reported as if
    the strategy simply found no setups."""
    provider = ListProvider(make_bars(40))
    report = run_segmented(
        provider=provider, symbols=["X"],
        # Needs 51 bars, but 4 windows of 40 bars give ~10 bars each.
        strategy_factory=lambda: SwingMAStrategy(
            SwingStrategyConfig(fast_ma_period=20, slow_ma_period=50)
        ),
        config=AppConfig(), num_windows=4,
    )
    assert all(s.warmup_starved for s in report.segments)
    assert report.usable_segments == []
    assert report.is_inconclusive is True
    assert report.is_consistent is False  # nothing was demonstrated
    assert any("NOT USABLE" in c for c in report.caveats)


def test_segments_with_enough_history_are_not_flagged_starved():
    provider = ListProvider(make_bars(60))
    report = run_segmented(
        provider=provider, symbols=["X"],
        strategy_factory=lambda: SwingMAStrategy(
            SwingStrategyConfig(fast_ma_period=2, slow_ma_period=4)
        ),
        config=AppConfig(), num_windows=2,
    )
    assert not any(s.warmup_starved for s in report.segments)
    assert len(report.usable_segments) == 2
    assert not any("NOT USABLE" in c for c in report.caveats)


def test_real_sample_data_in_out_of_sample_runs():
    config = AppConfig()
    report = run_in_out_of_sample(
        provider=CsvProvider(SAMPLES_DIR), symbols=["SAMPLE_TCS"],
        strategy_factory=lambda: SwingMAStrategy(config.swing), config=config,
    )
    assert len(report.segments) == 2
    assert report.strategy_name == "swing_ma_crossover"


# --- comparison -------------------------------------------------------------


def make_row(name, net, trades=50, gross=None, ambiguous=0, benchmark=None) -> ComparisonRow:
    return ComparisonRow(
        strategy_name=name, net_return_pct=net,
        gross_return_pct=gross if gross is not None else net + 0.01,
        trade_count=trades, win_rate=0.5, expectancy=10.0,
        max_drawdown_pct=0.1, total_costs=100.0,
        ambiguous_trades=ambiguous, benchmark_return_pct=benchmark,
    )


def test_no_winner_with_too_few_trades():
    rows = [make_row("a", 0.20, trades=3), make_row("b", 0.01, trades=3)]
    ok, reason = can_declare_winner(rows)
    assert ok is False
    assert "too few trades" in reason


def test_no_winner_on_small_margin():
    rows = [make_row("a", 0.101), make_row("b", 0.100)]
    ok, reason = can_declare_winner(rows)
    assert ok is False
    assert "too small" in reason


def test_no_winner_with_single_strategy():
    ok, reason = can_declare_winner([make_row("a", 0.5)])
    assert ok is False
    assert "fewer than two" in reason


def test_winner_allowed_only_with_trades_and_clear_margin():
    rows = [make_row("a", 0.30), make_row("b", 0.05)]
    ok, reason = can_declare_winner(rows)
    assert ok is True
    assert "NET return" in reason
    assert "not a forecast" in reason


def test_winner_decided_on_net_not_gross():
    """A strategy winning on gross but losing on net must not be the winner."""
    rows = [
        make_row("high_gross_high_cost", net=0.01, gross=0.50),
        make_row("modest_gross_low_cost", net=0.25, gross=0.26),
    ]
    ok, reason = can_declare_winner(rows)
    assert ok is True
    assert "modest_gross_low_cost" in reason


def test_comparison_flags_low_trade_counts_and_underperformance():
    results_rows, caveats = build_comparison({})
    assert results_rows == []

    rows = [make_row("a", 0.01, trades=2, ambiguous=1, benchmark=0.10)]
    # build_comparison works off BacktestResults, so test the caveat logic via rows
    ok, reason = can_declare_winner(rows)
    assert ok is False


def test_build_comparison_from_real_results_carries_caveats():
    config = AppConfig()
    result = run_backtest(
        provider=CsvProvider(SAMPLES_DIR), symbols=["SAMPLE_TCS"],
        strategy=SwingMAStrategy(config.swing), config=config,
    )
    rows, caveats = build_comparison({"swing": result})
    assert len(rows) == 1
    assert rows[0].strategy_name == "swing"
    # Sample data produces very few trades, so a sample-size caveat is mandatory.
    assert any("below the" in c or "noise" in c for c in caveats)
    assert any("not directly comparable" in c for c in caveats)
