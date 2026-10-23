"""Tests for the selection pipeline: regime, tradability, ranking."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import Bar, DataQuality
from app.selection.filters import TradabilityConfig, check_tradability
from app.selection.indicators import atr, daily_volatility, pct_return, sma
from app.selection.ranking import RankingConfig, rank_swing_candidates
from app.selection.regime import Regime, detect_regime

IST = ZoneInfo("Asia/Kolkata")
START = datetime(2024, 1, 1, tzinfo=IST)


def make_bars(
    closes: list[float], volume: int = 1_000_000, symbol: str = "X",
    spread: float = 0.005, volumes: list[int] | None = None,
) -> list[Bar]:
    bars = []
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev
        hi = max(o, c) * (1 + spread)
        lo = min(o, c) * (1 - spread)
        bars.append(Bar(
            symbol=symbol, timestamp=START + timedelta(days=i), open=o, high=hi, low=lo,
            close=c, volume=(volumes[i] if volumes else volume), source="test",
            retrieved_at=START, interval="1d",
        ))
        prev = c
    return bars


def uptrend(n: int = 120, start: float = 100.0, step: float = 0.002) -> list[float]:
    out, p = [], start
    for i in range(n):
        p *= 1 + step + (0.001 if i % 2 else -0.001)  # small deterministic wiggle
        out.append(p)
    return out


@dataclass
class Liq:
    turnover: float
    trade_count: int


def liq(turnover: float = 5e8, trades: int = 5000, n: int = 20) -> list[Liq]:
    return [Liq(turnover, trades) for _ in range(n)]


def as_of_for(bars: list[Bar]) -> date:
    return bars[-1].timestamp.date()


# --- indicators ------------------------------------------------------------

def test_sma_and_none_on_short_history():
    assert sma([1, 2, 3, 4], 2) == 3.5
    assert sma([1], 2) is None


def test_pct_return():
    assert pct_return([100, 110], 1) == pytest.approx(0.10)
    assert pct_return([100], 1) is None


def test_atr_matches_hand_calculation():
    bars = make_bars([100, 102, 101], spread=0.0)
    # TRs: |102-100|=2 ; max(1, |101-102|, |101-102|)=1 -> mean 1.5
    assert atr(bars, 2) == 1.5


def test_volatility_is_zero_for_constant_growth():
    closes = [100 * 1.01 ** i for i in range(30)]
    assert daily_volatility(closes, 20) < 1e-9


def test_indicators_are_causal():
    closes = uptrend(80)
    full = sma(closes[:60], 20)
    # Appending future bars must not change a value computed on the prefix.
    assert sma(closes[:60] + closes[60:], 20) != full
    assert sma(closes[:60], 20) == full


# --- regime ----------------------------------------------------------------

def test_regime_uncertain_on_insufficient_history():
    r = detect_regime(make_bars(uptrend(30)))
    assert r.regime == Regime.UNCERTAIN
    assert not r.allows_new_long_entries


def test_regime_uncertain_on_empty():
    assert detect_regime([]).regime == Regime.UNCERTAIN


def test_regime_bullish_allows_setups():
    r = detect_regime(make_bars(uptrend(120)))
    assert r.regime == Regime.TRENDING_BULLISH
    assert r.eligible_setups == {"BREAKOUT", "PULLBACK"}


def test_regime_bearish_allows_nothing():
    closes = [200 * (1 - 0.002) ** i for i in range(120)]
    r = detect_regime(make_bars(closes))
    assert r.regime == Regime.TRENDING_BEARISH
    assert not r.eligible_setups


def test_regime_high_volatility_overrides_trend():
    closes = uptrend(100)
    for i in range(20):
        closes.append(closes[-1] * (1.04 if i % 2 else 0.96))
    r = detect_regime(make_bars(closes))
    assert r.regime == Regime.HIGH_VOLATILITY
    assert not r.eligible_setups


def test_regime_reports_missing_breadth():
    r = detect_regime(make_bars(uptrend(120)))
    assert any("breadth" in s.lower() for s in r.limitations)


def test_regime_rejects_bad_quality_latest_bar():
    bars = make_bars(uptrend(120))
    bars[-1] = Bar(**{**bars[-1].__dict__, "quality": DataQuality.INVALID})
    assert detect_regime(bars).regime == Regime.UNCERTAIN


# --- tradability -----------------------------------------------------------

def test_liquid_symbol_passes():
    bars = make_bars(uptrend(100))
    res = check_tradability("X", bars, liq(), as_of_for(bars))
    assert res.passed and res.turnover_source == "exchange"


def test_illiquid_symbol_rejected_before_scoring():
    bars = make_bars(uptrend(100))
    res = check_tradability("X", bars, liq(turnover=1e6), as_of_for(bars))
    assert not res.passed
    assert {r.rule for r in res.rejections} == {"ILLIQUID"}


def test_few_trades_rejected():
    bars = make_bars(uptrend(100))
    res = check_tradability("X", bars, liq(trades=10), as_of_for(bars))
    assert "FEW_TRADES" in {r.rule for r in res.rejections}


def test_missing_liquidity_uses_stricter_proxy_and_says_so():
    bars = make_bars(uptrend(100), volume=1_000_000)  # ~Rs 1.3e8 proxy
    res = check_tradability("X", bars, None, as_of_for(bars))
    assert res.turnover_source == "proxy"
    assert res.notes


def test_proxy_threshold_is_stricter_than_exchange():
    bars = make_bars([100.0] * 100, volume=600_000)  # proxy 6e7: passes 5e7, fails 7.5e7
    assert not check_tradability("X", bars, None, as_of_for(bars)).passed
    exchange = liq(turnover=6e7)
    assert check_tradability("X", bars, exchange, as_of_for(bars)).passed


def test_stale_data_rejected():
    bars = make_bars(uptrend(100))
    res = check_tradability("X", bars, liq(), as_of_for(bars) + timedelta(days=10))
    assert "STALE" in {r.rule for r in res.rejections}


def test_insufficient_history_rejected():
    bars = make_bars(uptrend(20))
    res = check_tradability("X", bars, liq(), as_of_for(bars))
    assert "INSUFFICIENT_HISTORY" in {r.rule for r in res.rejections}


def test_big_gap_rejected():
    closes = uptrend(100)
    bars = make_bars(closes)
    bars[-3] = Bar(**{**bars[-3].__dict__, "open": bars[-4].close * 1.3, "high": bars[-3].high * 1.4})
    res = check_tradability("X", bars, liq(), as_of_for(bars))
    assert "EXCESSIVE_GAP" in {r.rule for r in res.rejections}


def test_no_bars_rejected():
    assert not check_tradability("X", [], None, date(2024, 6, 1)).passed


# --- ranking ---------------------------------------------------------------

def breakout_stock(symbol: str, surge: int = 3_000_000) -> list[Bar]:
    closes = uptrend(110, step=0.0015)
    flat = closes[-1]
    closes += [flat * (1 + 0.0005 * i) for i in range(1, 10)]  # tight range
    closes.append(closes[-1] * 1.02)  # breakout bar
    volumes = [1_000_000] * (len(closes) - 1) + [surge]
    return make_bars(closes, symbol=symbol, volumes=volumes)


def test_no_trade_when_regime_blocks():
    bearish = make_bars([200 * 0.998 ** i for i in range(120)])
    stock = breakout_stock("AAA")
    res = rank_swing_candidates({"AAA": stock}, bearish, as_of_for(stock), {"AAA": liq()})
    assert res.no_trade and "TRENDING_BEARISH" in res.no_trade_reason
    assert res.candidates == ()


def test_no_trade_on_empty_universe():
    index = make_bars(uptrend(120))
    res = rank_swing_candidates({}, index, as_of_for(index))
    assert res.no_trade


def test_breakout_candidate_is_ranked_with_full_explanation():
    index = make_bars(uptrend(120))
    stock = breakout_stock("AAA")
    res = rank_swing_candidates({"AAA": stock}, index, as_of_for(stock), {"AAA": liq()})
    assert not res.no_trade, res.rejections
    c = res.candidates[0]
    assert c.symbol == "AAA" and c.rank == 1 and c.setup == "BREAKOUT" and c.bias == "LONG"
    assert 0 <= c.score <= 100
    assert c.stop_loss < c.entry_zone[0] < c.target
    assert c.reward_risk >= 1.5
    assert c.event_risk_status == "NOT_NEWS_VERIFIED"
    assert c.qualified_because and c.limitations and c.reasons_to_avoid
    assert any("probability" in s for s in c.limitations)
    assert c.data_source == "test"


def test_illiquid_high_momentum_stock_is_rejected_not_ranked():
    index = make_bars(uptrend(120))
    stock = breakout_stock("PUMP")
    res = rank_swing_candidates({"PUMP": stock}, index, as_of_for(stock), {"PUMP": liq(turnover=1e6)})
    assert res.no_trade
    assert any(r.symbol == "PUMP" and r.rule == "ILLIQUID" for r in res.rejections)


def test_breakout_needs_volume_confirmation():
    index = make_bars(uptrend(120))
    stock = breakout_stock("AAA", surge=1_000_000)
    res = rank_swing_candidates({"AAA": stock}, index, as_of_for(stock), {"AAA": liq()})
    assert res.no_trade
    assert any(r.rule == "NO_SETUP" for r in res.rejections)


def test_extended_breakout_is_not_chased():
    index = make_bars(uptrend(120))
    closes = uptrend(110, step=0.0015)
    closes += [closes[-1]] * 9
    closes.append(closes[-1] * 1.25)  # enormous move far beyond the level
    stock = make_bars(closes, symbol="AAA", volumes=[1_000_000] * (len(closes) - 1) + [5_000_000])
    res = rank_swing_candidates({"AAA": stock}, index, as_of_for(stock), {"AAA": liq()})
    assert res.no_trade


def test_ranking_is_order_stable_under_input_shuffle():
    index = make_bars(uptrend(120))
    a, b = breakout_stock("AAA"), breakout_stock("BBB")
    lq = {"AAA": liq(), "BBB": liq()}
    r1 = rank_swing_candidates({"AAA": a, "BBB": b}, index, as_of_for(a), lq)
    r2 = rank_swing_candidates({"BBB": b, "AAA": a}, index, as_of_for(a), lq)
    assert [c.symbol for c in r1.candidates] == [c.symbol for c in r2.candidates]
    assert [c.score for c in r1.candidates] == [c.score for c in r2.candidates]


def test_max_candidates_cutoff_is_recorded_as_rejection():
    index = make_bars(uptrend(120))
    names = ["AAA", "BBB", "CCC"]
    uni = {n: breakout_stock(n) for n in names}
    res = rank_swing_candidates(
        uni, index, as_of_for(uni["AAA"]), {n: liq() for n in names},
        config=RankingConfig(max_candidates=2),
    )
    assert len(res.candidates) == 2
    assert any(r.rule == "BELOW_CUTOFF" for r in res.rejections)


def test_future_bars_cannot_change_a_past_decision():
    """Decision at day N must be identical whether or not later bars exist."""
    index_full = make_bars(uptrend(140))
    stock = breakout_stock("AAA")
    as_of = as_of_for(stock)
    later = make_bars(uptrend(140), symbol="AAA")
    extended = stock + [
        Bar(**{**b.__dict__, "timestamp": stock[-1].timestamp + timedelta(days=i + 1), "close": b.close * 0.5})
        for i, b in enumerate(later[:5])
    ]
    lq = {"AAA": liq()}
    r1 = rank_swing_candidates({"AAA": stock}, index_full, as_of, lq)
    r2 = rank_swing_candidates({"AAA": extended}, index_full, as_of, lq)
    assert [(c.symbol, c.score, c.stop_loss) for c in r1.candidates] == \
           [(c.symbol, c.score, c.stop_loss) for c in r2.candidates]


def test_every_rejection_has_symbol_rule_and_reason():
    index = make_bars(uptrend(120))
    uni = {"SHORT": make_bars(uptrend(20), symbol="SHORT")}
    res = rank_swing_candidates(uni, index, as_of_for(index), {})
    assert res.rejections
    assert all(r.symbol and r.rule and r.detail for r in res.rejections)
