"""Stages C and ranking: swing setup discovery and a transparent score.

PAPER TRADING -- NO REAL ORDERS. This module only ranks; it places nothing.

The pipeline for one decision date is:
  1. Regime gate: if the regime allows no long setups -> NO_TRADE.
  2. Hard tradability rejections (filters.py) -> rejected, never scored.
  3. Setup detection (BREAKOUT / PULLBACK), only those the regime allows.
  4. ATR-based invalidation and a minimum reward:risk requirement.
  5. Score, then rank.

The score is a RANKING aid on a 0-100 scale. It is NOT a probability of
winning and must never be displayed as one. Weights are untuned baselines.

Causality: every input is sliced to timestamp <= as_of. The entry zone is a
reference only; a real fill would be at a later price (see the backtest
engine's next-bar-open model).

News: no news source exists, so every candidate is labelled NOT_NEWS_VERIFIED
and that is surfaced as a limitation rather than hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from app.models import Bar
from app.selection.filters import Rejection, TradabilityConfig, check_tradability
from app.selection.indicators import atr, median, pct_return, sma
from app.selection.regime import Regime, RegimeAssessment, RegimeConfig, detect_regime

EVENT_STATUS = "NOT_NEWS_VERIFIED"


@dataclass(frozen=True)
class RankingConfig:
    breakout_lookback: int = 20
    breakout_volume_multiple: float = 1.5
    pullback_ma: int = 20
    pullback_atr_band: float = 1.0
    rs_lookback: int = 20
    atr_period: int = 14
    stop_atr_multiple: float = 2.0
    target_rr: float = 2.0
    min_reward_risk: float = 1.5
    # Skip chasing: reject a breakout already this many ATRs above the level.
    max_extension_atr: float = 1.5
    max_candidates: int = 5
    weights: tuple[tuple[str, float], ...] = (
        ("trend_quality", 0.25),
        ("relative_strength", 0.25),
        ("relative_volume", 0.20),
        ("volatility_suitability", 0.15),
        ("liquidity", 0.15),
    )


@dataclass(frozen=True)
class Candidate:
    rank: int
    symbol: str
    score: float
    setup: str
    bias: str
    strategy: str
    qualified_because: tuple[str, ...]
    score_detractors: tuple[str, ...]
    components: dict
    rejection_checks_passed: tuple[str, ...]
    data_source: str
    data_as_of: datetime
    entry_zone: tuple[float, float]
    stop_loss: float
    target: float
    reward_risk: float
    event_risk_status: str
    limitations: tuple[str, ...]
    reasons_to_avoid: tuple[str, ...]


@dataclass(frozen=True)
class SelectionResult:
    as_of: date
    regime: RegimeAssessment
    candidates: tuple[Candidate, ...]
    rejections: tuple[Rejection, ...]
    no_trade: bool
    no_trade_reason: str | None


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _detect_setup(bars: list[Bar], cfg: RankingConfig, allowed: frozenset[str], atr_v: float):
    """Returns (setup, reasons, detractors, avoid) or None. Uses bars up to now."""
    closes = [b.close for b in bars]
    last = bars[-1]
    reasons: list[str] = []
    detractors: list[str] = []
    avoid: list[str] = []

    if "BREAKOUT" in allowed and len(bars) > cfg.breakout_lookback:
        prior = bars[-1 - cfg.breakout_lookback:-1]
        level = max(b.high for b in prior)
        avg_vol = sum(b.volume for b in prior) / len(prior)
        if last.close > level and avg_vol > 0 and last.volume >= cfg.breakout_volume_multiple * avg_vol:
            extension = (last.close - level) / atr_v
            if extension > cfg.max_extension_atr:
                return None  # already extended: do not chase
            reasons.append(
                f"close {last.close:.2f} above prior {cfg.breakout_lookback}-bar high {level:.2f} "
                f"on volume x{last.volume / avg_vol:.1f}"
            )
            if extension > 1.0:
                detractors.append(f"breakout is {extension:.1f} ATR above the level; entry is getting extended")
            avoid.append("breakouts can fail and reverse; the invalidation level is below the entry")
            return "BREAKOUT", reasons, detractors, avoid

    if "PULLBACK" in allowed:
        ma = sma(closes, cfg.pullback_ma)
        slow = sma(closes, 50)
        if ma is not None and slow is not None and ma > slow and len(closes) > 1:
            near = abs(last.close - ma) <= cfg.pullback_atr_band * atr_v
            resumed = last.close > closes[-2]
            if near and resumed and last.close > slow:
                reasons.append(
                    f"pullback to MA{cfg.pullback_ma} {ma:.2f} within {cfg.pullback_atr_band} ATR "
                    "in an uptrend, with a higher close than the prior bar"
                )
                avoid.append("a single up-close is weak evidence that the pullback has ended")
                return "PULLBACK", reasons, detractors, avoid
    return None


def rank_swing_candidates(
    universe: dict[str, list[Bar]],
    index_bars: list[Bar],
    as_of: date,
    liquidity: dict[str, list] | None = None,
    regime_config: RegimeConfig | None = None,
    tradability_config: TradabilityConfig | None = None,
    config: RankingConfig | None = None,
) -> SelectionResult:
    cfg = config or RankingConfig()
    liquidity = liquidity or {}
    cutoff = lambda b: b.timestamp.date() <= as_of  # noqa: E731

    index_visible = [b for b in index_bars if cutoff(b)]
    regime = detect_regime(index_visible, regime_config)

    def no_trade(reason: str, rejections=()) -> SelectionResult:
        return SelectionResult(as_of, regime, (), tuple(rejections), True, reason)

    if not regime.allows_new_long_entries:
        return no_trade(f"regime {regime.regime.value}: {'; '.join(regime.reasons)}")

    rejections: list[Rejection] = []
    scored: list[dict] = []
    idx_closes = [b.close for b in index_visible]
    idx_ret = pct_return(idx_closes, cfg.rs_lookback)

    for symbol in sorted(universe):
        bars = [b for b in universe[symbol] if cutoff(b)]
        tr = check_tradability(symbol, bars, liquidity.get(symbol), as_of, tradability_config)
        if not tr.passed:
            rejections.extend(tr.rejections)
            continue

        atr_v = atr(bars, cfg.atr_period)
        if not atr_v or atr_v <= 0:
            rejections.append(Rejection(symbol, "NO_ATR", "ATR could not be computed"))
            continue

        found = _detect_setup(bars, cfg, regime.eligible_setups, atr_v)
        if found is None:
            rejections.append(Rejection(symbol, "NO_SETUP", "no eligible setup on this bar"))
            continue
        setup, reasons, detractors, avoid = found

        last = bars[-1]
        stop = round(last.close - cfg.stop_atr_multiple * atr_v, 2)
        risk = last.close - stop
        if stop <= 0 or risk <= 0:
            rejections.append(Rejection(symbol, "BAD_STOP", "invalidation level is not positive"))
            continue
        target = round(last.close + cfg.target_rr * risk, 2)
        reward_risk = (target - last.close) / risk
        if reward_risk < cfg.min_reward_risk:
            rejections.append(Rejection(symbol, "LOW_REWARD_RISK", f"{reward_risk:.2f} < {cfg.min_reward_risk}"))
            continue

        closes = [b.close for b in bars]
        fast, slow = sma(closes, 20), sma(closes, 50)
        trend = _clamp((fast - slow) / slow / 0.05) if fast and slow and slow > 0 else 0.0
        stock_ret = pct_return(closes, cfg.rs_lookback)
        if stock_ret is not None and idx_ret is not None:
            rs = _clamp(0.5 + (stock_ret - idx_ret) / 0.10)
            reasons.append(f"{cfg.rs_lookback}-bar return {stock_ret:+.1%} vs index {idx_ret:+.1%}")
        else:
            rs = 0.0
            detractors.append("relative strength could not be computed")
        recent_vol = [b.volume for b in bars[-21:-1]]
        rel_vol = _clamp((last.volume / (sum(recent_vol) / len(recent_vol)) - 1.0) / 2.0) if recent_vol and sum(recent_vol) else 0.0
        atr_pct = atr_v / last.close
        vol_fit = _clamp(1.0 - abs(atr_pct - 0.02) / 0.03)  # peak near 2% ATR; assumption
        liq_floor = (tradability_config or TradabilityConfig()).min_median_turnover
        liq = _clamp((tr.median_turnover or 0) / (5 * liq_floor))  # saturates at 5x the floor
        if tr.turnover_source == "proxy":
            detractors.append("liquidity measured by close*volume proxy, not exchange turnover")
            liq *= 0.5

        comps = {
            "trend_quality": trend, "relative_strength": rs, "relative_volume": rel_vol,
            "volatility_suitability": vol_fit, "liquidity": liq,
        }
        score = 100.0 * sum(comps[name] * w for name, w in cfg.weights)
        for name, value in comps.items():
            if value < 0.3:
                detractors.append(f"weak {name.replace('_', ' ')} ({value:.2f})")

        scored.append({
            "symbol": symbol, "score": score, "setup": setup, "reasons": reasons,
            "detractors": detractors, "avoid": avoid, "comps": comps, "bar": last,
            "stop": stop, "target": target, "rr": reward_risk, "atr": atr_v,
            "checks": ("data_quality", "history", "freshness", "gaps", "liquidity", "reward_risk"),
        })

    scored.sort(key=lambda s: (-s["score"], s["symbol"]))  # symbol breaks ties: order-stable
    top = scored[: cfg.max_candidates]
    for s in scored[cfg.max_candidates:]:
        rejections.append(Rejection(s["symbol"], "BELOW_CUTOFF", f"score {s['score']:.1f} outside top {cfg.max_candidates}"))
    if not top:
        return no_trade("no symbol passed tradability, setup and reward:risk checks", rejections)

    candidates = tuple(
        Candidate(
            rank=i, symbol=s["symbol"], score=round(s["score"], 2), setup=s["setup"], bias="LONG",
            strategy="swing_selection_v0", qualified_because=tuple(s["reasons"]),
            score_detractors=tuple(s["detractors"]), components=s["comps"],
            rejection_checks_passed=s["checks"], data_source=s["bar"].source,
            data_as_of=s["bar"].timestamp,
            entry_zone=(round(s["bar"].close, 2), round(s["bar"].close + 0.25 * s["atr"], 2)),
            stop_loss=s["stop"], target=s["target"], reward_risk=round(s["rr"], 2),
            event_risk_status=EVENT_STATUS,
            limitations=(
                "score is a ranking aid, not a probability of success",
                "news and corporate events not checked",
                "prices are unadjusted for corporate actions",
                "weights are untuned baselines; no out-of-sample evidence yet",
            ),
            reasons_to_avoid=tuple(s["avoid"]) + ("overnight gap can skip the stop",),
        )
        for i, s in enumerate(top, start=1)
    )
    return SelectionResult(as_of, regime, candidates, tuple(rejections), False, None)
