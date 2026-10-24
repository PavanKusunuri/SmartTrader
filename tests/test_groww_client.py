"""Groww read-only client: allowlist, auth bodies, retries, secret hygiene.

All traffic goes through httpx.MockTransport. Nothing here touches the network
or needs credentials.
"""

import hashlib
import json

import httpx
import pytest

from app.data.groww_client import (
    GrowwAuthError, GrowwDataError, GrowwNotFound, GrowwRateLimited,
    GrowwReadOnlyClient, totp_now,
)

SECRET_TOKEN = "tok_SUPER_SECRET_123"


def make(handler, **kw):
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("min_interval", 0)
    return GrowwReadOnlyClient(SECRET_TOKEN, transport=httpx.MockTransport(handler), **kw)


def ok(payload):
    return httpx.Response(200, json={"status": "SUCCESS", "payload": payload})


# ------------------------------------------------------------ allowlist guard

@pytest.mark.parametrize("method,path", [
    ("POST", "/order/create"), ("GET", "/order/list"), ("POST", "/order-advance/create"),
    ("POST", "/order-advance/smart-order"), ("GET", "/positions/user"), ("GET", "/margins/detail/user"),
    ("POST", "/historical/candles"), ("GET", "/token/api/access"), ("DELETE", "/historical/candles"),
])
def test_anything_outside_the_three_endpoints_is_refused_before_any_network_call(method, path):
    calls = []
    client = make(lambda r: calls.append(r) or ok({}))
    with pytest.raises(GrowwDataError, match="not an allowed read-only endpoint"):
        client._request(method, path)
    assert calls == []


def test_client_exposes_no_order_methods():
    names = {n for n in dir(GrowwReadOnlyClient) if not n.startswith("_")}
    assert names == {"authenticate", "authenticated", "close", "historical_candles", "quote"}


# ------------------------------------------------------------------- requests

def test_candle_request_shape_and_envelope_unwrapped():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["authorization"]
        return ok({"candles": [["2025-09-24T10:30:00", 1, 2, 0.5, 1.5, 10, None]]})

    out = make(handler).historical_candles(
        exchange="NSE", segment="CASH", groww_symbol="NSE-WIPRO",
        start_time="2025-09-24 10:00:00", end_time="2025-09-24 12:00:00", candle_interval="30minute")
    assert out["candles"][0][5] == 10
    assert seen["url"].startswith("https://api.groww.in/v1/historical/candles?")
    assert "groww_symbol=NSE-WIPRO" in seen["url"] and "candle_interval=30minute" in seen["url"]
    assert seen["auth"] == f"Bearer {SECRET_TOKEN}"


def test_failure_envelope_raises_without_token():
    def handler(req):
        return httpx.Response(200, json={"status": "FAILURE", "error": {"code": "GA001", "message": "nope"}})
    with pytest.raises(GrowwDataError) as e:
        make(handler).quote(exchange="NSE", segment="CASH", trading_symbol="X")
    assert e.value.code == "GA001" and SECRET_TOKEN not in str(e.value)


# ----------------------------------------------------------------------- auth

def test_api_key_secret_flow_builds_documented_checksum(monkeypatch):
    monkeypatch.setattr("time.time", lambda: 1_700_000_000.0)
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        seen["auth"] = req.headers["authorization"]
        return ok({"token": "ACCESS"}) if False else httpx.Response(200, json={"token": "ACCESS"})

    c = GrowwReadOnlyClient(transport=httpx.MockTransport(handler), sleep=lambda s: None, min_interval=0)
    c.authenticate(api_key="KEY", api_secret="SEC")
    assert seen["auth"] == "Bearer KEY"
    assert seen["body"] == {
        "key_type": "approval", "timestamp": 1_700_000_000,
        "checksum": hashlib.sha256(b"SEC1700000000").hexdigest(),
    }
    assert c.authenticated


