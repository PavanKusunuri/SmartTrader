"""Causal volume and liquidity analytics.

Every function sees only the bars it is given; callers pass bars up to and
including the decision bar. Volume is used ONLY when present and valid
(volume > 0 on a non-INVALID bar). High volume is participation, not a
prediction of direction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from app.models import Bar, DataQuality


def avg_volume(bars: Sequence[Bar], lookback: int) -> Optional[float]:
    """Mean volume of the `lookback` bars BEFORE the last bar (the last bar is
    excluded so it is not compared against itself). None if not enough history."""
    prior = list(bars[:-1])[-lookback:]
    if lookback < 1 or len(prior) < lookback:
        return None
    return sum(b.volume for b in prior) / lookback


def relative_volume(bars: Sequence[Bar], lookback: int = 20) -> Optional[float]:
    """Last bar's volume / mean volume of the prior `lookback` bars."""
    avg = avg_volume(bars, lookback)
    if not bars or avg is None or avg <= 0:
        return None
    return bars[-1].volume / avg


def is_volume_spike(bars: Sequence[Bar], lookback: int = 20, threshold: float = 2.0) -> bool:
    rv = relative_volume(bars, lookback)
    return rv is not None and rv >= threshold


def avg_traded_value(bars: Sequence[Bar], lookback: int = 20) -> Optional[float]:
    """Mean of close*volume over the last `lookback` bars (proxy; exchange
    turnover is preferred when available)."""
    tail = list(bars)[-lookback:]
    if lookback < 1 or len(tail) < lookback:
        return None
    return sum(b.close * b.volume for b in tail) / lookback


def trailing_zero_volume_days(bars: Sequence[Bar]) -> int:
    n = 0
    for b in reversed(bars):
        if b.volume == 0:
            n += 1
        else:
            break
    return n


def breakout_with_volume(
    bars: Sequence[Bar], breakout_lookback: int = 20,
    volume_lookback: int = 20, min_relative_volume: float = 1.5,
) -> tuple[bool, str]:
    """Close above the prior `breakout_lookback`-bar high AND relative volume
    >= threshold. Prior high excludes the current bar (no self-reference)."""
    if len(bars) < max(breakout_lookback, volume_lookback) + 1:
        return False, "insufficient history"
    prior_high = max(b.high for b in bars[-breakout_lookback - 1:-1])
    rv = relative_volume(bars, volume_lookback)
    if rv is None:
        return False, "relative volume unavailable"
    broke = bars[-1].close > prior_high
    if broke and rv >= min_relative_volume:
        return True, f"close {bars[-1].close} > prior {breakout_lookback}-bar high {prior_high}; rel vol {rv:.2f}"
    return False, f"breakout={broke}, rel vol {rv:.2f} (need >= {min_relative_volume})"


# --- universe eligibility ---------------------------------------------------

@dataclass(frozen=True)
class UniverseCriteria:
    """All thresholds are visible, configurable ASSUMPTIONS, not verified facts."""

    min_price: float = 20.0
    penny_price: float = 50.0            # flagged for extra review, not rejected
    min_avg_volume: float = 100_000
    min_avg_traded_value: float = 2e7    # Rs
    lookback: int = 20
    min_history_bars: int = 60
    max_zero_volume_days: int = 1
    spike_threshold: float = 2.0
    max_age_days: int = 4


@dataclass(frozen=True)
class UniverseRow:
    symbol: str
    exchange: str
    eligible: bool
    reasons: tuple[str, ...]        # why excluded, or why included
    flags: tuple[str, ...]          # risk flags that do not exclude
    latest_data_ts: str | None
    price: float | None
    volume: int | None
    avg_traded_value: float | None
    relative_volume: float | None
    scan_ts: str


def evaluate_universe(
    universe: dict[str, list[Bar]],
    as_of_date,
    scan_ts: str,
    criteria: UniverseCriteria | None = None,
    exchange: str = "NSE",
) -> list[UniverseRow]:
    """One row per symbol, always: nothing is silently dropped."""
    c = criteria or UniverseCriteria()
    rows: list[UniverseRow] = []
    for sym in sorted(universe):
        bars = universe[sym]
        if not bars:
            rows.append(UniverseRow(sym, exchange, False, ("no data",), (), None, None, None, None, None, scan_ts))
            continue
        last = bars[-1]
        excl: list[str] = []
        flags: list[str] = []
        if last.quality == DataQuality.INVALID:
            excl.append(f"latest bar invalid: {'; '.join(last.quality_notes)}")
        if last.quality == DataQuality.SUSPECT_GAP:
            flags.append("suspect gap (possible unadjusted corporate action)")
        age = (as_of_date - last.timestamp.date()).days
        if age > c.max_age_days:
            excl.append(f"stale: latest bar {age} days old (limit {c.max_age_days})")
        if len(bars) < c.min_history_bars:
            excl.append(f"insufficient history: {len(bars)} < {c.min_history_bars} bars")
        if last.close < c.min_price:
            excl.append(f"price {last.close} < minimum {c.min_price}")
        elif last.close < c.penny_price:
            flags.append(f"low-priced (< {c.penny_price}): extra risk review")
        zdays = trailing_zero_volume_days(bars)
        if zdays > c.max_zero_volume_days:
            excl.append(f"{zdays} consecutive zero-volume sessions")
        av = avg_volume(bars, c.lookback)
        atv = avg_traded_value(bars, c.lookback)
        if av is not None and av < c.min_avg_volume:
            excl.append(f"avg volume {av:,.0f} < {c.min_avg_volume:,.0f}")
        if atv is not None and atv < c.min_avg_traded_value:
            excl.append(f"avg traded value {atv:,.0f} < {c.min_avg_traded_value:,.0f}")
        if av is None or atv is None:
            excl.append("not enough history for volume/liquidity averages")
        rv = relative_volume(bars, c.lookback)
        if rv is not None and rv >= c.spike_threshold:
            flags.append(f"volume spike ({rv:.1f}x average)")
        flags.append("surveillance/restriction status not checked (no reliable data source)")
        reasons = tuple(excl) if excl else (
            f"passed: price {last.close}, avg volume {av:,.0f}, avg traded value {atv:,.0f}",
        )
        rows.append(UniverseRow(
            sym, exchange, not excl, reasons, tuple(flags), last.timestamp.isoformat(),
            last.close, last.volume, atv, rv, scan_ts,
        ))
    return rows
