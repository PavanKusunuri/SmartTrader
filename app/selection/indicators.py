"""Causal indicator helpers for the selection pipeline.

Every function takes only the data up to the decision point and returns None
when there is not enough history, never a guess. Callers must slice bars to
`timestamp <= decision time` BEFORE calling; nothing here can see the future
because nothing here is given it.
"""

from __future__ import annotations

import math
import statistics
from typing import Optional, Sequence

from app.models import Bar


def sma(values: Sequence[float], period: int) -> Optional[float]:
    if period < 1 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def median(values: Sequence[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def pct_return(closes: Sequence[float], lookback: int) -> Optional[float]:
    """Return over the last `lookback` bars: close[-1] / close[-1-lookback] - 1."""
    if lookback < 1 or len(closes) < lookback + 1:
        return None
    base = closes[-1 - lookback]
    if base <= 0:
        return None
    return closes[-1] / base - 1.0


def true_ranges(bars: Sequence[Bar]) -> list[float]:
    """True range per bar from the second bar on (needs the prior close)."""
    out: list[float] = []
    for prev, cur in zip(bars, bars[1:]):
        out.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
    return out


def atr(bars: Sequence[Bar], period: int = 14) -> Optional[float]:
    """Simple-mean ATR over the last `period` true ranges."""
    trs = true_ranges(bars)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def daily_volatility(closes: Sequence[float], window: int = 20) -> Optional[float]:
    """Standard deviation of daily log returns over the last `window` returns.

    Per-day, NOT annualised: annualising would add a 252-day assumption this
    project has no reason to bake in.
    """
    if len(closes) < window + 1:
        return None
    tail = closes[-(window + 1):]
    if any(c <= 0 for c in tail):
        return None
    rets = [math.log(b / a) for a, b in zip(tail, tail[1:])]
    return statistics.pstdev(rets)
