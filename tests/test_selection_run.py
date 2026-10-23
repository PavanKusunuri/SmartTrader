"""End-to-end: UDiFF bhavcopy files + index CSV -> ranked output."""

from __future__ import annotations

import csv
from datetime import date, timedelta

from app.selection.run import main
from tests.test_bhavcopy_provider import udiff_row, write_bhavcopy


def _write_world(tmp_path, bullish_index: bool = True):
    bhav = tmp_path / "bhav"
    bhav.mkdir()
    start = date(2024, 9, 2)
    days = [start + timedelta(days=i) for i in range(130)]
    px = 100.0
    stock_close = []
    for i, _ in enumerate(days):
        px *= 1.0015 + (0.001 if i % 2 else -0.001)
        stock_close.append(px)
    flat = stock_close[-1]
    stock_close = stock_close[:-10] + [flat * (1 + 0.0005 * k) for k in range(1, 10)] + [flat * 1.015]
    for i, d in enumerate(days):
        vol = 4_000_000 if i == len(days) - 1 else 1_000_000
        write_bhavcopy(
            bhav, f"BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv",
            [udiff_row(d.isoformat(), "ZED", close=stock_close[i], volume=vol,
                       turnover=6e8, trades=9000)],
        )

    idx = tmp_path / "idx"
    idx.mkdir()
    with (idx / "NIFTY50.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        p = 20000.0
        for i, d in enumerate(days):
            p *= (1.0015 if bullish_index else 0.9985) + (0.001 if i % 2 else -0.001)
            w.writerow([d.isoformat(), p, p * 1.003, p * 0.997, p, 0])
    return bhav, idx


def test_end_to_end_ranks_and_labels_paper(tmp_path, capsys):
    bhav, idx = _write_world(tmp_path)
    assert main(["--bhavcopy-dir", str(bhav), "--index-dir", str(idx)]) == 0
    out = capsys.readouterr().out
    assert "PAPER TRADING -- NO REAL ORDERS" in out
    assert "TRENDING_BULLISH" in out
    assert "ZED" in out and "NOT a win probability" in out
    assert "NOT_NEWS_VERIFIED" in out


def test_bearish_index_gives_no_trade(tmp_path, capsys):
    bhav, idx = _write_world(tmp_path, bullish_index=False)
    main(["--bhavcopy-dir", str(bhav), "--index-dir", str(idx)])
    out = capsys.readouterr().out
    assert "NO TRADE" in out and "#1" not in out


def test_missing_index_is_no_trade_not_a_crash(tmp_path, capsys):
    bhav, _ = _write_world(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    main(["--bhavcopy-dir", str(bhav), "--index-dir", str(empty)])
    out = capsys.readouterr().out
    assert "UNCERTAIN" in out and "NO TRADE" in out
