"""Local-dashboard hardening: Host allowlist, cross-site guard, security headers.

PAPER TRADING — NO REAL ORDERS. The dashboard has no authentication, so the
network boundary *is* the security boundary. Binding to 127.0.0.1 is necessary
but not sufficient: a malicious web page can use DNS rebinding to make the
browser send requests to 127.0.0.1 under an attacker-controlled hostname, and
the browser will then let that page read the responses. Rejecting any request
whose Host header is not one we expect defeats that, because the rebound request
still carries the attacker's hostname.
"""

from __future__ import annotations

from ipaddress import ip_address

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# Everything is served from this origin: no CDN, no third-party scripts. Style
# needs 'unsafe-inline' only because the vendored Tailwind runtime injects a
# <style> element; scripts get no such allowance.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cache-Control": "no-store",
}


def is_loopback(host: str) -> bool:
    """True for localhost and any loopback IP literal."""
    host = host.strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def hostname_of(host_header: str) -> str:
    """Hostname from a Host header, without port, lower-cased. Handles [::1]:8000."""
    value = host_header.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end != -1 else value
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


class LocalOnlyMiddleware:
    """Rejects unexpected Host headers and cross-site API requests, and adds
    security headers to every response."""

    def __init__(self, app: ASGIApp, allowed_hosts: frozenset[str] = LOOPBACK_HOSTS) -> None:
        self.app = app
        self.allowed_hosts = frozenset(h.lower() for h in allowed_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        rejection: PlainTextResponse | None = None

        if hostname_of(headers.get("host", "")) not in self.allowed_hosts:
            rejection = PlainTextResponse("Invalid Host header", status_code=400)
        elif (
            scope["path"].startswith("/api/")
            and headers.get("sec-fetch-site", "same-origin") == "cross-site"
        ):
            rejection = PlainTextResponse("Cross-site request refused", status_code=403)

        if rejection is not None:
            for name, value in SECURITY_HEADERS.items():
                rejection.headers[name] = value
            await rejection(scope, receive, send)
            return

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message["headers"]}
                extra = [
                    (k.lower().encode("latin-1"), v.encode("latin-1"))
                    for k, v in SECURITY_HEADERS.items()
                    if k.lower().encode("latin-1") not in existing
                ]
                message["headers"] = [*message["headers"], *extra]
            await send(message)

        await self.app(scope, receive, send_with_headers)
