"""Telegram alerts: off by default, secret hygiene, throttling, local recording."""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from pydantic import SecretStr

from app.alerts.base import LABEL, Alert, AlertKind, NullNotifier
from app.alerts.dispatch import AlertDispatcher, alerts_from_scan
from app.alerts.telegram import TelegramNotifier, load_notifier
from app.scanner.scanner import ScanOutcome, ScanReport, SymbolScanResult
from app.storage.sqlite import SqliteRepository

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2025, 1, 2, 10, 0, tzinfo=IST)
TOKEN = "123456:SECRET-BOT-TOKEN"


def alert(key="k1", kind=AlertKind.SIGNAL):
    return Alert(kind, "INFY: simulated trade", "details", key, NOW)


def notifier(handler, **kw):
    return TelegramNotifier(SecretStr(TOKEN), "42", transport=httpx.MockTransport(handler),
                            sleep=lambda s: None, **kw)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for v in ("TELEGRAM_ENABLED", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(v, raising=False)


# --------------------------------------------------------- disabled by default

def test_disabled_by_default(tmp_path):
    assert isinstance(load_notifier(tmp_path / "missing.env"), NullNotifier)


def test_token_without_enable_flag_stays_off(tmp_path):
    f = tmp_path / ".env"
    f.write_text(f"TELEGRAM_BOT_TOKEN={TOKEN}\nTELEGRAM_CHAT_ID=42\n")
    assert isinstance(load_notifier(f), NullNotifier)


def test_enabled_but_incomplete_stays_off(tmp_path):
    f = tmp_path / ".env"
    f.write_text("TELEGRAM_ENABLED=true\nTELEGRAM_BOT_TOKEN=abc\n")
    assert isinstance(load_notifier(f), NullNotifier)


def test_fully_configured_enables(tmp_path):
    f = tmp_path / ".env"
    f.write_text(f"TELEGRAM_ENABLED=true\nTELEGRAM_BOT_TOKEN={TOKEN}\nTELEGRAM_CHAT_ID=42\n")
    n = load_notifier(f)
    assert isinstance(n, TelegramNotifier) and TOKEN not in repr(n)


def test_null_notifier_sends_nothing():
    assert NullNotifier().send(alert()) is False


# ------------------------------------------------------------------- delivery

def test_message_has_paper_label_and_goes_to_telegram_only():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"ok": True})

    assert notifier(handler).send(alert()) is True
    assert seen["url"] == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert seen["body"]["chat_id"] == "42"
    assert seen["body"]["text"].startswith(LABEL)
    assert "parse_mode" not in seen["body"]            # no markup injection from symbol names


def test_long_text_is_truncated():
    a = Alert(AlertKind.SYSTEM, "t", "x" * 10000, "k", NOW)
    assert len(a.render()) <= 3500


def test_transport_error_does_not_leak_token_in_state_or_logs(caplog):
    def handler(req):
        raise httpx.ConnectError(f"cannot reach {req.url}")
    n = notifier(handler)
    with caplog.at_level("DEBUG"):
        assert n.send(alert()) is False
    assert TOKEN not in (n.last_error or "") and TOKEN not in caplog.text
    assert TOKEN not in repr(n)


def test_429_honours_retry_after_but_is_capped_and_bounded():
    sleeps, calls = [], []

    def handler(req):
        calls.append(1)
        return httpx.Response(429, json={"parameters": {"retry_after": 999}})

    n = TelegramNotifier(SecretStr(TOKEN), "42", transport=httpx.MockTransport(handler),
                         sleep=sleeps.append, max_retries=2)
    assert n.send(alert()) is False
    assert len(calls) == 3 and max(sleeps) <= 5.0


def test_4xx_is_not_retried():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(401)
    assert notifier(handler).send(alert()) is False
    assert len(calls) == 1


def test_5xx_retries_then_succeeds():
    responses = iter([httpx.Response(502), httpx.Response(200, json={"ok": True})])
    assert notifier(lambda r: next(responses)).send(alert()) is True


# ------------------------------------------------------------- scan -> alerts

def scan_report():
    r = lambda sym, out, d: SymbolScanResult(sym, out, d, bar_timestamp=NOW)   # noqa: E731
    return ScanReport(NOW, [
        r("AAA", ScanOutcome.TRADED, "bought"), r("BBB", ScanOutcome.REJECTED_BROKER, "kill switch"),
        r("CCC", ScanOutcome.REJECTED_STALE, "35 min old"), r("DDD", ScanOutcome.ERROR, "provider down"),
        r("EEE", ScanOutcome.REJECTED_DATA, "bad bar"), r("FFF", ScanOutcome.NO_TRADE, "flat"),
        r("GGG", ScanOutcome.SKIPPED_SESSION, "closed"), r("HHH", ScanOutcome.SKIPPED_DUPLICATE, "seen"),
    ])


def test_scan_maps_to_the_right_alert_kinds_and_ignores_quiet_outcomes():
    kinds = {a.title.split(":")[0]: a.kind for a in alerts_from_scan(scan_report(), "swing")}
    assert kinds == {
        "AAA": AlertKind.SIGNAL, "BBB": AlertKind.RISK_LIMIT, "CCC": AlertKind.STALE_DATA,
        "DDD": AlertKind.DATA_ERROR, "EEE": AlertKind.DATA_ERROR,
    }


def test_signal_alert_says_fills_are_simulated():
    a = next(a for a in alerts_from_scan(scan_report(), "swing") if a.kind is AlertKind.SIGNAL)
    assert "simulated" in a.render() and a.render().startswith(LABEL)


# ------------------------------------------------- throttling + local storage

class Recorder(NullNotifier):
    def __init__(self, deliver=True):
        self.sent, self.deliver = [], deliver

    def send(self, a):
        self.sent.append(a.key)
        return self.deliver


def test_same_key_sent_once_in_process(tmp_path):
    rec = Recorder()
    d = AlertDispatcher(rec, None)
    assert d.dispatch([alert("a"), alert("a"), alert("b")]) == {"sent": 2, "failed": 0, "throttled": 1}
    assert rec.sent == ["a", "b"]


def test_throttle_survives_a_restart_via_the_ledger(tmp_path):
    db = tmp_path / "l.db"
    repo = SqliteRepository(db)
    AlertDispatcher(Recorder(), repo).dispatch([alert("stale:X:2025")])
    repo.close()

    repo2 = SqliteRepository(db)                       # a "new process"
    rec = Recorder()
    out = AlertDispatcher(rec, repo2).dispatch([alert("stale:X:2025")])
    assert out["throttled"] == 1 and rec.sent == []
    repo2.close()


def test_expired_throttle_window_sends_again(tmp_path):
    repo = SqliteRepository(tmp_path / "l.db")
    AlertDispatcher(Recorder(), repo).dispatch([alert("k")])
    rec = Recorder()
    out = AlertDispatcher(rec, repo, throttle=timedelta(seconds=-1)).dispatch([alert("k")])
    assert out["sent"] == 1
    repo.close()


def test_failed_delivery_is_recorded_as_a_warning_not_swallowed(tmp_path):
    repo = SqliteRepository(tmp_path / "l.db")
    out = AlertDispatcher(Recorder(deliver=False), repo).dispatch([alert("k")])
    assert out["failed"] == 1
    rows = repo._conn.execute("SELECT severity, message FROM app_events WHERE component='alerts'").fetchall()
    assert rows and rows[0][0] == "WARNING" and "NOT DELIVERED" in rows[0][1]
    repo.close()