def test_totp_flow_sends_code_not_secret():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"token": "ACCESS"})

    c = GrowwReadOnlyClient(transport=httpx.MockTransport(handler), sleep=lambda s: None, min_interval=0)
    c.authenticate(totp_token="TT", totp_secret="GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ")
    assert seen["body"]["key_type"] == "totp" and len(seen["body"]["totp"]) == 6
    assert "GEZDGNBV" not in json.dumps(seen["body"])


def test_totp_matches_rfc6238_vector():
    # RFC 6238 appendix B, SHA-1, T=59s: 8-digit 94287082 -> last 6 digits 287082.
    assert totp_now("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", at=59) == "287082"


def test_access_token_needs_no_network():
    calls = []
    c = GrowwReadOnlyClient(transport=httpx.MockTransport(lambda r: calls.append(r)))
    c.authenticate(access_token="X")
    assert c.authenticated and calls == []


def test_incomplete_credentials_rejected():
    with pytest.raises(GrowwAuthError):
        GrowwReadOnlyClient().authenticate(api_key="only-key")


def test_unauthenticated_request_refused():
    with pytest.raises(GrowwAuthError):
        GrowwReadOnlyClient().quote(exchange="NSE", segment="CASH", trading_symbol="X")


# ---------------------------------------------------------- retries and errors

def test_401_is_not_retried_and_is_an_auth_error():
    n = []
    def handler(req):
        n.append(1)
        return httpx.Response(401, json={})
    with pytest.raises(GrowwAuthError):
        make(handler).quote(exchange="NSE", segment="CASH", trading_symbol="X")
    assert len(n) == 1


def test_404_is_not_retried():
    n = []
    def handler(req):
        n.append(1)
        return httpx.Response(404)
    with pytest.raises(GrowwNotFound):
        make(handler).quote(exchange="NSE", segment="CASH", trading_symbol="X")
    assert len(n) == 1


def test_429_retries_are_bounded_then_raise():
    n = []
    def handler(req):
        n.append(1)
        return httpx.Response(429)
    with pytest.raises(GrowwRateLimited):
        make(handler, max_retries=3).quote(exchange="NSE", segment="CASH", trading_symbol="X")
    assert len(n) == 4          # 1 try + 3 retries, no more


def test_transient_500_then_success():
    responses = iter([httpx.Response(503), httpx.Response(500), ok({"last_price": 1})])
    out = make(lambda r: next(responses)).quote(exchange="NSE", segment="CASH", trading_symbol="X")
    assert out == {"last_price": 1}


def test_backoff_sleeps_are_bounded():
    sleeps = []
    c = make(lambda r: httpx.Response(500), max_retries=8, sleep=sleeps.append)
    with pytest.raises(GrowwDataError):
        c.quote(exchange="NSE", segment="CASH", trading_symbol="X")
    assert max(sleeps) <= 10.0


def test_timeout_and_network_errors_become_data_errors_without_url_or_token():
    def handler(req):
        raise httpx.ConnectError(f"boom {req.url} {SECRET_TOKEN}")
    with pytest.raises(GrowwDataError) as e:
        make(handler, max_retries=1).quote(exchange="NSE", segment="CASH", trading_symbol="X")
    assert SECRET_TOKEN not in str(e.value) and "api.groww.in" not in str(e.value)
    assert "ConnectError" in str(e.value)


def test_redirects_are_not_followed_so_token_cannot_leak_to_another_host():
    seen = []
    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(302, headers={"location": "https://evil.example.com/steal"})
    with pytest.raises(GrowwDataError):
        make(handler).quote(exchange="NSE", segment="CASH", trading_symbol="X")
    assert all("evil.example.com" not in u for u in seen)


def test_non_json_response_is_a_data_error():
    with pytest.raises(GrowwDataError, match="not JSON"):
        make(lambda r: httpx.Response(200, text="<html>")).quote(exchange="NSE", segment="CASH", trading_symbol="X")


def test_repr_hides_token():
    assert SECRET_TOKEN not in repr(make(lambda r: ok({})))
