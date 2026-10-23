"""Swing prototype: moving-average trend-filter strategy.

EXPERIMENTAL DEFAULTS — this is a research prototype, not a claim that MA
crossovers are profitable. See CLAUDE.md section 7B and docs/STRATEGIES.md.

Entry: fast MA crosses above slow MA (classic trend filter), only when
enough history exists and the latest bar's data quality is OK.
Exit: fast MA crosses below slow MA, or the position has been held for
max_holding_days. Stop-loss/target level breaches are NOT decided here —
the backtest engine / paper broker checks bar high/low against the levels
this strategy sets at entry, because that's a fill-simulation concern, not
a strategy-signal concern (see app/backtest/engine.py).
"""

from __future__ import annotations

from typing import Optional

from app.config import SwingStrategyConfig
from app.models import Bar, DataQuality, Position, Signal, SignalAction
from app.selection.volume import relative_volume
from app.strategies.base import Strategy


def _sma(values: list[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


class SwingMAStrategy(Strategy):
    name = "swing_ma_crossover"

    def __init__(self, config: SwingStrategyConfig | None = None):
        self.config = config or SwingStrategyConfig()

    @property
    def required_history(self) -> int:
        return self.config.slow_ma_period + 1

    def _signal_id(self, bar: Bar) -> str:
        return f"{self.name}:{bar.symbol}:{bar.timestamp.isoformat()}"

    def evaluate(self, bars: list[Bar], position: Optional[Position] = None) -> Signal:
        if not bars:
            raise ValueError("evaluate() requires at least one bar")

        now_bar = bars[-1]
        cfg = self.config
        min_bars_needed = cfg.slow_ma_period + 1  # +1 so we can see the prior crossover state

        def no_trade(reason: str, inputs: dict) -> Signal:
            return Signal(
                signal_id=self._signal_id(now_bar),
                symbol=now_bar.symbol,
                timestamp=now_bar.timestamp,
                strategy_name=self.name,
                action=SignalAction.NO_TRADE,
                reason=reason,
                inputs=inputs,
                charge_profile=self.charge_profile,
            )

        if now_bar.quality != DataQuality.OK:
            return no_trade(
                f"latest bar data quality is {now_bar.quality.value}, not OK",
                {"quality_notes": now_bar.quality_notes},
            )

        if len(bars) < min_bars_needed:
            return no_trade(
                f"insufficient history: have {len(bars)} bars, need {min_bars_needed}",
                {"bars_available": len(bars)},
            )

        closes = [b.close for b in bars]
        fast_curr = _sma(closes, cfg.fast_ma_period)
        slow_curr = _sma(closes, cfg.slow_ma_period)
        fast_prev = _sma(closes[:-1], cfg.fast_ma_period)
        slow_prev = _sma(closes[:-1], cfg.slow_ma_period)

        assert fast_curr is not None and slow_curr is not None
        assert fast_prev is not None and slow_prev is not None

        inputs = {
            "fast_ma": fast_curr,
            "slow_ma": slow_curr,
            "fast_ma_prev": fast_prev,
            "slow_ma_prev": slow_prev,
            "close": now_bar.close,
        }

        crossed_up = fast_prev <= slow_prev and fast_curr > slow_curr
        crossed_down = fast_prev >= slow_prev and fast_curr < slow_curr

        if position is None:
            if crossed_up and cfg.volume_filter_enabled:
                rv = relative_volume(bars, cfg.volume_lookback)
                inputs["relative_volume"] = rv
                if rv is None or rv < cfg.min_relative_volume:
                    return no_trade(
                        f"bullish crossover rejected by volume filter: relative volume "
                        f"{'n/a' if rv is None else f'{rv:.2f}'} < {cfg.min_relative_volume}",
                        inputs,
                    )
            if crossed_up:
                return Signal(
                    signal_id=self._signal_id(now_bar),
                    symbol=now_bar.symbol,
                    timestamp=now_bar.timestamp,
                    strategy_name=self.name,
                    action=SignalAction.BUY,
                    reason=(
                        f"fast MA({cfg.fast_ma_period})={fast_curr:.2f} crossed above "
                        f"slow MA({cfg.slow_ma_period})={slow_curr:.2f}"
                    ),
                    inputs=inputs,
                    charge_profile=self.charge_profile,
                    stop_loss=round(now_bar.close * (1 - cfg.stop_loss_pct), 2),
                    target=round(now_bar.close * (1 + cfg.target_pct), 2),
                )
            return no_trade("no bullish MA crossover", inputs)

        # Have an open position: look for an exit signal only.
        days_held = (now_bar.timestamp.date() - position.opened_at.date()).days if position.opened_at else None
        inputs["days_held"] = days_held

        if crossed_down:
            return Signal(
                signal_id=self._signal_id(now_bar),
                symbol=now_bar.symbol,
                timestamp=now_bar.timestamp,
                strategy_name=self.name,
                action=SignalAction.SELL,
                reason=(
                    f"fast MA({cfg.fast_ma_period})={fast_curr:.2f} crossed below "
                    f"slow MA({cfg.slow_ma_period})={slow_curr:.2f}"
                ),
                inputs=inputs,
                charge_profile=self.charge_profile,
            )

        if days_held is not None and days_held >= cfg.max_holding_days:
            return Signal(
                signal_id=self._signal_id(now_bar),
                symbol=now_bar.symbol,
                timestamp=now_bar.timestamp,
                strategy_name=self.name,
                action=SignalAction.SELL,
                reason=f"max holding period reached ({days_held} >= {cfg.max_holding_days} days)",
                inputs=inputs,
                charge_profile=self.charge_profile,
            )

        return no_trade("holding open position, no exit condition met", inputs)
