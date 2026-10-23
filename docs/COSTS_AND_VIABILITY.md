# Costs, trade viability, and API rate limits

**PAPER TRADING — NO REAL ORDERS.** Nothing described here places, or can
place, a live order. No Groww credential is requested, stored, or used.

This document covers three connected things:

1. The charge model — what a simulated trade is charged, and where each rate
   came from.
2. The viability gate — why the system refuses trades whose charges would eat
   the profit.
3. API rate-limit budgeting — whether a given watchlist and scan interval
   could fit inside the published Groww Trading API limits.

---

## 1. Why this exists

Buy 20 shares at ₹19.00, sell at ₹19.05. Gross profit: ₹1.00.

With the default (unverified) assumptions in `app/config.py`, the system now
prices that round trip as:

| | Delivery | Intraday |
|---|---|---|
| Order value | ₹380.00 | ₹380.00 |
| Entry charges | ₹6.35 | ₹5.92 |
| Exit charges | ₹27.83 | ₹6.01 |
| **Round-trip charges** | **₹34.18** (9.0% of order value) | **₹11.93** (3.1%) |
| Gross profit at ₹19.05 | ₹1.00 | ₹1.00 |
| **Net result** | **−₹33.18** | **−₹10.93** |
| Breakeven exit price | ₹20.72 (+9.1%) | ₹19.60 (+3.2%) |

The trade needs a **9.1% move to break even**, not the 0.26% move it appears
to need. No amount of signal accuracy fixes this, because the problem is
arithmetic rather than prediction.

Two structural features of Indian cash-equity charges cause it, and a
percentage-only cost model hides both:

- **Brokerage has a floor.** 0.1% of ₹380 is ₹0.38, but the per-order minimum
  is ₹5 — so the small order pays 13× the percentage rate.
- **The DP charge on a delivery sell is flat.** ₹18.25 whether the position is
  ₹380 or ₹380,000. On a ₹380 exit it alone is 4.8% of turnover.

Cost as a share of turnover therefore explodes as order size shrinks. The
system now refuses these trades instead of booking them as winners.

## 2. The charge model

`app/backtest/costs.py` computes, per order leg:

```
brokerage      = clamp(pct × turnover, floor, cap)   per order
STT            = rate × turnover      delivery: both legs; intraday: sell only
exchange txn   = rate × turnover      both legs
SEBI turnover  = rate × turnover      both legs
IPFT           = rate × turnover      both legs
stamp duty     = rate × turnover      buy leg only
DP charge      = flat per sell leg    delivery only
GST            = rate × (brokerage + exchange txn + SEBI + IPFT + DP)
```

`compute_order_cost_breakdown()` returns every component itemised, so the
dashboard and a rejection reason can name *which* charge killed a trade.
Components are rounded to paise and the total is their exact sum, so a
displayed breakdown always adds up.

### Charge profiles

Delivery and intraday are charged differently, so each trade carries a
`ChargeProfile`:

| | Delivery (CNC) | Intraday (MIS) |
|---|---|---|
| STT | both legs | sell leg only, lower rate |
| Stamp duty | buy leg, higher rate | buy leg, lower rate |
| DP charge | flat, on sell | none |

The strategy declares the profile (`Strategy.charge_profile`), because only
the strategy knows its own holding horizon: `IntradayMomentumStrategy` is
`INTRADAY`, the swing strategy is `DELIVERY`. The profile is stamped onto the
signal, carried by the position, and recorded on the order and trade — so the
**exit leg is costed under the profile the position was opened with**, and a
stored trade can be re-priced later rather than re-guessed.

### Provenance of every rate — read this before trusting a number

**Every rate in `app/config.py` is an unverified, user-configurable
assumption.** CLAUDE.md §8 requires official verification before any figure is
presented as current, and that verification **failed**:

