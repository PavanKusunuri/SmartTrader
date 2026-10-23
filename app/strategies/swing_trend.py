"""Swing trend strategy: regime gate -> trend filter -> breakout/pullback -> ATR risk.

EXPERIMENTAL. A research prototype, not a claim of profitability. It exists
because the older MA-crossover prototype has a fixed-percentage stop that
ignores how volatile a stock is, and no market-regime awareness.

Decision flow on each daily bar (all inputs are bars up to and including now):

1. Data gate: latest bar quality must be OK and enough history must exist.
2. Regime gate (when index bars are supplied): the index must be a clean
   bullish trend with normal volatility, otherwise NO_TRADE. Index bars are
   sliced to the decision date, so this cannot see the future.
3. Trend filter on the stock: close > slow MA, fast MA > slow MA, and the slow
   MA not falling. Long-only; a stock in a downtrend is never bought.
4. Setup: a volume-confirmed breakout above the prior N-bar high (rejected if
   already extended beyond max_extension_atr ATRs, i.e. no chasing) or a
   pullback to the fast MA in an uptrend. Reuses app.selection.ranking so the
   scanner and the backtest cannot drift apart.
5. Risk: stop = close - stop_atr_multiple * ATR, target = target_rr * risk
   above. The RiskManager sizes the position from the stop distance, so
   volatile stocks automatically get fewer shares.

Exit (position open): close below the exit MA (trend failure) or the time
stop. The signal fires on a bar's close and the engine fills it at the NEXT
bar's open. The stop/target are checked intrabar by the engine; a bar that
touches both is booked as the stop (conservative).

Known limits: gap risk can fill a stop worse than its level; prices are
unadjusted for corporate actions; no news/event check; with no index bars the
regime gate is skipped and that is stated in every signal's inputs.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from app.config import SwingTrendConfig
from app.models import Bar, DataQuality, Position, Signal, SignalAction
from app.selection.indicators import atr, sma
from app.selection.ranking import RankingConfig, _detect_setup
from app.selection.regime import RegimeConfig, detect_regime
from app.strategies.base import Strategy


class SwingTrendStrategy(Strategy):
    name = "swing_trend_atr"

    def __init__(
        self,
        config: SwingTrendConfig | None = None,
        index_bars: list[Bar] | None = None,
        regime_config: RegimeConfig | None = None,
    ):
        self.config = config or SwingTrendConfig()
        # Sorted once; sliced to the decision date on every call.
        self._index_bars = sorted(index_bars, key=lambda b: b.timestamp) if index_bars else []
        self._regime_config = regime_config or RegimeConfig()
        c = self.config
        self._ranking = RankingConfig(
            breakout_lookback=c.breakout_lookback,
            breakout_volume_multiple=c.breakout_volume_multiple,
            pullback_ma=c.trend_fast_ma,
            pullback_atr_band=c.pullback_atr_band,
            atr_period=c.atr_period,
            max_extension_atr=c.max_extension_atr,
        )

    @property
    def required_history(self) -> int:
        c = self.config
        return max(c.trend_slow_ma + 6, c.breakout_lookback + 1, c.atr_period + 1, c.exit_ma)

    def _signal_id(self, bar: Bar) -> str:
        return f"{self.name}:{bar.symbol}:{bar.timestamp.isoformat()}"

    def _index_visible(self, as_of: date) -> list[Bar]:
        return [b for b in self._index_bars if b.timestamp.date() <= as_of]

    def evaluate(self, bars: list[Bar], position: Optional[Position] = None) -> Signal:
        if not bars:
            raise ValueError("evaluate() requires at least one bar")
        now = bars[-1]
        cfg = self.config

        def make(action: SignalAction, reason: str, inputs: dict, **levels) -> Signal:
            return Signal(
                signal_id=self._signal_id(now), symbol=now.symbol, timestamp=now.timestamp,
                strategy_name=self.name, action=action, reason=reason, inputs=inputs,
                charge_profile=self.charge_profile, **levels,
            )

        def no_trade(reason: str, inputs: dict | None = None) -> Signal:
            return make(SignalAction.NO_TRADE, reason, inputs or {})

        if now.quality != DataQuality.OK:
            return no_trade(
                f"latest bar data quality is {now.quality.value}, not OK",
                {"quality_notes": now.quality_notes},
            )
        if len(bars) < self.required_history:
            return no_trade(
                f"insufficient history: have {len(bars)} bars, need {self.required_history}",
                {"bars_available": len(bars)},
            )

        closes = [b.close for b in bars]
        fast = sma(closes, cfg.trend_fast_ma)
        slow = sma(closes, cfg.trend_slow_ma)
        slow_prev = sma(closes[:-5], cfg.trend_slow_ma)  # 5 bars ago: slope check
        exit_ma = sma(closes, cfg.exit_ma)
        atr_v = atr(bars, cfg.atr_period)
        if None in (fast, slow, slow_prev, exit_ma) or not atr_v or atr_v <= 0:
            return no_trade("an indicator could not be computed from the available bars")

        inputs = {
            "close": now.close, "fast_ma": fast, "slow_ma": slow, "exit_ma": exit_ma,
            "atr": atr_v, "atr_pct": atr_v / now.close,
        }

        # ---------------- open position: exits only ----------------
        if position is not None:
            days_held = (
                (now.timestamp.date() - position.opened_at.date()).days
                if position.opened_at else None
            )
            inputs["days_held"] = days_held
            if now.close < exit_ma:
                return make(
                    SignalAction.SELL,
                    f"trend failure: close {now.close:.2f} below MA{cfg.exit_ma} {exit_ma:.2f}",
                    inputs,
                )
            if days_held is not None and days_held >= cfg.max_holding_days:
                return make(
                    SignalAction.SELL,
                    f"time stop: held {days_held} >= {cfg.max_holding_days} days",
                    inputs,
                )
            return no_trade("holding open position, no exit condition met", inputs)

        # ---------------- flat: look for an entry ----------------
        if cfg.use_regime_filter:
            if self._index_bars:
                regime = detect_regime(self._index_visible(now.timestamp.date()), self._regime_config)
                inputs["regime"] = regime.regime.value
                if not regime.allows_new_long_entries:
                    return no_trade(
                        f"regime {regime.regime.value}: {'; '.join(regime.reasons)}", inputs
                    )
                allowed = regime.eligible_setups
            else:
                inputs["regime"] = "NOT_CHECKED (no index bars supplied)"
                allowed = frozenset({"BREAKOUT", "PULLBACK"})
        else:
            inputs["regime"] = "FILTER_DISABLED"
            allowed = frozenset({"BREAKOUT", "PULLBACK"})

        if not (now.close > slow and fast > slow and slow >= slow_prev):
            return no_trade(
                f"no uptrend: need close > MA{cfg.trend_slow_ma}, MA{cfg.trend_fast_ma} > "
                f"MA{cfg.trend_slow_ma} and a non-falling slow MA", inputs,
            )

        found = _detect_setup(bars, self._ranking, allowed, atr_v)
        if found is None:
            return no_trade("uptrend, but no breakout or pullback setup on this bar", inputs)
        setup, reasons, _detractors, _avoid = found

        stop = round(now.close - cfg.stop_atr_multiple * atr_v, 2)
        risk = now.close - stop
        if stop <= 0 or risk <= 0:
            return no_trade("ATR stop is not a positive price below the close", inputs)
        target = round(now.close + cfg.target_rr * risk, 2)
        inputs.update({"setup": setup, "stop_distance": risk})

        return make(
            SignalAction.BUY,
            f"{setup}: " + "; ".join(reasons)
            + f"; stop {stop:.2f} ({cfg.stop_atr_multiple} ATR), target {target:.2f}",
            inputs, stop_loss=stop, target=target,
        )
