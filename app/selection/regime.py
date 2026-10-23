"""Stage A: market regime detection from an index series (e.g. Nifty 50).

The regime decides which setups are ELIGIBLE. It is deliberately
conservative: anything that is not a clean bullish trend with normal
volatility yields no eligible swing setups, so the engine returns NO_TRADE
instead of forcing a trade.

Thresholds are EXPERIMENTAL assumptions, not tuned or validated values.

What this does NOT do: market breadth. Breadth needs the whole constituent
set, and inferring it from a small watchlist would present a biased sample as
"the market", which the project brief forbids. Absent breadth is reported in
`limitations`, not silently ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.models import Bar, DataQuality
from app.selection.indicators import atr, daily_volatility, sma


class Regime(str, Enum):
    TRENDING_BULLISH = "TRENDING_BULLISH"
    TRENDING_BEARISH = "TRENDING_BEARISH"
    RANGE_BOUND = "RANGE_BOUND"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    UNCERTAIN = "UNCERTAIN"  # data insufficient or invalid


@dataclass(frozen=True)
class RegimeConfig:
    fast_ma: int = 20
    slow_ma: int = 50
    vol_window: int = 20
    # Daily log-return std above this is "high volatility". EXPERIMENTAL.
    high_daily_vol: float = 0.015
    # Latest-vs-longer-run vol ratio above this counts as volatility expansion.
    vol_expansion_ratio: float = 1.6
    long_vol_window: int = 60

    @property
    def min_bars(self) -> int:
        return max(self.slow_ma, self.long_vol_window) + 1


@dataclass(frozen=True)
class RegimeAssessment:
    regime: Regime
    reasons: tuple[str, ...]
    inputs: dict
    eligible_setups: frozenset[str]
    limitations: tuple[str, ...] = field(
        default=(
            "Market breadth not assessed: no constituent-wide dataset is available.",
            "Regime is inferred from a single index series only.",
        )
    )

    @property
    def allows_new_long_entries(self) -> bool:
        return bool(self.eligible_setups)


_BULLISH_SETUPS = frozenset({"BREAKOUT", "PULLBACK"})


def detect_regime(index_bars: list[Bar], config: RegimeConfig | None = None) -> RegimeAssessment:
    """Classifies the regime as of `index_bars[-1]`. Slice to the decision time first."""
    cfg = config or RegimeConfig()

    def uncertain(reason: str, inputs: dict | None = None) -> RegimeAssessment:
        return RegimeAssessment(Regime.UNCERTAIN, (reason,), inputs or {}, frozenset())

    if not index_bars:
        return uncertain("no index data supplied")
    if index_bars[-1].quality != DataQuality.OK:
        return uncertain(f"latest index bar quality is {index_bars[-1].quality.value}")
    if len(index_bars) < cfg.min_bars:
        return uncertain(f"insufficient index history: {len(index_bars)} < {cfg.min_bars} bars")

    closes = [b.close for b in index_bars]
    fast, slow = sma(closes, cfg.fast_ma), sma(closes, cfg.slow_ma)
    vol = daily_volatility(closes, cfg.vol_window)
    long_vol = daily_volatility(closes, cfg.long_vol_window)
    index_atr = atr(index_bars, 14)
    if None in (fast, slow, vol, long_vol, index_atr):
        return uncertain("an index indicator could not be computed")

    close = closes[-1]
    inputs = {
        "close": close, "fast_ma": fast, "slow_ma": slow, "daily_vol": vol,
        "long_daily_vol": long_vol, "atr_pct": index_atr / close,
    }

    expansion = vol / long_vol if long_vol else float("inf")
    inputs["vol_expansion_ratio"] = expansion
    if vol > cfg.high_daily_vol or expansion > cfg.vol_expansion_ratio:
        return RegimeAssessment(
            Regime.HIGH_VOLATILITY,
            (f"daily vol {vol:.2%} (limit {cfg.high_daily_vol:.2%}), "
             f"expansion x{expansion:.2f} (limit x{cfg.vol_expansion_ratio})",),
            inputs, frozenset(),
        )

    if close > slow and fast > slow:
        return RegimeAssessment(
            Regime.TRENDING_BULLISH,
            (f"close {close:.2f} > MA{cfg.slow_ma} {slow:.2f} and MA{cfg.fast_ma} {fast:.2f} > MA{cfg.slow_ma}",),
            inputs, _BULLISH_SETUPS,
        )
    if close < slow and fast < slow:
        return RegimeAssessment(
            Regime.TRENDING_BEARISH,
            ("index below a falling trend; this engine is long-only and does not short swing",),
            inputs, frozenset(),
        )
    return RegimeAssessment(
        Regime.RANGE_BOUND,
        ("index trend signals disagree; no directional edge assumed",),
        inputs, frozenset(),
    )
