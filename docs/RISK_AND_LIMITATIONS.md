# Risk and limitations

Read this before interpreting any number this application prints.

## This is a simulation, not trading

- **PAPER TRADING — NO REAL ORDERS.** There is no broker integration, no
  order-placement endpoint, and no credential handling anywhere in the code.
- Simulated fills do not guarantee real-world fills or prices. In a real
  market your order may not fill at all, may fill partially, or may fill at a
  materially worse price.
- Paper-trading results do not predict live results.

## Known limitations of the Milestone 1 backtest

These are real weaknesses, deliberately documented rather than hidden:

1. **Signal and fill happen at the same bar's close.** The engine simulates a
   fill at the close of the same daily bar that generated the signal. A live
   trader cannot act on a closing price at the instant it prints. This makes
   backtest results optimistic relative to reality. A next-bar-open fill model
   is the correct fix and is not implemented yet.
2. **Stop-loss and target levels are recorded but not enforced by the engine.**
   The strategy sets them at entry and they are stored on the position, but
   this milestone's engine exits only on the strategy's own SELL signal (trend
   reversal or max holding period). Intrabar stop/target simulation requires
   assumptions about the order of high/low touches within a bar — CLAUDE.md
   requires treating that collision conservatively or as ambiguous, and that
   logic is intentionally deferred rather than guessed at.
3. **No market-holiday calendar.** The engine iterates over whatever dates
   exist in the data. It does not verify those dates were actual trading
   sessions. The synthetic sample data merely skips weekends.
4. **Cost rates are unverified assumptions.** The brokerage, STT, exchange
   transaction charge, GST, stamp duty, and slippage defaults in
   `app/config.py` were **not** verified against current official NSE/SEBI
   sources. Treat them as placeholders you must configure yourself. Net
   results are only as meaningful as those inputs.
5. **Daily-loss limit uses day-start equity.** The max-daily-loss check is
   measured against equity at the start of the trading day, computed from the
   last available close. It is not a tick-by-tick intraday drawdown guard.
6. **Exposure check uses entry prices, not live marks.** Existing exposure is
   valued at average entry price when checking the max-exposure limit, which
   understates exposure for a position that has moved up.
7. **No survivorship-bias handling.** The universe is whatever CSVs you
   supply. If you supply only symbols that still exist and did well, the
   results are biased and this tool cannot detect that for you.
8. **No out-of-sample or walk-forward evaluation yet.** CLAUDE.md requires
   this before any performance conclusion. It is a Milestone 2 item. **Do not
   draw conclusions about the strategy from the current numbers.**
9. **Single-position-per-symbol model.** The portfolio holds at most one
   position per symbol and does not support scaling in/out or partial fills.

## About the bundled sample data

`data/samples/SAMPLE_*.csv` is a deterministic synthetic random walk, not real
market data. Any return, win rate, or drawdown computed on it describes the
random walk, not any real stock and not any real strategy edge.

## No performance claims

The swing strategy's default parameters are experimental starting points. They
have not been validated out-of-sample, have not been compared fairly against a
benchmark on real data, and are not represented as profitable. A moving-average
crossover is a well-known, widely-published rule; there is no claim of edge here.