- `https://groww.in/charges` — HTTP 404.
- `https://groww.in/p/groww-brokerage-charges` — HTTP 404.
- `https://groww.in/calculators/brokerage-calculator` — loaded, but renders
  its rates client-side. The only figure readable in the page text was a
  footnote: *"A DP charge of ₹18.25 + GST will be charged for sell
  transaction"*, alongside *"The above charges are tentative. Please refer
  contract note for exact charges."* That is the source of the ₹18.25 default.
  (The page's worked example uses a hypothetical "Broker Z", not Groww's own
  rate — it must not be read as Groww's brokerage.)

Everything else is drawn from third-party aggregator summaries retrieved
2026-10-22, **which disagreed with each other** — notably on brokerage
percentage (0.05% vs 0.1%) and on whether delivery brokerage is zero. The
defaults chosen (0.1% capped at ₹20, floored at ₹5) are the more conservative
reading. Treat them as a placeholder.

**What to do about it:** take the figures off your own Groww contract note and
override them. Nothing else in the app needs to change:

```python
from app.config import AppConfig, CostAssumptions, ProfileCharges

config = AppConfig(
    costs=CostAssumptions(
        delivery=ProfileCharges(
            brokerage_pct=0.001, brokerage_cap=20.0, brokerage_min=5.0,
            dp_charge_per_sell=18.25,
        ),
        gst_rate=0.18,
    )
)
```

Backtest results carry the assumptions they were run under
(`runs.costs_json`), so an old run keeps its own numbers instead of being
silently re-priced by today's defaults. The pre-rework flat schema is
migrated, not reinterpreted.

## 3. The viability gate

`app/paper/viability.py` prices a proposed round trip **before** the broker
fills it and returns a verdict with a readable reason. The paper broker
enforces it in `_process_buy`, which is the single place a signal becomes a
position — so the backtest, the scanner, and any future caller all inherit it.

It is a **hard gate, not a silent resize**: failing a check means `NO_TRADE`
with a reason, exactly like the risk limits. It never quietly trades smaller.

### The checks (all configurable via `ViabilityPolicy`)

| Check | Default | Rationale |
|---|---|---|
| `min_order_value` | ₹2,500 | Below this, flat charges dominate. |
| `require_net_profit_at_target` | on | Hitting the target must make money. |
| `min_reward_to_cost_ratio` | 2.0 | Gross profit at target ≥ 2× charges. |
| `max_cost_pct_of_turnover` | 1% | Charges capped as a share of order value. |
| `min_net_reward_to_risk` | **0.0 (off)** | See below. |
| `enabled` | on | Off computes the figures without enforcing them. |

`min_net_reward_to_risk` is deliberately **off** by default. Charges cut the
reward and add to the risk, so a gross 1:1 setup can never show a net ratio of
1.0 — enforcing one would silently outlaw every symmetric strategy, including
high-win-rate ones that are perfectly sound. That is a view about trade shape
and win rate, not cost arithmetic, and this project has not researched it. Set
it to 1.0 if you want it enforced.

### What a rejection tells you

Rejections quote the numbers, and where a larger size would work, the size:

> not cost-viable: order value ₹380.00 is below the minimum ₹2,500.00: flat
> per-order charges (brokerage floor, DP charge) dominate an order this small;
> net profit at target is ₹-33.18 after ₹34.18 of round-trip charges (gross
> ₹1.00): hitting the target still loses money; price must reach ₹20.72 just
> to break even. Would need at least N shares for this setup to clear its own
> charges, but risk sizing allows only 20

`minimum_viable_quantity()` binary-searches the smallest size that passes, and
returns `None` when no size can work — which is the honest answer for a
5-paise target, since percentage charges scale with it.

The gate judges **economics, not share price**. The same ₹19 stock passes
with a target big enough to pay for the charges; it is not a penny-stock ban.

`TradeEconomics` is recorded for every entry the broker priced, including
rejected ones (`PaperBroker.trade_economics`), with `as_dict()` for storage
and the dashboard. A refusal is auditable, not mysterious.

## 4. Groww Trading API rate-limit budgeting

`app/services/rate_limits.py` is a **local calculator and gate**. It makes no
network call, holds no credential, and contains no Groww endpoint — v1 must
not connect to live order placement (CLAUDE.md §16), and this does not.

Its value is design-time: a watchlist and scan interval that quietly need 900
quote requests a minute is a design error, and it is better found by
calculation than by an HTTP 429 in the middle of a session.

### Assumed limits — also unverified

Limits apply **per category**, not per endpoint: every call in a category
draws on one shared budget.

| Category | Per second | Per minute | Per day |
|---|---|---|---|
| Authentication (token) | 5 | 30 | 150 |
| Orders | 10 | 250 | — |
| Live data (quote/LTP/OHLC) | 10 | 300 | — |
| Non-trading (positions, holdings…) | 20 | 500 | — |

**Provenance:** a web-search summary of the official documentation at
`groww.in/trade-api/docs`, retrieved 2026-10-22. **The official page was not
read directly** — that fetch was cancelled mid-session — and third-party
sources found alongside it disagreed (one listed 15/second for orders; an
older article 100 orders/minute). `GrowwApiLimits.verified` is therefore
`False`, and `plan_scan()` appends the caveat to every plan it returns, so a
displayed limit is never mistaken for a verified one. A `per_day` of `None`
means "not documented", never "unlimited".

### Scan planning

`plan_scan(watchlist_size, interval_seconds, ...)` turns "can I add 200
symbols?" into a number:

- 200 symbols, 60s interval → 200 requests/min against 300/min: **fits**, but
  warns that firing 200 at once breaches the 10/second ceiling, so spread each
  scan over ≥20s.
- 200 symbols, 30s interval → 400 requests/min: **does not fit**. The warning
  states both remedies — scan no more often than every 40s, or cut to 150
  symbols at that interval.
- One token refresh per minute over a 6.25-hour session → 375 against a
  150/day cap: **does not fit**.

A per-minute average hides bursts, so the per-second ceiling is checked
separately. This matters because the limits are per category: polling quotes
and reading positions compete for different budgets, and order calls for a
third.

### The gate

`RateLimitBudget` is a sliding-window gate over one category. Two deliberate
choices:

- **It refuses rather than sleeps.** A scan that cannot fetch fresh data
  should say so and skip the symbol, not block and then act on data that aged
  while it waited.
- **`now` is passed in, never read from the clock** — the same rule the
  scanner follows, so a decision is reproducible in a test and a backfilled
  run cannot believe it is live.

The scanner consults it before each provider call. Symbols it cannot afford
are reported as `SKIPPED_RATE_LIMIT` with a retry-after and surfaced in
`ScanReport.warnings`, because a skipped symbol means the scan was
**incomplete** — not that the symbol had no setup. This also implements the
anti-burst requirement in CLAUDE.md §10: a scanner recovering from an outage
cannot fire a backlog of requests.

One known wrinkle, by design: the budget is spent *before* the provider call,
so a request is consumed even when the bar turns out to be one already
processed. A bar's timestamp is only knowable after fetching it, so this is
unavoidable; the alternative would be to fetch first and gate afterwards,
which defeats the purpose.

## 5. Limitations

- **No rate is verified.** Every charge and every API limit here is an
  assumption. The modelled *structure* (floors, caps, flat DP charge, which
  leg pays what) is the durable part; the numbers are placeholders until you
  replace them from your contract note and the official API docs.
- The gate prices the **plan**, not the outcome: entry, stop and target as the
  strategy proposed them. A trade that exits early for another reason pays
  whatever the real exit costs.
- DP charge is applied once per sell leg. It is really per scrip per day, so a
  position exited in several orders on one day would be modelled as costlier
  than it should be. The paper broker exits in a single order, so this does
  not currently arise.
- Charges are modelled for **long cash-equity round trips only**. No
  shorting, no leverage, no derivatives.
- Passing the gate means a trade can *pay for itself if the target is hit*. It
  is not a prediction that the target will be hit, and not evidence that any
  strategy is profitable.
- `plan_scan` assumes a 6.25-hour session (09:15–15:30 IST) when checking
  daily caps, and that a scan issues its requests together.
