# Data sources — verified options as of 2026-10-22

This records what was actually checked, not assumptions. CLAUDE.md requires
verifying provider terms, redistribution restrictions, and cost before
implementing any provider, and forbids scraping or claiming something is
free without checking current official documentation. Findings below.

## Decision for Milestone 1

**CSV only: bundled sample OHLCV files + user-imported CSV files.** No
network calls, no provider adapter beyond `csv_provider.py`. This satisfies
CLAUDE.md section 6 directly and avoids every legal/reliability question
below until a provider is actually needed.

## Options checked for a later milestone

### NSE official website / bhavcopy files
- NSE (NSE Data & Analytics) published a "Data Usage and Sharing Policy"
  (issued 2024-10-03) asserting NSE's ownership of market data and
  restricting use to authorized sources, with redistribution controls.
- NSE's live site (`nseindia.com`) could not be fetched programmatically in
  this session — requests to it and to its archived-PDF host timed out,
  consistent with widely reported anti-automation behavior (the site
  expects a browser session with specific headers/cookies, which is why
  third-party libraries built around it have historically broken).
- Known community libraries built on scraping NSE's site —
  `nsepy`, `nsetools`, early `nsepython` — are reported deprecated or broken
  because NSE retired the endpoints/pages they depended on.
- **Conclusion: do not build an automated NSE scraper.** That would mean
  bypassing access controls, which CLAUDE.md explicitly forbids, and the
  policy language is restrictive enough that redistribution/automated-reuse
  rights are not clearly granted to an individual. A user can still
  **manually** download an official bhavcopy file from nseindia.com in a
  browser and feed it to `csv_provider.py` like any other CSV — that is a
  manual action by the user, not an automated scrape by the app, and stays
  consistent with "do not scrape websites or bypass access controls."

### yfinance (unofficial Yahoo Finance wrapper)
- Not an official API. It calls undocumented internal Yahoo endpoints that
  Yahoo can change or block without notice.
- Yahoo's own Terms of Service prohibit automated collection of data "for
  any purpose" without express prior permission, and bar commercial
  exploitation of the Services/APIs without written permission. Yahoo does
  grant a personal, revocable license to use software/APIs *it* provides —
  but that does not clearly cover third-party scraping of its internal
  endpoints.
- Practically: supports NSE tickers via a `.NS` suffix (e.g. `TCS.NS`) and
  BSE via `.BO`, with daily history going back years and shorter lookback
  windows for intraday intervals. Widely used for personal research, but
  the legal footing is genuinely gray, and reliability (breakage on Yahoo
  changes) is a real operational risk, not just a theoretical one.
- **Conclusion: not used in Milestone 1.** If a later milestone wants a
  free quasi-real/delayed source for convenience, it must be clearly
  labeled "unofficial, delayed, personal-use-only, may break without
  notice" in the UI and docs, and the user must explicitly opt in — this is
  a decision for the user to make with eyes open, not a default.

### Alpha Vantage
- Has a free tier, but reported limits are low (on the order of 25
  requests/day in recent community reports — unconfirmed against Alpha
  Vantage's own current page at implementation time) and Indian-market
  (NSE/BSE) coverage is reported thin/inconsistent — daily OHLC sometimes
  available, intraday and fundamentals for Indian symbols reported missing
  or unreliable by users.
- **Conclusion: not suitable as a primary source** for a watchlist-driven
  scanner even later; rate limits are too restrictive for repeated polling
  of more than one or two symbols. Would need re-verification against
  Alpha Vantage's own current documentation before any use.

## What this means going forward

No compliant, free, reliable real-time or near-real-time NSE data source
was found that doesn't involve either (a) scraping a site whose own policy
and technical behavior discourage it, or (b) relying on an unofficial
wrapper with unclear ToS standing. Per CLAUDE.md section 6 ("If no
compliant free real-time source is available, say so clearly and keep the
app usable with CSV/sample data"), the app's Milestone 1–3 design keeps
CSV/sample data as the only required path, with any live-ish provider
explicitly optional, clearly labeled, and opt-in later.
