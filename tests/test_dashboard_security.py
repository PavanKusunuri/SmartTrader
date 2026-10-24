"""Regression tests for the local dashboard's hardening (app/api/security.py).

Each test names the attack it blocks. Before the fix, a request with
Host: evil.example.com to the running dashboard returned HTTP 200 (observed with curl).
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.security import hostname_of, is_loopback

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"


@pytest.fixture()
def app_factory(tmp_path):
    def make(**kw):
        return create_app(db_path=str(tmp_path / "x.db"), samples_dir=str(SAMPLES_DIR), **kw)
    return make


@pytest.fixture()
def client(app_factory):
    return TestClient(app_factory(), base_url="http://127.0.0.1:8000")


@pytest.mark.parametrize("host", [
    "evil.example.com", "evil.example.com:8000", "127.0.0.1.evil.com",
    "localhost.evil.com", "192.168.1.5:8000", "",
])
def test_dns_rebinding_hosts_are_rejected(client, host):
    r = client.get("/api/status", headers={"Host": host})
    assert r.status_code == 400
    assert "mode" not in r.text


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost:8000", "LOCALHOST", "[::1]:8000"])
def test_loopback_hosts_are_accepted(client, host):
    assert client.get("/api/status", headers={"Host": host}).status_code == 200


def test_cross_site_api_requests_are_refused(client):
    r = client.get("/api/status", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert client.get("/api/status", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_page_may_be_navigated_to_from_elsewhere(client):
    # Following a link to the dashboard is harmless; only /api/ is guarded.
    assert client.get("/", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


def test_security_headers_on_page_api_static_and_errors(client):
    for path in ("/", "/api/status", "/static/app.js", "/nope"):
        h = client.get(path).headers
        assert "script-src 'self'" in h["content-security-policy"]
        assert "'unsafe-inline'" not in h["content-security-policy"].split("style-src")[0]
        assert "frame-ancestors 'none'" in h["content-security-policy"]
        assert h["x-frame-options"] == "DENY"
        assert h["x-content-type-options"] == "nosniff"
        assert h["referrer-policy"] == "no-referrer"
    bad = client.get("/api/status", headers={"Host": "evil.example.com"})
    assert bad.headers["x-frame-options"] == "DENY"


def test_no_cors_headers_are_ever_sent(client):
    r = client.get("/api/status", headers={"Origin": "http://evil.example.com"})
    assert "access-control-allow-origin" not in r.headers


def test_api_docs_off_by_default_and_opt_in(app_factory):
    off = TestClient(app_factory(), base_url="http://127.0.0.1:8000")
    assert off.get("/api/docs").status_code == 404
    assert off.get("/api/openapi.json").status_code == 404
    on = TestClient(app_factory(enable_docs=True), base_url="http://127.0.0.1:8000")
    assert on.get("/api/openapi.json").status_code == 200


def test_page_has_no_inline_script_so_csp_can_forbid_it(client):
    html = client.get("/").text
    import re
    inline = [m for m in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.+?)</script>", html, re.S) if m.strip()]
    assert inline == []
    assert client.get("/static/tailwind-config.js").status_code == 200


@pytest.mark.parametrize("value,expected", [
    ("127.0.0.1:8000", "127.0.0.1"), ("[::1]:8000", "::1"),
    ("LocalHost", "localhost"), ("evil.com:80", "evil.com"),
])
def test_hostname_parsing(value, expected):
    assert hostname_of(value) == expected


def test_is_loopback():
    assert is_loopback("127.0.0.1") and is_loopback("localhost") and is_loopback("::1")
    assert not is_loopback("0.0.0.0") and not is_loopback("192.168.1.5")


def test_serve_refuses_non_loopback_bind_without_acknowledgement(capsys):
    from app.cli import main
    assert main(["serve", "--host", "0.0.0.0"]) == 2
    assert "Refusing to bind" in capsys.readouterr().out
