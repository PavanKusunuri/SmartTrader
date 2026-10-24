# Security — local-only paper-trading dashboard

PAPER TRADING — NO REAL ORDERS. This document describes the app as it exists.
It has no broker connection, no order code and no network client code; the only
Groww-related code (`app/groww_env.py`) reads credentials from the environment
and makes no network call.

## Threat model (current version)

| Asset | Threat | Control |
|---|---|---|
| Ledger, trades, file paths | Malicious website reads the dashboard via DNS rebinding | Host-header allowlist (`app/api/security.py`) |
| Same | Another device on your LAN | Binds 127.0.0.1; non-loopback bind refused without `--i-understand-no-auth` |
| Same | Cross-site `fetch` to `/api/*` | `Sec-Fetch-Site: cross-site` refused; no CORS headers are ever sent |
| Dashboard UI | XSS from hostile CSV/ledger text | All data-bearing template values go through `esc()`; CSP `script-src 'self'`, no inline script |
| Dashboard UI | Clickjacking | `X-Frame-Options: DENY`, `frame-ancestors 'none'` |
| Ledger | Write/delete via the web UI | Every route is GET; SQLite opened `mode=ro` per request (test-enforced) |
| Source tree | Secret commit | `.env`, `*.db`, `logs/` git-ignored; no tracked secret files |

## Not protected (residual risk)

- **Malware or any process running as your Windows user** can read the ledger file,
  `.env`, and call the localhost API directly. Localhost is not authentication.
- The dashboard has **no login**. Loopback + Host check is the whole boundary.
- Tailwind runtime needs CSP `style-src 'unsafe-inline'`. Scripts are not relaxed.
- Dependencies are pinned but not hash-locked and not vulnerability-scanned
  (`pip-audit` not yet run). `app/api/static/vendor/tailwind.js` is third-party and unreviewed.
- CSP/headers are covered by HTTP tests, not by an automated browser test.
- Absolute ledger/data paths are still shown in `/api/status` for local usability.
  They are only readable by loopback-Host requests.

## If Groww credentials are ever added

1. Keep them in `.env` (git-ignored) or Windows Credential Manager; never in chat, logs or screenshots.
2. Revoke/regenerate API keys and tokens through Groww's own app/website API-keys page
   (official channel only), then review Groww order history and contact Groww if anything is unexpected.
3. Live ordering is out of scope (CLAUDE.md §3, §16). Before any such code exists, write the
   order-gateway design first: server-side proposal hash, single-use expiring approval bound to exact
   parameters, idempotency, risk limits and kill switch enforced outside the UI.

## Emergency stop (today)

Press Ctrl+C in the terminal running `python -m app.cli serve`, or
`Get-NetTCPConnection -State Listen -LocalPort 8000 | % { Stop-Process -Id $_.OwningProcess }`.
There is no trading process to stop; the kill switch in `app/config.py` blocks new simulated entries.

## Verifying

    .venv\Scripts\python.exe -m pytest tests\test_dashboard_security.py -v
    curl.exe -s -o NUL -w "%{http_code}`n" -H "Host: evil.example.com" http://127.0.0.1:8000/api/status   # expect 400
