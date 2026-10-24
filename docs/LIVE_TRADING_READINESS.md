# Live-trading readiness review (research + design only)

Date: 2026-10-24. PAPER TRADING — NO REAL ORDERS. **No order-placement code exists and none was
written.** This is the fresh review CLAUDE.md §16 requires before any live work. Live trading stays
out of scope (Milestone 6) until you have reviewed this and explicitly approved a separate plan.

Evidence grades: **[G]** stated on Groww's own pages (read 2026-10-24); **[S]** secondary source only
(broker blogs, forums, law-firm summaries), not checked against the primary SEBI/NSE text;
**[?]** not stated anywhere I read — must be confirmed with Groww or the circulars.

## 1. What is established

| Topic | Finding |
|---|---|
| Static IP for orders | **[G]** Groww blog "Static IP for API Trading: Setup Guide" (dated 02 Sep 2026) says order placement must originate from a static IP and Groww whitelists it: `groww.in/trade-api/api-keys` → "Add static IP", primary plus optional secondary. Groww cites no SEBI text. |
| Static IP: other endpoints | **[?]** Page does not say whether data/portfolio calls need it. |
| IP change rules | **[?]** Groww: not stated. **[S]** NSE standards reportedly allow one change per calendar week; unverified. |
| Static IP options listed by Groww | **[G]** ISP static-IP add-on (page quotes ₹300–₹1,000/month) or a cloud VPS with a static public IP (page quotes $5–$50/month). Prices are Groww's blog figures, not verified quotes. |
| Home IP / phone hotspot | Follows from the above: a changing home IP or a hotspot failover would present a non-whitelisted IP, so **orders would likely be refused**. Test with a tiny order before relying on any path. |
| Broker-side exits | **[G]** `create_smart_order`: GTT (single trigger) and **OCO** (target + stop-loss, one cancels the other). OCO "is meant to exit an existing position"; quantity ≤ open net position; CASH supports MIS. Status values ACTIVE/TRIGGERED/CANCELLED/EXPIRED/FAILED/COMPLETED. |
| Does OCO survive my PC going offline? | **[?]** Docs say ACTIVE = "monitoring trigger conditions" but never explicitly say it runs server-side while the client is offline. Must be proven with a small test. |
| Entry + attached stop in one call | **[?]** GTT has an optional `child_legs` ("bracket orders") whose structure is undocumented. Treat as unavailable. Realistic flow: entry → confirm filled → create OCO → confirm ACTIVE. The gap between fill and OCO is a risk window. |
| MIS auto square-off | **[?]** Docs say only "must close by day end." Do not assume Groww squares off for you or when. The app must exit on its own, plus the OCO. |
| Order idempotency | **Conflict.** Smart orders: `reference_id` is called an idempotency key **[G]**. Plain `place_order`: `order_reference_id` is only "to track the order"; duplicate rejection is **not** stated **[G]**. The earlier note (FINDINGS_INTRADAY_GROWW.md) says duplicates give error GA007; that is unconfirmed. Therefore: never auto-retry a timed-out `place_order`; look it up with `get_order_status_by_reference` first. |
| Auth | **[G]** API key+secret needs daily approval on Groww; TOTP flow described as "No Expiry" (earlier note says Groww's pages disagree); access-token lifetime not stated; token endpoint limited to 150 requests/24h. Intro says "OAuth 2.0" but documented flows are not OAuth. |
| Rate limits | **[G]** Orders 10/s, 250/min; non-trading 20/s, 500/min. |
| Algo registration / tagging | **[S]** Retail algos under an orders-per-second threshold (commonly reported as 10/s) need no exchange registration but are tagged; self-developed algos above it must register via the broker; daily API logout, 2FA, key + static IP. Effective date reported variously (April 1 vs August 1, 2026). **[?]** Groww pages say nothing on this. Read the SEBI circular (4 Feb 2025) and the NSE implementation standards, and ask Groww support, before go-live. |
| Source discrepancy | A search snippet described the Groww static-IP post as March 2026 and mentioning "Groww Cloud"; the page fetched says 02 Sep 2026 and does not mention it. Re-check on Groww's site; do not plan around Groww Cloud. |

## 2. Failure scenarios and required behaviour

Principle: **the broker must hold the protection, not the PC.** Backup internet or a UPS lowers how often
you are blind; it cannot remove it.

| Scenario | Required behaviour |
|---|---|
| Internet lost, no open positions | No new orders until a fresh connectivity + account check passes. |
| Internet lost, position open | Protection is the already-ACTIVE OCO at Groww **only if** testing proves it runs server-side. You exit via the Groww phone app. |
| PC dead / battery flat | As above. Plan for it: phone has Groww app logged in; UPS on router + PC. |
| Fill happened, OCO creation failed | Highest-risk window. App retries creation, then alerts you, then attempts a market exit if configured. Must be tested against a mock. |
| `place_order` timeout / lost reply | Query by reference id; never resend blindly. Unknown ⇒ treat as possibly filled. |
| Recovery after outage | Reconcile positions, orders, funds against Groww before anything else; do not act on stale signals; no burst of catch-up orders. |
| Groww outage / rate-limit / order rejected repeatedly | Halt new entries, alert, keep kill switch engaged until you clear it. |
| Failover to hotspot | Orders likely refused (IP not whitelisted). App must report "orders unavailable" rather than retry. |
| Token expired mid-day | Halt entries; alert. Daily approval needs you present each morning. |
| Unexpected order/position found at Groww | Kill switch, alert, no auto-correction. |

## 3. Controls required before any live code is merged

1. Single order gateway; deny by default; live disabled at every startup and after any incident.
2. Server-side proposal → hash → your explicit approval bound to exact parameters → short expiry →
   single use → revalidation (data freshness, risk limits) at submit time.
3. Risk limits enforced from **Groww's** margin/positions, not only the app's records.
4. Kill switch outside the dashboard (file flag or OS-level), blocking new orders immediately.
5. Credentials in Windows Credential Manager or a git-ignored `.env`; never in logs; rotation/revocation
   runbook through Groww's API-keys page.
6. Paper-observation period on real quotes first (CLAUDE.md Milestone 4) with strategy evidence; this repo's
   own findings say the default expectation is NO EDGE / INCONCLUSIVE and most retail intraday traders lose money.
7. All order tests against mocks or Groww-supported sandbox only; first real order, if ever approved,
   is a single minimal-quantity test chosen and placed by you.

## 4. Decisions for you

1. Static IP: ISP static-IP add-on at home, or a small fixed-IP VPS for the order-sending part? The VPS
   contradicts your local-only requirement and brings credentials off your PC; choose knowingly.
2. Who verifies the SEBI/NSE rules: I can pull the primary circulars next; you should also ask Groww support
   the [?] items in §1 in writing.
3. Approve a separate implementation plan only after §1 [?] items are answered.
