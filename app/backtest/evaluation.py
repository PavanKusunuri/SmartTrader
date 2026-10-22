"""Out-of-sample and segmented (walk-forward style) evaluation.

Why this module exists: a single backtest number over one period is close to
meaningless. CLAUDE.md section 8 requires out-of-sample and walk-forward
evaluation before ANY performance conclusion.

**Be precise about what this does and does not do.** No parameter fitting
happens anywhere in this project yet. So:

- `run_in_out_of_sample` splits the timeline once and evaluates the SAME
  fixed parameters on both halves. That tests *stability across periods*,
  which is weaker than true out-of-sample validation of a fitted model, but
  it is the honest version of what the data supports.
- `run_segmented` does the same across N contiguous windows. This is
  **walk-forward-style segmentation, NOT walk-forward optimisation**: there
  is no in-sample re-fitting step between windows, because nothing is fit.
  Calling it "walk-forward optimised" would be a lie.

Each segment is run independently with a fresh portfolio and the full
starting cash, so segment results are comparable to each other.

Known cost of independent segments: each segment must re-warm its
indicators, so roughly the first `slow_ma_period` bars of every segment can
produce no signal. Shorter segments therefore have proportionally less
tradable time. This biases segment trade counts downward and is reported
rather than hidden.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from app.backtest.engine import BacktestResult, run_backtest
from app.config import AppConfig
from app.data.base import DataProvider
from app.market_calendar import MarketSession
from app.strategies.base import Strategy

StrategyFactory = Callable[[], Strategy]


@dataclass
class Segment:
    label: str
    start: date
    end: date
    result: BacktestResult
    # True when the segment held fewer bars than the strategy needs to warm
    # up its indicators. Such a segment CANNOT produce a signal, so its
    # 0 trades say nothing about the strategy.
    warmup_starved: bool = False

    @property
    def net_return_pct(self) -> float:
        return self.result.metrics.total_return_net_pct

    @property
    def trade_count(self) -> int:
        return self.result.metrics.trade_count


@dataclass
class EvaluationReport:
    strategy_name: str
    segments: list[Segment]
    caveats: list[str]

    @property
    def total_trades(self) -> int:
        return sum(s.trade_count for s in self.segments)

    @property
    def segments_profitable(self) -> int:
        return sum(1 for s in self.segments if s.net_return_pct > 0)

    @property
    def usable_segments(self) -> list[Segment]:
        """Segments that had enough bars to actually warm up and trade."""
        return [s for s in self.segments if not s.warmup_starved]

    @property
    def is_consistent(self) -> bool:
        """True only if every USABLE segment was net positive. Deliberately
        strict: one good segment and one bad one is not consistency. Returns
        False when no segment was usable, because nothing was demonstrated."""
        usable = self.usable_segments
        if not usable:
            return False
        return all(s.net_return_pct > 0 for s in usable)

    @property
    def is_inconclusive(self) -> bool:
        """True when the evaluation could not test the strategy at all."""
        return not self.usable_segments or self.total_trades == 0


def _collect_dates(
    provider: DataProvider, symbols: list[str], interval: str,
    start: str | None, end: str | None,
) -> list[date]:
    dates: set[date] = set()
    for symbol in symbols:
        for bar in provider.get_bars(symbol, interval=interval, start=start, end=end):
            dates.add(bar.timestamp.date())
    return sorted(dates)


def _run_window(
    provider: DataProvider, symbols: list[str], strategy_factory: StrategyFactory,
    config: AppConfig, window_start: date, window_end: date, interval: str,
    session: MarketSession | None, label: str,
) -> Segment:
    strategy = strategy_factory()  # fresh instance per window
    result = run_backtest(
        provider=provider,
        symbols=symbols,
        strategy=strategy,
        config=config,
        start=window_start.isoformat(),
        end=window_end.isoformat(),
        interval=interval,
        session=session,
    )

    # Did any symbol in this window have enough bars to warm up the indicators?
    required = getattr(strategy, "required_history", 2)
    bars_per_symbol = [
        sum(1 for s in result.signals if s.symbol == symbol) for symbol in symbols
    ]
    starved = bool(bars_per_symbol) and max(bars_per_symbol, default=0) < required

    return Segment(
        label=label, start=window_start, end=window_end,
        result=result, warmup_starved=starved,
    )


def run_in_out_of_sample(
    provider: DataProvider,
    symbols: list[str],
    strategy_factory: StrategyFactory,
    config: AppConfig,
    split_fraction: float = 0.7,
    interval: str = "1d",
    session: MarketSession | None = None,
    start: str | None = None,
    end: str | None = None,
) -> EvaluationReport:
    """Splits the available dates chronologically and evaluates both halves.

    The split is by TIME, never randomly — shuffling time series rows would
    leak future information into the training period.
    """

    if not 0 < split_fraction < 1:
        raise ValueError("split_fraction must be strictly between 0 and 1")

    dates = _collect_dates(provider, symbols, interval, start, end)
    if len(dates) < 2:
        raise ValueError("need at least 2 distinct dates to split in/out of sample")

    split_idx = max(1, min(len(dates) - 1, int(len(dates) * split_fraction)))
    in_sample_dates = dates[:split_idx]
    out_sample_dates = dates[split_idx:]

    segments = [
        _run_window(
            provider, symbols, strategy_factory, config,
            in_sample_dates[0], in_sample_dates[-1], interval, session, "in-sample",
        ),
        _run_window(
            provider, symbols, strategy_factory, config,
            out_sample_dates[0], out_sample_dates[-1], interval, session, "out-of-sample",
        ),
    ]

    caveats = [
        "No parameters were fitted on the in-sample period, so this measures "
        "stability across periods, not validation of a fitted model.",
        "Each segment re-warms its indicators, so early bars in each segment "
        "cannot generate signals.",
    ]
    strategy = strategy_factory()
    caveats.extend(_warmup_caveats(segments, strategy, len(segments)))
    return EvaluationReport(
        strategy_name=getattr(strategy, "name", "unknown"),
        segments=segments,
        caveats=caveats,
    )


def run_segmented(
    provider: DataProvider,
    symbols: list[str],
    strategy_factory: StrategyFactory,
    config: AppConfig,
    num_windows: int = 4,
    interval: str = "1d",
    session: MarketSession | None = None,
    start: str | None = None,
    end: str | None = None,
) -> EvaluationReport:
    """Evaluates the same fixed parameters across `num_windows` contiguous
    time windows. Walk-forward-STYLE segmentation; no re-fitting occurs."""

    if num_windows < 2:
        raise ValueError("num_windows must be at least 2")

    dates = _collect_dates(provider, symbols, interval, start, end)
    if len(dates) < num_windows * 2:
        raise ValueError(
            f"need at least {num_windows * 2} distinct dates for {num_windows} windows, "
            f"have {len(dates)}"
        )

    window_size = len(dates) // num_windows
    segments: list[Segment] = []
    for i in range(num_windows):
        lo = i * window_size
        hi = (i + 1) * window_size - 1 if i < num_windows - 1 else len(dates) - 1
        segments.append(
            _run_window(
                provider, symbols, strategy_factory, config,
                dates[lo], dates[hi], interval, session, f"window {i + 1}/{num_windows}",
            )
        )

    caveats = [
        "Walk-forward-STYLE segmentation only: no parameters are re-fitted "
        "between windows, because nothing is fitted at all.",
        "Each window re-warms its indicators, reducing tradable bars per window.",
        "Short windows produce small trade counts, which are not statistically "
        "meaningful on their own.",
    ]
    strategy = strategy_factory()
    caveats.extend(_warmup_caveats(segments, strategy, num_windows))
    return EvaluationReport(
        strategy_name=getattr(strategy, "name", "unknown"),
        segments=segments,
        caveats=caveats,
    )


def _warmup_caveats(segments: list[Segment], strategy: Strategy, num_windows: int) -> list[str]:
    """Turns "0 trades" into an explicit diagnosis when the real cause is
    that the windows were shorter than the indicator warmup."""

    starved = [s for s in segments if s.warmup_starved]
    if not starved:
        return []

    required = getattr(strategy, "required_history", 2)
    if len(starved) == len(segments):
        return [
            f"RESULT IS NOT USABLE: every window was shorter than the "
            f"{required} bars {getattr(strategy, 'name', 'this strategy')} needs to warm "
            "up its indicators, so no window could produce a single signal. The zeros "
            "above measure the window length, NOT the strategy. Re-run with fewer "
            f"windows (e.g. --windows 2), a longer dataset, or shorter MA periods."
        ]
    return [
        f"{len(starved)} of {num_windows} windows were shorter than the {required} bars "
        "needed to warm up indicators; those windows could not trade at all and their "
        "zeros should be ignored."
    ]


# --- Honest strategy comparison --------------------------------------------

MIN_TRADES_FOR_ANY_CONCLUSION = 30


@dataclass
class ComparisonRow:
    strategy_name: str
    net_return_pct: float
    gross_return_pct: float
    trade_count: int
    win_rate: float | None
    expectancy: float | None
    max_drawdown_pct: float
    total_costs: float
    ambiguous_trades: int
    benchmark_return_pct: float | None


def build_comparison(results: dict[str, BacktestResult]) -> tuple[list[ComparisonRow], list[str]]:
    """Builds a side-by-side comparison plus the caveats that must accompany it.

    Deliberately returns caveats as data rather than printing a bare table:
    CLAUDE.md forbids picking a winner on gross return alone, and a table
    without its sample-size warnings invites exactly that mistake.
    """

    rows = [
        ComparisonRow(
            strategy_name=name,
            net_return_pct=r.metrics.total_return_net_pct,
            gross_return_pct=r.metrics.total_return_gross_pct,
            trade_count=r.metrics.trade_count,
            win_rate=r.metrics.win_rate,
            expectancy=r.metrics.expectancy,
            max_drawdown_pct=r.metrics.max_drawdown_pct,
            total_costs=r.metrics.total_costs,
            ambiguous_trades=r.ambiguous_trade_count,
            benchmark_return_pct=r.metrics.benchmark_return_pct,
        )
        for name, r in results.items()
    ]

    caveats: list[str] = []
    for row in rows:
        if row.trade_count < MIN_TRADES_FOR_ANY_CONCLUSION:
            caveats.append(
                f"{row.strategy_name}: only {row.trade_count} trade(s) — far below the "
                f"{MIN_TRADES_FOR_ANY_CONCLUSION} needed for even a weak conclusion. "
                "Treat this result as noise."
            )
        if row.ambiguous_trades:
            caveats.append(
                f"{row.strategy_name}: {row.ambiguous_trades} trade(s) had an ambiguous "
                "intrabar stop/target ordering; the conservative outcome was assumed."
            )
        if row.benchmark_return_pct is not None and row.net_return_pct < row.benchmark_return_pct:
            caveats.append(
                f"{row.strategy_name}: net return {row.net_return_pct:.2%} UNDERPERFORMED "
                f"buy-and-hold ({row.benchmark_return_pct:.2%})."
            )

    caveats.append(
        "Strategies run on different bar intervals are not directly comparable: "
        "intraday and daily strategies face different cost and slippage burdens."
    )
    return rows, caveats


def can_declare_winner(rows: list[ComparisonRow]) -> tuple[bool, str]:
    """Returns whether the evidence supports naming a better strategy at all.

    Returns False in most realistic cases. That is the point — this function
    exists to stop a tempting-looking table from becoming a claim.
    """

    if len(rows) < 2:
        return False, "fewer than two strategies compared"

    if any(r.trade_count < MIN_TRADES_FOR_ANY_CONCLUSION for r in rows):
        return False, (
            "at least one strategy has too few trades for a meaningful comparison "
            f"(minimum {MIN_TRADES_FOR_ANY_CONCLUSION})"
        )

    if len({round(r.net_return_pct, 10) for r in rows}) == 1:
        return False, "net returns are identical"

    best = max(rows, key=lambda r: r.net_return_pct)
    runner_up = sorted(rows, key=lambda r: r.net_return_pct, reverse=True)[1]
    margin = best.net_return_pct - runner_up.net_return_pct
    if margin < 0.02:
        return False, (
            f"margin between best and second ({margin:.2%}) is too small to be "
            "distinguishable from noise"
        )

    return True, (
        f"{best.strategy_name} led on NET return by {margin:.2%} with "
        f"{best.trade_count} trades — still only on this dataset, not a forecast"
    )
