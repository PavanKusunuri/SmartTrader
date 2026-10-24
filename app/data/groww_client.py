"""Minimal READ-ONLY Groww HTTP client (market data only).

PAPER TRADING — NO REAL ORDERS. This module can reach exactly three Groww
endpoints and nothing else:

    POST /token/api/access      exchange credentials for an access token
    GET  /historical/candles    historical OHLCV candles
    GET  /live-data/quote       point-in-time quote snapshot

There is no order, modify, cancel, smart-order, position or funds endpoint here,
and ``_request`` refuses any path outside that allowlist, so adding order
capability would require editing the allowlist (and would fail
tests/test_groww_readonly_guard.py). The official ``growwapi`` SDK is NOT used:
it needs ``pandas<3`` (this project pins pandas 3.x), drags in a dozen extra
packages, and bundles order methods. Endpoint paths, headers and the token
exchange below were taken from reading that SDK's source (v1.5.0, text only,
never installed or run) and Groww's docs at groww.in/trade-api/docs. They have
NOT been exercised against the live service by this project's tests.

Secrets: the token lives only in memory, is never logged, and never appears in
exception text (errors carry the HTTP status / Groww error code only).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import struct
import time
import uuid
from typing import Any, Callable

import httpx

BASE_URL = "https://api.groww.in/v1"

# The complete set of reachable endpoints. Do not extend this with anything that
# can create, change or cancel an order.
ALLOWED_GET_PATHS = frozenset({"/historical/candles", "/live-data/quote"})
ALLOWED_POST_PATHS = frozenset({"/token/api/access"})

log = logging.getLogger("smart_trader.groww")


class GrowwDataError(Exception):
    """Any failure talking to Groww. Never contains a credential."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


class GrowwAuthError(GrowwDataError):
    """401/403, a rejected credential, or an expired/unapproved key."""


class GrowwRateLimited(GrowwDataError):
    """429, still failing after bounded retries."""


class GrowwNotFound(GrowwDataError):
    """404: unknown symbol/endpoint."""


