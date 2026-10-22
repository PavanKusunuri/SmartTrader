# Risk and limitations

Read this before interpreting any number this application prints.

## This is a simulation, not trading

- **PAPER TRADING — NO REAL ORDERS.** There is no broker integration, no
  order-placement endpoint, and no credential handling anywhere in the code.
- Simulated fills do not guarantee real-world fills or prices. In a real
  market your order may not fill at all, may fill partially, or may fill at a
  materially worse price.
- Paper-trading results do not predict live results.

## Fixed in Milestone 2

These were Milestone 1 limitations and are now addressed:

- ~~Signal and fill at the same bar's close~~ → signals now fill at the
  **next bar's open** plus adverse slippage. A signal on the final bar never
  fills and is recorded as an expired order.
- ~~Stop/target recorded but not enforced~~ → both are now checked intrabar
  against each bar's low/high. When **both** are touched in one bar the
  engine assumes the **stop** filled first (the worse outcome) and marks the
  trade `ambiguous_exit=True`. It never assumes the favourable ordering.
- ~~No session handling~~ → intraday runs take a configurable `MarketSession`
  with an entry cutoff and a forced end-of-day flatten (no overnight
  positions).

## Known limitations that REMAIN

These are real weaknesses, deliberately documented rather than hidden:

1. **Next-bar-open fills still assume you get the open price.** In reality a
   market order at the open can fill materially away from the printed open,
   especially on a gap. Slippage is modelled as a flat percentage, which
   understates tail risk on volatile opens.
2. **Holiday calendar is empty unless you supply one.** `MarketSession`
   ships with **no** holidays, because CLAUDE.md forbids hardcoding an
   unverified list. Only weekends are excluded by default. Supply your own
   via `--holidays holidays.csv`. Until you do, the engine cannot tell you
   that a date in your data was actually a market holiday.
3. **Cost rates are unverified assumptions.** The brokerage, STT, exchange
   transaction charge, GST, stamp duty, and slippage defaults in
   `app/config.py` were **not** verified against current official NSE/SEBI
   sources. Treat them as placeholders you must configure yourself. Net
   results are only as meaningful as those inputs.
4. **Daily-loss limit uses day-start equity.** The max-daily-loss check is
   measured against equity at the start of the trading day, computed from the
   last available close. It is not a tick-by-tick intraday drawdown guard.
5. **Exposure check uses entry prices, not live marks.** Existing exposure is
   valued at average entry price when checking the max-exposure limit, which
   understates exposure for a position that has moved up.
6. **No survivorship-bias handling.** The universe is whatever CSVs you
   supply. If you supply only symbols that still exist and did well, the
   results are biased and this tool cannot detect that for you.
7. **"Walk-forward" here is segmentation, not optimisation.** `evaluate
   --mode segmented` runs the SAME fixed parameters across contiguous
   windows. Because nothing is fitted anywhere in this project, there is no
   in-sample re-fitting step between windows. Calling it walk-forward
   *optimisation* would be false. It tests stability across periods only.
8. **Each evaluation segment re-warms its indicators.** Segments are run
   independently, so roughly the first `required_history` bars of every
   segment cannot produce a signal. Short windows can therefore report zero
   trades purely as a warmup artifact. The tool now detects this and labels
   such segments `too short to warm up` / `INCONCLUSIVE` rather than
   presenting the zeros as a strategy result — but read that label carefully.
9. **Single-position-per-symbol model.** The portfolio holds at most one
   position per symbol and does not support scaling in/out or partial fills.
10. **Intraday costs are the dominant unknown.** The intraday prototype
   trades far more often than the swing one, so it is far more sensitive to
   the unverified cost and slippage assumptions above. A small error in
   those rates changes its sign.

## About the bundled sample data

`data/samples/SAMPLE_*.csv` is a deterministic synthetic random walk, not real
market data. Any return, win rate, or drawdown computed on it describes the
random walk, not any real stock and not any real strategy edge.

## No performance claims

Both strategies' default parameters are experimental starting points. They
have not been validated on real data, have not been compared fairly against a
benchmark on real data, and are not represented as profitable. A moving-average
crossover is a well-known, widely-published rule; there is no claim of edge here.

On the synthetic sample data the intraday prototype currently **loses** money
net of assumed costs and underperforms buy-and-hold. That is the expected
outcome of trading a random walk with costs, and it is reported rather than
tuned away.

### Why the tool refuses to name a "better" strategy

`python -m app.cli compare` deliberately declines to rank strategies unless
each has at least 30 trades and the net-return margin exceeds 2%. On the
sample data neither condition holds, so it prints `NO WINNER DECLARED` even
though one strategy's headline number looks much better. A difference built
on 3 trades is noise, and ranking on it would be the exact mistake CLAUDE.md
section 17 warns against.
