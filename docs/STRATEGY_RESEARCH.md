# Strategy research and pre-checks

Web research done 2026-10-23. Mostly secondary sources and abstracts; **no
primary paper was read in full**. Treat every figure as indicative.

## The honest headline

There is no verified "most winning" strategy. The evidence says:

- SEBI's study of ~6.9M individual intraday traders in the NSE cash segment:
  **71% lost money in FY23**, rising to 80% for those making 500+ trades a year
  ([SEBI press release, Jul 2024](https://www.sebi.gov.in/media-and-notifications/press-releases/jul-2024/sebi-study-finds-that-7-out-of-10-individual-intraday-traders-in-equity-cash-segment-make-losses_84948.html)).
  No newer cash-segment study was found. FY25 F&O loss figures (~91%) come from
  secondary sources and are out of scope for this app.
- Published win rates are marketing-grade. Realistic swing figures are 35-50%
  with larger winners; claims of 60-80% are unverified and survivorship-biased.
- Win rate is not profit, so the pre-checks do **not** rank or filter by it.

## Candidates, ranked by evidence quality (not by win rate)

| Strategy | Horizon | Evidence | Cost sensitivity |
|---|---|---|---|
| Momentum / trend following (cross-sectional and time-series) | Swing | Academic, incl. NSE/BSE studies | Low |
| Pullback in an uptrend | Swing | Anecdotal | Low |
| Volatility / base breakout | Swing | Anecdotal | Low |
| Opening-range breakout on "stocks in play" | Intraday | Mixed | High |
| MA + momentum + volume (existing prototype) | Intraday | Anecdotal | High |

Key findings:

- **Momentum**: Indian studies report short-term momentum profits on NSE data
  (e.g. 1997-2013) and time-series momentum outperforming cross-sectional, but one
  NSE study finds long-run reversal, and a BSE study (1996-2020) finds absolute
  momentum suffers severe losses in crisis/recovery phases. Needs a regime filter.
  Sources: [IIMB Management Review](https://iimb.ac.in/imr/previous-issues/momentum-march-2020.php),
  [arXiv 2302.13245](https://www.arxiv.org/pdf/2302.13245),
  [risk-managed TS momentum, emerging markets](https://alicia.concytec.gob.pe/vufind/Record/ESAN_b19ca1844b0b1d632370de48a4d3ad68).
- **ORB**: Zarattini/Barbon/Aziz report after-cost profits on US stocks
  2016-2023 when restricted to "stocks in play"
  ([paper](https://abarbon.com/papers/a-profitable-day-trading-strategy-for-the-u-s-equity-market)).
  An independent replication on five index CFDs found
  [gross reproduced, net zero](https://www.mql5.com/en/blogs/post/776235) (a blog,
  not peer-reviewed). Never tested on NSE.
- **Pullback / breakout**: only blogs, vendor pages and closed-source scripts
  ([example](https://www.trade-ideas.com/learning-center/backtesting-strategy-development/backtesting-examples-for-traders/)).

## What "pre-checks" means here

`app/prechecks/` contains:

- `candidates.py` — the registry above, with evidence grade and caveats.
- `gate.py` — `run_prechecks(candidate, in_sample, out_of_sample, data_kind)`.
  A strategy must clear **all** of:
  1. real (non-synthetic) data,
  2. evidence not purely anecdotal,
  3. an out-of-sample result exists,
  4. >= 30 trades in-sample and out-of-sample,
  5. positive **net** expectancy in both,
  6. out-of-sample net return beats buy-and-hold,
  7. costs <= 50% of gross profit out of sample,
  8. for cost-sensitive (intraday) strategies, net-positive out of sample.

All failures are reported, not just the first. Passing means "worth further paper
study", never "profitable".

## Not done / limits

- The gate is a library; it is not yet wired into the CLI `compare`/`evaluate`
  commands or the scanner. Nothing blocks a scan from running a failed strategy.
- No real NSE data has been run through it. All bundled data is synthetic, so
  every existing strategy currently fails check 1 by design.
- Pullback, breakout and ORB are **not implemented**; only the registry entries exist.
- The 30-trade and 50%-cost thresholds are judgement calls, not derived values.
