"""Stage B: hard tradability rejections, applied BEFORE any scoring.

A symbol that fails here can never be ranked, however strong its momentum.
Every rejection carries its reason so the dashboard can show why.

Thresholds are EXPERIMENTAL assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.models import Bar, DataQuality
from app.selection.indicators import median


@dataclass(frozen=True)
class TradabilityConfig:
    min_bars: int = 60
    liquidity_window: int = 20
    # Rupees. Assumption: roughly Rs 5 crore median daily traded value.
    min_median_turnover: float = 5e7
    # Without real turnover we only have close*volume, a cruder proxy, so a
    # higher bar applies. This is the conservative fallback.
    proxy_turnover_multiple: float = 1.5
    min_median_trades: int = 500
    max_abs_gap: float = 0.10
    gap_window: int = 20
    max_bar_age_days: int = 4


@dataclass(frozen=True)
class Rejection:
    symbol: str
    rule: str
    detail: str


@dataclass(frozen=True)
class TradabilityResult:
    symbol: str
    passed: bool
    rejections: tuple[Rejection, ...]
    notes: tuple[str, ...]
    median_turnover: float | None
    turnover_source: str  # "exchange" | "proxy" | "none"


def check_tradability(
    symbol: str,
    bars: list[Bar],
    liquidity: list | None,
    as_of: date,
    config: TradabilityConfig | None = None,
) -> TradabilityResult:
    """`liquidity` is a list of objects with .turnover and .trade_count
    (e.g. bhavcopy LiquidityRecord), or None/empty when unknown."""
    cfg = config or TradabilityConfig()
    rejections: list[Rejection] = []
    notes: list[str] = []

    def reject(rule: str, detail: str) -> None:
        rejections.append(Rejection(symbol, rule, detail))

    if not bars:
        reject("NO_DATA", "no bars supplied")
        return TradabilityResult(symbol, False, tuple(rejections), (), None, "none")

    last = bars[-1]
    if last.quality != DataQuality.OK:
        reject("BAD_DATA_QUALITY", f"latest bar is {last.quality.value}: {'; '.join(last.quality_notes)}")
    if len(bars) < cfg.min_bars:
        reject("INSUFFICIENT_HISTORY", f"{len(bars)} bars < {cfg.min_bars}")

    age = (as_of - last.timestamp.date()).days
    if age > cfg.max_bar_age_days:
        reject("STALE", f"latest bar is {age} days old (limit {cfg.max_bar_age_days})")

    recent = bars[-cfg.gap_window:]
    suspect = [b for b in recent if b.quality == DataQuality.SUSPECT_GAP]
    if suspect:
        reject(
            "SUSPECT_GAP",
            f"{len(suspect)} abnormal gap bar(s) in last {cfg.gap_window}; may be an "
            "unadjusted split/bonus or a real event, either way unreliable",
        )
    big = [
        b for prev, b in zip(recent, recent[1:])
        if prev.close > 0 and abs(b.open - prev.close) / prev.close > cfg.max_abs_gap
    ]
    if big:
        reject("EXCESSIVE_GAP", f"{len(big)} gap(s) over {cfg.max_abs_gap:.0%} in last {cfg.gap_window} bars")

    median_turnover: float | None = None
    source = "none"
    window = (liquidity or [])[-cfg.liquidity_window:]
    if len(window) >= cfg.liquidity_window // 2:
        median_turnover = median([r.turnover for r in window])
        source = "exchange"
        med_trades = median([r.trade_count for r in window])
        if median_turnover < cfg.min_median_turnover:
            reject("ILLIQUID", f"median traded value {median_turnover:,.0f} < {cfg.min_median_turnover:,.0f}")
        if med_trades is not None and med_trades < cfg.min_median_trades:
            reject("FEW_TRADES", f"median trades/day {med_trades:,.0f} < {cfg.min_median_trades}")
    else:
        proxy = median([b.close * b.volume for b in bars[-cfg.liquidity_window:]])
        median_turnover, source = proxy, "proxy"
        needed = cfg.min_median_turnover * cfg.proxy_turnover_multiple
        notes.append("exchange turnover unavailable; using close*volume proxy with a stricter threshold")
        if proxy is None or proxy < needed:
            reject("ILLIQUID", f"proxy traded value {proxy or 0:,.0f} < {needed:,.0f} (conservative fallback)")

    return TradabilityResult(symbol, not rejections, tuple(rejections), tuple(notes), median_turnover, source)
