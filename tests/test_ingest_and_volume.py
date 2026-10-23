"""Tests for the bhavcopy import pipeline, volume analytics, universe report,
volume-filtered swing entry and weekly loss limit. No network."""

from __future__ import annotations

import json
import zipfile
from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.config import RiskLimits, SwingStrategyConfig
from app.data.ingest import import_bhavcopy_files
from app.models import Bar, SignalAction
from app.paper.portfolio import Portfolio
from app.paper.risk import RiskManager
from app.selection.volume import (
    UniverseCriteria, avg_traded_value, breakout_with_volume, evaluate_universe,
    relative_volume, trailing_zero_volume_days,
)
from app.strategies.swing import SwingMAStrategy
from tests.test_bhavcopy_provider import udiff_row, write_bhavcopy

IST = ZoneInfo("Asia/Kolkata")


def _dirs(tmp_path):
    return dict(raw_dir=tmp_path / "raw", processed_dir=tmp_path / "proc",
                manifest_dir=tmp_path / "man")


def _src(tmp_path):
    d = tmp_path / "src"
    d.mkdir()
    write_bhavcopy(d, "a.csv", [udiff_row("2025-01-06", "AAA"), udiff_row("2025-01-06", "BBB")])
    write_bhavcopy(d, "b.csv", [udiff_row("2025-01-08", "AAA", close=106)])
    return d


def test_import_keeps_raw_unchanged_and_writes_processed_and_manifest(tmp_path):
    src = _src(tmp_path)
    rep = import_bhavcopy_files([src], **_dirs(tmp_path))
    assert rep.rows_imported == 3 and rep.symbols == {"AAA", "BBB"}
    assert (tmp_path / "raw" / "a.csv").read_bytes() == (src / "a.csv").read_bytes()
    lines = (tmp_path / "proc" / "AAA.csv").read_text().splitlines()
    assert lines[0] == "date,open,high,low,close,volume" and len(lines) == 3
    m = json.loads(rep.manifest_path.read_text())
    assert "DAILY" in m["dataset"] and m["prices_adjusted_for_corporate_actions"] is False
    assert len(m["files"][0]["sha256"]) == 64
    # Tue 2025-01-07 has no file: reported, not filled.
    assert date(2025, 1, 7) in rep.missing_sessions


def test_reimport_is_idempotent(tmp_path):
    src = _src(tmp_path)
    import_bhavcopy_files([src], **_dirs(tmp_path))
    rep = import_bhavcopy_files([src], **_dirs(tmp_path))
    assert rep.rows_imported == 0 and rep.rows_skipped_duplicate > 0
    assert len((tmp_path / "proc" / "AAA.csv").read_text().splitlines()) == 3


def test_date_and_symbol_filters(tmp_path):
    rep = import_bhavcopy_files([_src(tmp_path)], start=date(2025, 1, 7),
                                symbols=["AAA"], **_dirs(tmp_path))
    assert rep.dates == {date(2025, 1, 8)} and rep.symbols == {"AAA"}


def test_bad_zip_and_legacy_and_wrong_report_fail_cleanly(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "bad.zip").write_bytes(b"not a zip")
    (src / "legacy.csv").write_text("SYMBOL,SERIES,TOTTRDQTY,PREVCLOSE\nX,EQ,1,1\n")
    (src / "empty.csv").write_text("")
    rep = import_bhavcopy_files([src], **_dirs(tmp_path))
    assert set(rep.files_failed) == {"bad.zip", "legacy.csv", "empty.csv"}
    assert rep.rows_imported == 0


