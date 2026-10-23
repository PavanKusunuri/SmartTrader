"""Event-driven, causally-correct backtest loop.

Fill and exit model, stated explicitly as CLAUDE.md section 8 requires:

1. **Next-bar-open fills.** A signal generated on bar N is queued and filled
   at bar N+1's OPEN, plus adverse slippage. A decision is never filled at
   the close that produced it, because that price is not available to act on
   after the signal exists. A signal on the final bar never fills (there is
   no next bar) and is recorded as an expired order.
2. **Risk re-validation at fill time.** Queued orders go through the broker's
   risk checks when they fill, not when they were created, so a stale queued
   order cannot bypass exposure/daily-loss/kill-switch limits.
3. **Stop-loss and target are checked intrabar** against each bar's low/high.
   If only one is touched, that one fills at its level. If BOTH are touched
   in the same bar, OHLC data cannot tell us which came first: the engine
   assumes the STOP filled first (the conservative, worse outcome) and marks
   the trade `ambiguous_exit=True` so those trades can be scrutinised or
   excluded. It never assumes the favourable ordering.
4. **Session rules (intraday only).** When a MarketSession is supplied, no
   new entry is queued at/after the entry cutoff, and any open position is
   force-flattened at/after the flatten time — implementing "no overnight
   positions by default". With no session supplied (daily swing bars),
   positions are held across days as the strategy dictates.
5. One shared Portfolio/PaperBroker across all symbols, so risk limits apply
   across the whole watchlist rather than per symbol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from app.backtest.metrics import Metrics, compute_metrics
from app.config import AppConfig
from app.data.base import DataProvider
from app.market_calendar import MarketSession
from app.models import (
    Bar,
    DataQuality,
    Order,
    OrderStatus,
    Position,
    Signal,
    SignalAction,
    Trade,
)
from app.paper.broker import PaperBroker
from app.paper.portfolio import Portfolio
from app.strategies.base import Strategy


@dataclass
class BacktestResult:
    symbols: list[str]
    interval: str
    source_summary: dict[str, str]
    trades: list[Trade]
    signals: list[Signal]
    orders: list[Order]
    metrics: Metrics
    start_date: date | None = None
    end_date: date | None = None
    reconciliation_problems: list[str] = field(default_factory=list)
    expired_signal_count: int = 0
    # (timestamp, equity, cash, open_position_count) per evaluated bar event.
    # Recorded so the dashboard can draw equity/drawdown from stored values
    # rather than recomputing them from trades.
    equity_points: list[tuple[datetime, float, float, int]] = field(default_factory=list)
    # Positions still open when the run ended (closed ones are in `trades`).
    open_positions: list[Position] = field(default_factory=list)
    # Last close per symbol that the provider actually supplied, used to mark
    # open positions. Never a fabricated price.
    final_prices: dict[str, float] = field(default_factory=dict)

    @property
    def ambiguous_trade_count(self) -> int:
        return sum(1 for t in self.trades if t.ambiguous_exit)


def compute_buy_and_hold_benchmark(symbol_bars: dict[str, list[Bar]]) -> float | None:
    """Equal-cash-split buy-and-hold return over the same symbols/period,
    ignoring costs (documented simplification). None if any symbol lacks data."""

    per_symbol_returns = []
    for bars in symbol_bars.values():
        if not bars:
            return None
        first_close, last_close = bars[0].close, bars[-1].close
        if first_close <= 0:
            return None
        per_symbol_returns.append((last_close - first_close) / first_close)

    if not per_symbol_returns:
        return None
    return sum(per_symbol_returns) / len(per_symbol_returns)


def _synthetic_exit_signal(
    bar: Bar, reason: str, kind: str, inputs: dict | None = None
) -> Signal:
    """Builds a SELL signal for an engine-initiated exit (stop/target/EOD),
    as opposed to one the strategy asked for."""

    return Signal(
        signal_id=f"{kind}:{bar.symbol}:{bar.timestamp.isoformat()}",
        symbol=bar.symbol,
        timestamp=bar.timestamp,
        strategy_name=f"engine:{kind}",
        action=SignalAction.SELL,
        reason=reason,
        inputs=inputs or {},
    )


def run_backtest(
    provider: DataProvider,
    symbols: list[str],
    strategy: Strategy,
    config: AppConfig,
    start: str | None = None,
    end: str | None = None,
    interval: str = "1d",
    session: MarketSession | None = None,
) -> BacktestResult:
    symbol_bars = {
        s: provider.get_bars(s, interval=interval, start=start, end=end) for s in symbols
    }
    source_summary = {
        s: (bars[0].source if bars else "no data") for s, bars in symbol_bars.items()
    }

    # One merged, time-sorted event stream. Symbol breaks ties so the order is
    # deterministic across runs.
    events: list[tuple[datetime, str, int]] = sorted(
        (
            (bar.timestamp, symbol, idx)
            for symbol, bars in symbol_bars.items()
            for idx, bar in enumerate(bars)
        ),
        key=lambda e: (e[0], e[1]),
    )

    portfolio = Portfolio(config.starting_cash)
    broker = PaperBroker(config, portfolio)

    history: dict[str, list[Bar]] = {s: [] for s in symbols}
    pending: dict[str, Signal] = {}  # symbol -> signal awaiting next-bar fill
    signals_log: list[Signal] = []
    equity_curve: list[float] = []
    equity_points: list[tuple[datetime, float, float, int]] = []
    current_prices: dict[str, float] = {}
    current_day: date | None = None
    expired_signals = 0

    def synthetic_exit(bar, reason: str, kind: str, inputs: dict | None = None) -> Signal:
        """Builds an engine-initiated exit signal AND records it in the signal
        log, so every simulated order has a matching, explained signal."""
        sig = _synthetic_exit_signal(bar, reason, kind, inputs)
        signals_log.append(sig)
        return sig

    for ts, symbol, idx in events:
        bar = symbol_bars[symbol][idx]

        if current_day != ts.date():
            current_day = ts.date()
            portfolio.mark_new_day(current_day, current_prices)

        history[symbol].append(bar)

        # --- 1. Fill any order queued on the previous bar, at THIS bar's open.
        queued = pending.pop(symbol, None)
        if queued is not None:
            if bar.quality == DataQuality.INVALID:
                # Never fill against a bar we know is broken.
                broker.record_expired_order(
                    queued, "fill bar has INVALID data quality; order dropped"
                )
                expired_signals += 1
            else:
                broker.process_signal(
                    queued,
                    fill_price_basis=bar.open,
                    current_prices={**current_prices, symbol: bar.open},
                    fill_timestamp=bar.timestamp,
                )

        # --- 2. Intrabar stop-loss / target check on any open position.
        # A stop is filled at min(stop, open): a bar that gaps below the stop
        # fills at the open, not at the unreachable stop level.
        position = portfolio.positions.get(symbol)
        if position is not None and bar.quality != DataQuality.INVALID:
            stop = position.stop_loss
            target = position.target
            stop_touched = stop is not None and bar.low <= stop
            target_touched = target is not None and bar.high >= target

            if stop_touched and target_touched:
                exit_signal = synthetic_exit(
                    bar,
                    f"stop {stop} and target {target} both touched in one bar; "
                    "assuming stop filled first (conservative, true order unknown)",
                    kind="stop_target_ambiguous",
                    inputs={"bar_low": bar.low, "bar_high": bar.high, "stop": stop, "target": target},
                )
                broker.process_signal(
                    exit_signal, fill_price_basis=min(stop, bar.open), current_prices=current_prices,
                    fill_timestamp=bar.timestamp, ambiguous_exit=True,
                )
            elif stop_touched:
                exit_signal = synthetic_exit(
                    bar, f"stop-loss {stop} touched (bar low {bar.low})", kind="stop_loss",
                    inputs={"bar_low": bar.low, "stop": stop},
                )
                broker.process_signal(
                    exit_signal, fill_price_basis=min(stop, bar.open), current_prices=current_prices,
                    fill_timestamp=bar.timestamp,
                )
            elif target_touched:
                exit_signal = synthetic_exit(
                    bar, f"target {target} touched (bar high {bar.high})", kind="target",
                    inputs={"bar_high": bar.high, "target": target},
                )
                broker.process_signal(
                    exit_signal, fill_price_basis=target, current_prices=current_prices,
                    fill_timestamp=bar.timestamp,
                )

        # --- 3. Forced end-of-session flatten (intraday only).
        is_last_bar_for_symbol = idx == len(symbol_bars[symbol]) - 1
        if session is not None and symbol in portfolio.positions:
            next_bar = None if is_last_bar_for_symbol else symbol_bars[symbol][idx + 1]
            session_ends_here = (
                session.must_flatten(bar.timestamp)
                or next_bar is None
                or next_bar.timestamp.date() != bar.timestamp.date()
            )
            if session_ends_here:
                exit_signal = synthetic_exit(
                    bar,
                    "forced end-of-session exit: no overnight positions "
                    f"(flatten time {session.flatten_time})",
                    kind="eod_flatten",
                )
                broker.process_signal(
                    exit_signal, fill_price_basis=bar.close, current_prices=current_prices,
                    fill_timestamp=bar.timestamp,
                )
                pending.pop(symbol, None)  # cancel anything queued for tomorrow

        current_prices[symbol] = bar.close

        # --- 4. Evaluate the strategy and queue (not fill) any new order.
        signal = strategy.evaluate(history[symbol], position=portfolio.positions.get(symbol))
        signals_log.append(signal)

        if signal.action != SignalAction.NO_TRADE:
            blocked_by_session = (
                session is not None
                and signal.action == SignalAction.BUY
                and not session.can_enter(bar.timestamp)
            )
            if blocked_by_session:
                pass  # entry window closed; the engine simply does not queue it
            elif is_last_bar_for_symbol:
                broker.record_expired_order(
                    signal, "no subsequent bar available to fill against"
                )
                expired_signals += 1
            else:
                pending[symbol] = signal

        equity_now = portfolio.equity(current_prices)
        equity_curve.append(equity_now)
        equity_points.append(
            (bar.timestamp, equity_now, portfolio.cash, len(portfolio.positions))
        )

    final_prices = {s: bars[-1].close for s, bars in symbol_bars.items() if bars}
    final_equity = portfolio.equity(final_prices)

    all_dates = [ts.date() for ts, _, _ in events]

    metrics = compute_metrics(
        trades=portfolio.trades,
        starting_cash=config.starting_cash,
        final_equity=final_equity,
        equity_curve=equity_curve,
        benchmark_return_pct=compute_buy_and_hold_benchmark(symbol_bars),
    )

    return BacktestResult(
        symbols=symbols,
        interval=interval,
        source_summary=source_summary,
        trades=portfolio.trades,
        signals=signals_log,
        orders=broker.orders,
        metrics=metrics,
        start_date=min(all_dates) if all_dates else None,
        end_date=max(all_dates) if all_dates else None,
        reconciliation_problems=portfolio.reconcile(final_prices),
        expired_signal_count=expired_signals,
        equity_points=equity_points,
        open_positions=list(portfolio.positions.values()),
        final_prices=final_prices,
    )
