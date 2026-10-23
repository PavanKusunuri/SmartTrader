# Findings: evidence-based intraday engine on Groww API (Stage 1)

Date: 2026-10-23. Scope: repository audit + Groww API documentation check. No strategy code
written, no backtest run, no credentials used, no live orders.

## 1. Existing project (audit)

- Paper-trading only; no live-order code path exists (CLAUDE.md sec. 3, 16).
- Present: CSV/bhavcopy providers + validation, causal backtest engine with costs/gap-aware stops,
  paper broker/portfolio/risk (1% risk sizing, exposure caps, daily/weekly loss, drawdown halt,
  kill switch), intraday + swing strategies, out-of-sample evaluation, pre-check gate,
  selection/ranking + volume analytics, scanner, FastAPI dashboard, SQLite persistence,
  rate-limit budget calculator, 409 tests passing at last full run (BUILD_LOG 2026-10-23).
- **Groww integration: none.** Only `app/services/rate_limits.py` (a budget calculator built from a
  search summary, flagged `verified=False`). No auth, no instrument master, no Groww data adapter.
- **Data on disk: daily bars only** (NSE bhavcopy, `data/processed/*.csv`). Intraday data is
  synthetic samples (2 symbols). **Intraday research cannot be done on real data yet.**

## 2. Groww API: verified from official docs (groww.in/trade-api/docs, read 2026-10-23)

| Topic | Documented |
|---|---|
| Auth | Bearer token. Three flows: access token (expires daily 6:00 AM), API key+secret (daily approval; token via `POST /v1/token/api/access`, checksum = SHA-256(secret+epoch)), TOTP. **Docs conflict** on whether TOTP needs daily approval. Active Trading API subscription required; price not stated on pages read. |
| Historical candles | `get_historical_candles` (current) : 1/2/3/5/10/15/30 min, 1h, 4h, 1d, 1w, 1m. Per request: ≤30 days for 1–5 min, ≤90 for 10–30 min, ≤180 for 1h+. "Equities, Indices and FNO available from 2020." Legacy `get_historical_candle_data` is deprecated and states intraday history is only the last 3 months. **The two pages disagree**; actual depth must be measured, not assumed. No historical rate limit documented. |
| Instruments | CSV at `growwapi-assets.groww.in/instruments/instrument.csv` (exchange_token, trading_symbol, groww_symbol, isin, segment, series, tick_size, buy/sell_allowed). |
| Live data | REST quote with depth, LTP/OHLC (≤50 symbols/call); WebSocket feed ≤1000 instruments (LTP, index value, market depth; sample shows 5 levels). |
| Orders | LIMIT, MARKET, SL, SL_M; products CNC, MIS (intraday), NRML; validity DAY only; modify/cancel/status/trade list; `order_reference_id` 8–20 chars for idempotency (error GA007 = duplicate). Segments CASH, FNO. |
| Portfolio/margin | positions, holdings, available margin, order-margin endpoints exist. Equity *position* push updates are not documented (only FNO). |
| Rate limits | Orders 10/s, 250/min; live data 10/s, 300/min; non-trading 20/s, 500/min; token endpoint 5/s, 30/min, 150/24h. |
| Not documented on pages read | Algo registration, static-IP requirement, SEBI API rules, subscription price. Curl sub-pages and smart-orders page not read. |

Caveat: this came from a subagent's read of the pages; it flagged internal contradictions (above).
I have not independently re-read the pages.

## 3. Conflict with project rules (needs your decision)

CLAUDE.md, which governs this repo, forbids in v1: live order placement, and requesting/storing
Groww credentials. The master prompt wants a Groww-executed engine. Proposed resolution, unless you
say otherwise:

1. **Do not build any order-placement code.** Stages 1–6 are research + paper only; Stage 8 (live
   pilot) stays out of scope until CLAUDE.md Milestone 6 review and your explicit approval.
2. Groww is used, if at all, **read-only for historical candles and instruments**. This needs your
   API subscription and key, which CLAUDE.md says not to handle in v1. You would keep secrets in a
   local `.env` that I never see; the adapter would read env vars only. That is a CLAUDE.md change
   only you can authorise.

## 4. Gaps blocking evidence-based conclusions

1. No real intraday history. Without it, ORB/VWAP/relative-volume strategies cannot be tested.
   Groww candles (if the subscription is active) are the one lawful candidate found; actual
   lookback is contradictory in the docs and must be probed with one read-only call.
2. No point-in-time index constituents, surveillance (ASM/GSM) lists, circuit-band data, or sector
   indices from a verified source. Survivorship bias on a current-constituent universe will be real.
3. Spread/depth history is not available from candles; spread cost has to be an assumption until
   paper observation records live quotes.
4. Regulatory (SEBI retail-algo, static IP, algo tagging) is unresolved: not on the Groww pages read.
   Needs the official SEBI/NSE circulars and Groww confirmation before any live step.
5. Multiple-testing control is needed before comparing strategy A–H (deflated Sharpe / reality-check
   style); the existing gate does not do this.

## 5. Research candidates (hypotheses, not findings)

No evidence yet for any. Priority order for testing once intraday data exists: (A) opening-range
breakout, (B) volume expansion + breakout, (C) VWAP continuation, (E) relative strength vs Nifty.
Mean reversion only if the data supports it. Costs and viability are already modelled
(`docs/COSTS_AND_VIABILITY.md`); SEBI data cited in BUILD_LOG says most retail intraday traders lose
money, so the default expectation is INCONCLUSIVE or NO EDGE.

## 6. Proposed next steps (awaiting approval)

1. You decide the questions below.
2. Build a read-only Groww data adapter (instrument CSV + candles, bounded retries, rate-limit
   budget, mocked tests), plus a probe command that reports the real intraday lookback.
3. Ingest into the existing validated-CSV pipeline with source/retrieval metadata.
4. Extend the backtest with intraday ORB/VWAP/RVOL candidates, time-of-day RVOL baseline,
   exit-method comparison, walk-forward, sensitivity to costs, multiple-testing correction.
5. Report with go/no-go. Paper trading on live quotes comes only after that.

## Questions that block step 2

- Do you have an active Groww Trading API subscription, and do you authorise a read-only adapter
  using credentials in your local `.env` (amending the CLAUDE.md no-credentials rule for data only)?
- Confirm that order placement stays unimplemented for now.
