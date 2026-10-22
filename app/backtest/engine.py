"""Event-driven, causally-correct backtest loop.

Design choices made explicit here (CLAUDE.md section 8 requires this):

- Decisions for a given calendar day use ONLY bars dated on or before that
  day. A symbol's strategy never sees a later bar.
- Fills are simulated at the SAME bar's close that produced the signal.
  That means the signal and its fill happen at the same instant in this
  simple model — there is no next-bar-open fill yet. This is a real
  limitation (a live system cannot act on a close the instant it prints);
  it is documented, not hidden. See docs/RISK_AND_LIMITATIONS.md.
- Stop-loss/target levels set at entry are NOT checked against this
  milestone's engine (that would require intrabar path assumptions this
  simple daily-bar engine does not model yet); exits happen only via the
  strategy's own SELL signal (trend reversal or max holding period). This
  is a deliberate Milestone-1 simplification, not an oversight.
- One shared Portfolio/PaperBroker across all symbols, so risk limits
  (max exposure, max daily loss, max open positions) are enforced across
  the whole watchlist, not per symbol.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import AppConfig
from app.data.base import DataProvider
from app.backtest.metrics import Metrics, compute_metrics
from app.models import Signal, SignalAction, Trade
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
    metrics: Metrics
    reconciliation_problems: list[str] = field(default_factory=list)


def compute_buy_and_hold_benchmark(symbol_bars: dict[str, list]) -> float | None:
    """Equal-cash-split buy-and-hold return over the same symbols/period,
    ignoring costs (documented simplification). Returns None if any symbol
    has no bars."""

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


def run_backtest(
    provider: DataProvider,
    symbols: list[str],
    strategy: Strategy,
    config: AppConfig,
    start: str | None = None,
    end: str | None = None,
) -> BacktestResult:
    symbol_bars = {s: provider.get_bars(s, start=start, end=end) for s in symbols}
    source_summary = {
        s: (bars[0].source if bars else "no data") for s, bars in symbol_bars.items()
    }

    all_dates = sorted({b.timestamp.date() for bars in symbol_bars.values() for b in bars})

    portfolio = Portfolio(config.starting_cash)
    broker = PaperBroker(config, portfolio)
    signals_log: list[Signal] = []
    equity_curve: list[float] = []

    for day in all_dates:
        bars_up_to_day = {
            s: [b for b in symbol_bars[s] if b.timestamp.date() <= day] for s in symbols
        }
        current_prices = {s: bars[-1].close for s, bars in bars_up_to_day.items() if bars}
        portfolio.mark_new_day(day, current_prices)

        for symbol in symbols:
            bars_so_far = bars_up_to_day[symbol]
            if not bars_so_far or bars_so_far[-1].timestamp.date() != day:
                continue  # this symbol has no bar today (holiday/gap/different calendar)

            position = portfolio.positions.get(symbol)
            signal = strategy.evaluate(bars_so_far, position=position)
            signals_log.append(signal)

            if signal.action != SignalAction.NO_TRADE:
                fill_price_basis = bars_so_far[-1].close
                broker.process_signal(signal, fill_price_basis, current_prices)
                current_prices[symbol] = fill_price_basis

        equity_curve.append(portfolio.equity(current_prices))

    final_prices = {s: bars[-1].close for s, bars in symbol_bars.items() if bars}
    final_equity = portfolio.equity(final_prices)
    benchmark_return = compute_buy_and_hold_benchmark(symbol_bars)

    metrics = compute_metrics(
        trades=portfolio.trades,
        starting_cash=config.starting_cash,
        final_equity=final_equity,
        equity_curve=equity_curve,
        benchmark_return_pct=benchmark_return,
    )

    reconciliation_problems = portfolio.reconcile(final_prices)

    return BacktestResult(
        symbols=symbols,
        interval="1d",
        source_summary=source_summary,
        trades=portfolio.trades,
        signals=signals_log,
        metrics=metrics,
        reconciliation_problems=reconciliation_problems,
    )
