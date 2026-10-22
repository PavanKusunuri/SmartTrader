"""Intraday prototype: MA trend filter + volume-confirmed momentum.

EXPERIMENTAL DEFAULTS — a research prototype, not a claim that this is
profitable. Intraday results are especially sensitive to slippage and costs,
which this project's cost rates do NOT currently verify. See CLAUDE.md
section 7A and docs/RISK_AND_LIMITATIONS.md.

Entry (all conditions required):
  1. Trend filter: fast MA > slow MA.
  2. Momentum: current bar closed above the previous bar's close.
  3. Volume confirmation: current volume > volume_surge_multiple x average volume.
  4. Session: the timestamp must be inside the configured entry window.

Exit: trend filter breaks (fast MA <= slow MA). Stop-loss, target, and the
forced end-of-session flatten are enforced by the backtest engine / broker,
not here — the engine owns fill simulation.

No overnight positions: the engine force-flattens at the session's flatten
time when a MarketSession is supplied.
"""

from __future__ import annotations

from typing import Optional

from app.config import IntradayStrategyConfig
from app.market_calendar import MarketSession
from app.models import Bar, DataQuality, Position, Signal, SignalAction
from app.strategies.base import Strategy


def _sma(values: list[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


class IntradayMomentumStrategy(Strategy):
    name = "intraday_ma_momentum"

    def __init__(
        self,
        config: IntradayStrategyConfig | None = None,
        session: MarketSession | None = None,
    ):
        self.config = config or IntradayStrategyConfig()
        self.session = session

    @property
    def required_history(self) -> int:
        return max(self.config.slow_ma_period, self.config.volume_ma_period) + 1

    def _signal_id(self, bar: Bar) -> str:
        return f"{self.name}:{bar.symbol}:{bar.timestamp.isoformat()}"

    def evaluate(self, bars: list[Bar], position: Optional[Position] = None) -> Signal:
        if not bars:
            raise ValueError("evaluate() requires at least one bar")

        now_bar = bars[-1]
        cfg = self.config
        min_bars = max(cfg.slow_ma_period, cfg.volume_ma_period) + 1

        def no_trade(reason: str, inputs: dict) -> Signal:
            return Signal(
                signal_id=self._signal_id(now_bar),
                symbol=now_bar.symbol,
                timestamp=now_bar.timestamp,
                strategy_name=self.name,
                action=SignalAction.NO_TRADE,
                reason=reason,
                inputs=inputs,
            )

        if now_bar.quality != DataQuality.OK:
            return no_trade(
                f"latest bar data quality is {now_bar.quality.value}, not OK",
                {"quality_notes": now_bar.quality_notes},
            )

        if self.session is not None and not self.session.is_in_session(now_bar.timestamp):
            return no_trade(
                "timestamp is outside the configured trading session",
                {"timestamp": now_bar.timestamp.isoformat()},
            )

        if len(bars) < min_bars:
            return no_trade(
                f"insufficient history: have {len(bars)} bars, need {min_bars}",
                {"bars_available": len(bars)},
            )

        closes = [b.close for b in bars]
        volumes = [float(b.volume) for b in bars]

        fast_ma = _sma(closes, cfg.fast_ma_period)
        slow_ma = _sma(closes, cfg.slow_ma_period)
        avg_volume = _sma(volumes, cfg.volume_ma_period)
        assert fast_ma is not None and slow_ma is not None and avg_volume is not None

        prev_close = closes[-2]
        trend_up = fast_ma > slow_ma
        momentum_up = now_bar.close > prev_close
        volume_confirms = (
            avg_volume > 0 and now_bar.volume > cfg.volume_surge_multiple * avg_volume
        )

        inputs = {
            "fast_ma": fast_ma,
            "slow_ma": slow_ma,
            "avg_volume": avg_volume,
            "volume": now_bar.volume,
            "close": now_bar.close,
            "prev_close": prev_close,
            "trend_up": trend_up,
            "momentum_up": momentum_up,
            "volume_confirms": volume_confirms,
        }

        if position is not None:
            if not trend_up:
                return Signal(
                    signal_id=self._signal_id(now_bar),
                    symbol=now_bar.symbol,
                    timestamp=now_bar.timestamp,
                    strategy_name=self.name,
                    action=SignalAction.SELL,
                    reason=(
                        f"trend filter broke: fast MA({cfg.fast_ma_period})={fast_ma:.2f} "
                        f"<= slow MA({cfg.slow_ma_period})={slow_ma:.2f}"
                    ),
                    inputs=inputs,
                )
            return no_trade("holding open position, trend still intact", inputs)

        if self.session is not None and not self.session.can_enter(now_bar.timestamp):
            return no_trade(
                f"entry window closed (cutoff {self.session.entry_cutoff})", inputs
            )

        if not trend_up:
            return no_trade("trend filter not satisfied (fast MA <= slow MA)", inputs)
        if not momentum_up:
            return no_trade("no upward momentum (close <= previous close)", inputs)
        if not volume_confirms:
            return no_trade(
                f"volume {now_bar.volume:.0f} did not exceed "
                f"{cfg.volume_surge_multiple}x average {avg_volume:.0f}",
                inputs,
            )

        return Signal(
            signal_id=self._signal_id(now_bar),
            symbol=now_bar.symbol,
            timestamp=now_bar.timestamp,
            strategy_name=self.name,
            action=SignalAction.BUY,
            reason=(
                f"trend up (fast {fast_ma:.2f} > slow {slow_ma:.2f}), "
                f"momentum up ({prev_close:.2f} -> {now_bar.close:.2f}), "
                f"volume {now_bar.volume:.0f} > {cfg.volume_surge_multiple}x avg {avg_volume:.0f}"
            ),
            inputs=inputs,
            stop_loss=round(now_bar.close * (1 - cfg.stop_loss_pct), 2),
            target=round(now_bar.close * (1 + cfg.target_pct), 2),
        )