def test_zip_input_and_invalid_ohlc_row_dropped(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    write_bhavcopy(src, "x.csv", [udiff_row("2025-01-06", "GOOD"),
                                  udiff_row("2025-01-06", "BAD", open_=100, high=90, low=95, close=96)])
    with zipfile.ZipFile(src / "x.csv.zip", "w") as z:
        z.write(src / "x.csv", "x.csv")
    (src / "x.csv").unlink()
    rep = import_bhavcopy_files([src / "x.csv.zip"], **_dirs(tmp_path))
    assert rep.symbols == {"GOOD"}
    assert any("BAD" in w and "dropped" in w for w in rep.warnings)


def test_two_dates_in_one_file_rejected(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    write_bhavcopy(src, "m.csv", [udiff_row("2025-01-06", "A"), udiff_row("2025-01-07", "B")])
    rep = import_bhavcopy_files([src / "m.csv"], **_dirs(tmp_path))
    assert "m.csv" in rep.files_failed


# --- volume -----------------------------------------------------------------

def mkbars(vols, closes=None, sym="T"):
    t0 = datetime(2025, 1, 1, tzinfo=IST)
    closes = closes or [100.0] * len(vols)
    return [Bar(sym, t0 + timedelta(days=i), c, c * 1.01, c * 0.99, c, v, "t", t0, "1d")
            for i, (v, c) in enumerate(zip(vols, closes))]


def test_relative_volume_excludes_current_bar_and_is_causal():
    bars = mkbars([100] * 20 + [300])
    assert relative_volume(bars, 20) == pytest.approx(3.0)
    assert relative_volume(bars[:-1], 20) is None  # only 19 prior bars
    # Bars after the decision point are never passed in, so cannot leak.
    assert relative_volume(bars, 20) == pytest.approx(3.0)


def test_breakout_needs_volume_confirmation():
    closes = [100.0] * 20 + [105.0]
    ok, _ = breakout_with_volume(mkbars([100] * 20 + [300], closes))
    weak, why = breakout_with_volume(mkbars([100] * 21, closes))
    assert ok and not weak and "rel vol" in why


def test_zero_volume_and_traded_value():
    assert trailing_zero_volume_days(mkbars([5, 5, 0, 0])) == 2
    assert avg_traded_value(mkbars([10, 10]), 2) == pytest.approx(1000.0)


def test_universe_explains_every_symbol():
    uni = {"GOOD": mkbars([1_000_000] * 80, [100.0] * 80, "GOOD"),
           "PENNY": mkbars([1_000_000] * 80, [10.0] * 80, "PENNY"),
           "THIN": mkbars([10] * 80, [100.0] * 80, "THIN"),
           "NEW": mkbars([1_000_000] * 5, [100.0] * 5, "NEW"),
           "EMPTY": []}
    rows = {r.symbol: r for r in evaluate_universe(uni, date(2025, 3, 21), "ts", UniverseCriteria())}
    assert set(rows) == set(uni)  # nothing silently dropped
    assert rows["GOOD"].eligible
    assert not rows["PENNY"].eligible and "price" in rows["PENNY"].reasons[0]
    assert not rows["THIN"].eligible
    assert not rows["NEW"].eligible and any("history" in r for r in rows["NEW"].reasons)
    assert not rows["EMPTY"].eligible
    assert all(r.reasons for r in rows.values())


def test_universe_flags_stale():
    uni = {"OLD": mkbars([1_000_000] * 80, [100.0] * 80, "OLD")}
    r = evaluate_universe(uni, date(2025, 6, 1), "ts")[0]
    assert not r.eligible and any("stale" in x for x in r.reasons)


# --- swing volume filter ----------------------------------------------------

def test_swing_volume_filter_blocks_low_volume_entry_only_when_enabled():
    cfg = dict(fast_ma_period=3, slow_ma_period=10)
    plain = SwingMAStrategy(SwingStrategyConfig(**cfg))
    bars = mkbars([100] * 64, [100.0] * 60 + [101, 103, 106, 110])
    buy_idx = next(i for i in range(12, len(bars) + 1)
                   if plain.evaluate(bars[:i]).action == SignalAction.BUY)
    sub = bars[:buy_idx]
    filt = SwingMAStrategy(SwingStrategyConfig(**cfg, volume_filter_enabled=True,
                                               min_relative_volume=1.5))
    assert plain.evaluate(sub).action == SignalAction.BUY
    s = filt.evaluate(sub)
    assert s.action == SignalAction.NO_TRADE and "volume filter" in s.reason
    sub[-1] = replace(sub[-1], volume=500)
    assert filt.evaluate(sub).action == SignalAction.BUY


# --- weekly loss ------------------------------------------------------------

def test_weekly_loss_limit_blocks_entries():
    rm = RiskManager(RiskLimits(max_weekly_loss_pct=0.05, max_daily_loss_pct=0.5,
                                max_drawdown_pct=0.9))
    p = Portfolio(100_000)
    p.mark_new_day(date(2025, 1, 6), {})          # Monday
    p.cash = 90_000                                # -10% since Monday
    p.mark_new_day(date(2025, 1, 7), {})          # Tuesday: daily resets, weekly not
    ok, why = rm.check_new_entry_allowed(p, "X", 10, 100.0, {})
    assert not ok and "weekly" in why
    p.mark_new_day(date(2025, 1, 13), {})         # next ISO week resets
    ok, _ = rm.check_new_entry_allowed(p, "X", 10, 100.0, {})
    assert ok