def totp_now(secret_base32: str, at: float | None = None) -> str:
    """RFC 6238 TOTP (SHA-1, 6 digits, 30 s step), so no extra dependency is needed."""
    import base64

    key = base64.b32decode(secret_base32.replace(" ", "").upper() + "=" * (-len(secret_base32) % 8))
    counter = int((time.time() if at is None else at) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 10**6
    return f"{code:06d}"


class GrowwReadOnlyClient:
    """Holds a bearer token in memory and exposes only the three calls above."""

    def __init__(
        self,
        token: str | None = None,
        *,
        base_url: str = BASE_URL,
        timeout: float = 15.0,
        max_retries: int = 3,
        min_interval: float = 0.25,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._max_retries = max(0, max_retries)
        self._min_interval = min_interval
        self._sleep = sleep
        self._clock = clock
        self._last_call = float("-inf")
        # follow_redirects stays False: a redirect must never carry the bearer
        # token to another host. TLS verification stays on (httpx default).
        self._http = httpx.Client(timeout=timeout, transport=transport, follow_redirects=False)

    def close(self) -> None:
        self._http.close()

    def __repr__(self) -> str:  # never show the token
        return f"GrowwReadOnlyClient(authenticated={self._token is not None})"

    # ------------------------------------------------------------------ auth

    @property
    def authenticated(self) -> bool:
        return self._token is not None

    def authenticate(
        self,
        *,
        access_token: str | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        totp_token: str | None = None,
        totp_secret: str | None = None,
    ) -> None:
        """Obtain a token using whichever documented flow the inputs complete.

        access_token wins (no network call). Daily-approval API keys must have
        been approved on Groww's API-keys page first, or this raises GrowwAuthError.
        """
        if access_token:
            self._token = access_token
            return
        if api_key and api_secret:
            ts = int(time.time())
            body = {
                "key_type": "approval",
                "checksum": hashlib.sha256((api_secret + str(ts)).encode()).hexdigest(),
                "timestamp": ts,
            }
            bearer = api_key
        elif totp_token and totp_secret:
            body = {"key_type": "totp", "totp": totp_now(totp_secret)}
            bearer = totp_token
        else:
            raise GrowwAuthError("no complete credential set supplied")
        payload = self._request("POST", "/token/api/access", bearer=bearer, json=body)
        token = payload.get("token") if isinstance(payload, dict) else None
        if not token:
            raise GrowwAuthError("token response did not contain a token")
        self._token = str(token)

    # ------------------------------------------------------------- data calls

    def historical_candles(
        self, *, exchange: str, segment: str, groww_symbol: str,
        start_time: str, end_time: str, candle_interval: str,
    ) -> dict[str, Any]:
        """Raw ``candles`` response: {"candles": [[ts, o, h, l, c, vol, oi], ...], ...}."""
        return self._request("GET", "/historical/candles", params={
            "exchange": exchange, "segment": segment, "groww_symbol": groww_symbol,
            "start_time": start_time, "end_time": end_time, "candle_interval": candle_interval,
        })

    def quote(self, *, exchange: str, segment: str, trading_symbol: str) -> dict[str, Any]:
        return self._request("GET", "/live-data/quote", params={
            "exchange": exchange, "segment": segment, "trading_symbol": trading_symbol,
        })

    # --------------------------------------------------------------- plumbing

    def _request(
        self, method: str, path: str, *, params: dict | None = None,
        json: dict | None = None, bearer: str | None = None,
    ) -> dict[str, Any]:
        allowed = ALLOWED_GET_PATHS if method == "GET" else ALLOWED_POST_PATHS
        if method not in ("GET", "POST") or path not in allowed:
            raise GrowwDataError(f"{method} {path} is not an allowed read-only endpoint")
        token = bearer or self._token
        if not token:
            raise GrowwAuthError("not authenticated")

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "x-request-id": str(uuid.uuid4()),
            "x-api-version": "1.0",
        }
        url = self._base_url + path
        last_error: GrowwDataError | None = None

        for attempt in range(self._max_retries + 1):
            wait = self._min_interval - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
            self._last_call = self._clock()
            try:
                response = self._http.request(method, url, headers=headers, params=params, json=json)
            except httpx.TimeoutException:
                last_error = GrowwDataError(f"timeout calling {path}")
            except httpx.HTTPError as exc:
                # type name only: httpx messages can echo the URL/query.
                last_error = GrowwDataError(f"network error calling {path}: {type(exc).__name__}")
            else:
                status = response.status_code
                if status in (401, 403):
                    raise GrowwAuthError(f"Groww rejected the credential (HTTP {status})", status=status)
                if status == 404:
                    raise GrowwNotFound(f"{path}: not found (HTTP 404)", status=404)
                if status == 400:
                    raise GrowwDataError(f"{path}: bad request (HTTP 400)", status=400, code=_error_code(response))
                if status == 429:
                    last_error = GrowwRateLimited("rate limited (HTTP 429)", status=429)
                elif status >= 500:
                    last_error = GrowwDataError(f"Groww server error (HTTP {status})", status=status)
                elif not response.is_success:
                    raise GrowwDataError(f"{path}: HTTP {status}", status=status)
                else:
                    return _unwrap(response, path)
            if attempt < self._max_retries:
                self._sleep(min(2.0 ** attempt, 10.0))  # bounded exponential backoff
        assert last_error is not None
        raise last_error


def _error_code(response: httpx.Response) -> str | None:
    try:
        return str(response.json().get("error", {}).get("code"))
    except Exception:  # noqa: BLE001 - diagnostic only
        return None


def _unwrap(response: httpx.Response, path: str) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise GrowwDataError(f"{path}: response was not JSON") from exc
    if not isinstance(body, dict):
        raise GrowwDataError(f"{path}: unexpected response shape")
    if body.get("status") == "FAILURE":
        err = body.get("error") or {}
        raise GrowwDataError(f"{path}: Groww error {err.get('code')}: {err.get('message')}", code=str(err.get("code")))
    payload = body["payload"] if "payload" in body else body
    if not isinstance(payload, dict):
        raise GrowwDataError(f"{path}: unexpected payload shape")
    return payload
