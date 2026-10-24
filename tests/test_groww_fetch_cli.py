"""groww-fetch / groww-probe CLI behaviour with a fake provider (no network)."""

import csv
import json
from argparse import Namespace

import pytest

import app.groww_cli as gc
from app.data.csv_provider import CsvProvider
from app.data.groww_client import GrowwDataError
from app.data.groww_provider import GrowwProvider


class FakeClient:
    def __init__(self, rows):
        self.rows = rows

    def historical_candles(self, **kw):
        return {"candles": self.rows}


def rows(*stamps, c=100.5):
    return [[s, 100.0, 101.0, 99.0, c, 1000, None] for s in stamps]


def args(tmp_path, **kw):
    base = dict(symbols="INFY", interval="5m", days=5, out=str(tmp_path / "g"), env_file="x")
    base.update(kw)
    return Namespace(**base)


@pytest.fixture()
def patch_provider(monkeypatch):
    def install(rs):
        monkeypatch.setattr(gc, "build_provider", lambda env: GrowwProvider(FakeClient(rs)))
    return install


def test_fetch_writes_csv_the_existing_provider_can_read_plus_manifest(tmp_path, patch_provider):
    patch_provider(rows("2025-01-02T09:15:00", "2025-01-02T09:20:00"))
    assert gc.cmd_groww_fetch(args(tmp_path)) == 0
    out = tmp_path / "g" / "5m"
    bars = CsvProvider(out).get_bars("INFY", "5m")
    assert len(bars) == 2 and bars[0].close == 100.5
    m = json.loads((out / "_manifest.json").read_text())
    assert m["kind"] == "REAL_HISTORICAL_FROM_BROKER_API" and m["streaming"] is False
    assert m["symbols"]["INFY"]["rows_in_file"] == 2 and "tz-assumed-IST" in m["timestamp_assumption"]


def test_repeat_fetch_merges_instead_of_overwriting_history(tmp_path, patch_provider):
    patch_provider(rows("2025-01-02T09:15:00"))
    gc.cmd_groww_fetch(args(tmp_path))
    patch_provider(rows("2025-01-03T09:15:00", c=111.0))
    gc.cmd_groww_fetch(args(tmp_path))
    with open(tmp_path / "g" / "5m" / "INFY.csv", newline="") as f:
        got = list(csv.DictReader(f))
    assert [r["date"] for r in got] == ["2025-01-02 09:15:00", "2025-01-03 09:15:00"]


def test_one_failing_symbol_does_not_abort_the_rest_and_exit_code_says_so(tmp_path, monkeypatch):
    class Flaky(GrowwProvider):
        def get_bars(self, symbol, *a, **k):
            if symbol == "BAD":
                raise GrowwDataError("boom")
            return super().get_bars(symbol, *a, **k)

    monkeypatch.setattr(gc, "build_provider", lambda env: Flaky(FakeClient(rows("2025-01-02T09:15:00"))))
    assert gc.cmd_groww_fetch(args(tmp_path, symbols="BAD,INFY")) == 1
    assert (tmp_path / "g" / "5m" / "INFY.csv").exists() and not (tmp_path / "g" / "5m" / "BAD.csv").exists()


def test_no_credentials_exits_2_without_traceback(tmp_path, monkeypatch, capsys):
    for v in ("GROWW_ACCESS_TOKEN", "GROWW_API_KEY", "GROWW_API_SECRET", "GROWW_TOTP_TOKEN", "GROWW_TOTP_SECRET"):
        monkeypatch.delenv(v, raising=False)
    assert gc.cmd_groww_fetch(args(tmp_path, env_file=str(tmp_path / "none.env"))) == 2
    assert "Cannot authenticate" in capsys.readouterr().out


def test_unsupported_interval_rejected(tmp_path):
    assert gc.cmd_groww_fetch(args(tmp_path, interval="7m")) == 2


def test_probe_reports_deepest_age(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gc, "build_provider", lambda env: GrowwProvider(FakeClient(rows("2025-01-02T09:15:00"))))
    code = gc.cmd_groww_probe(Namespace(symbol="INFY", interval="5m", env_file="x"))
    out = capsys.readouterr().out
    assert code == 0 and "CHECK BY HAND" in out and "Deepest age" in out
