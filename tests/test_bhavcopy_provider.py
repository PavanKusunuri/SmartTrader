"""Tests for the NSE UDiFF bhavcopy provider.

The header used here is the REAL 34-column UDiFF header, copied from an
actual 2025-12-30 CM-UDiFF file. Fixtures that invent a convenient column
layout would pass while the parser failed on the only input that matters.
"""

from __future__ import annotations

import csv
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.data.bhavcopy_provider import (
    BhavcopyFormatError,
    BhavcopyProvider,
)
from app.models import DataQuality

IST = ZoneInfo("Asia/Kolkata")

# Verified real UDiFF header, in file order.
UDIFF_HEADER = [
    "TradDt", "BizDt", "Sgmt", "Src", "FinInstrmTp", "FinInstrmId", "ISIN",
    "TckrSymb", "SctySrs", "XpryDt", "FininstrmActlXpryDt", "StrkPric",
    "OptnTp", "FinInstrmNm", "OpnPric", "HghPric", "LwPric", "ClsPric",
    "LastPric", "PrvsClsgPric", "UndrlygPric", "SttlmPric", "OpnIntrst",
    "ChngInOpnIntrst", "TtlTradgVol", "TtlTrfVal", "TtlNbOfTxsExctd",
    "SsnId", "NewBrdLotQty", "Rmks", "Rsvd1", "Rsvd2", "Rsvd3", "Rsvd4",
]

LEGACY_HEADER = [
    "SYMBOL", "SERIES", "OPEN", "HIGH", "LOW", "CLOSE", "LAST",
    "PREVCLOSE", "TOTTRDQTY", "TOTTRDVAL", "TIMESTAMP", "TOTALTRADES", "ISIN",
]


def udiff_row(
    date: str,
    symbol: str,
    close: float = 104.0,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: int = 50_000,
    turnover: str | float = 5_200_000.0,
    trades: str | int = 1_200,
    series: str = "EQ",
    instrument: str = "STK",
) -> dict:
    """Builds one full-width UDiFF row with every column present.

    Open/high/low are derived from `close` unless given, so a fixture that
    only varies the close still produces an OHLC-consistent bar. Hand-picked
    constants here would be flagged INVALID by `validate_series` and the
    test would be measuring the fixture, not the parser.
    """

    open_ = close if open_ is None else open_
    high = max(open_, close) * 1.005 if high is None else high
    low = min(open_, close) * 0.995 if low is None else low

    row = dict.fromkeys(UDIFF_HEADER, "")
    row.update(
        {
            "TradDt": date,
            "BizDt": date,
            "Sgmt": "CM",
            "Src": "NSE",
            "FinInstrmTp": instrument,
            "ISIN": f"INE000{abs(hash(symbol)) % 10000:04d}01",
            "TckrSymb": symbol,
            "SctySrs": series,
            "FinInstrmNm": symbol,
            "OpnPric": open_,
            "HghPric": high,
            "LwPric": low,
            "ClsPric": close,
            "LastPric": close,
            "PrvsClsgPric": open_,
            "TtlTradgVol": volume,
            "TtlTrfVal": turnover,
            "TtlNbOfTxsExctd": trades,
            "SsnId": "F1",
            "NewBrdLotQty": 1,
        }
    )
    return row


def write_bhavcopy(directory, filename: str, rows: list[dict], header=None) -> None:
    path = directory / filename
    with path.open("w", newline="", encoding="ascii") as fh:
        writer = csv.DictWriter(fh, fieldnames=header or UDIFF_HEADER)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


@pytest.fixture
def bhav_dir(tmp_path):
    """Two trading dates, three instruments: a normal EQ stock, a second EQ
    stock, and a gold bond that must never be mistaken for a share."""

    d = tmp_path / "bhavcopy"
    d.mkdir()
    write_bhavcopy(
        d,
        "BhavCopy_NSE_CM_0_0_0_20250102_F_0000.csv",
        [
            udiff_row("2025-01-02", "TCS", close=4000.0, turnover=8_000_000.0, trades=2_000),
            udiff_row("2025-01-02", "INFY", close=1500.0, turnover=3_000_000.0, trades=900),
            udiff_row("2025-01-02", "SGBJUN28", close=14303.89, series="GB"),
        ],
    )
    write_bhavcopy(
        d,
        "BhavCopy_NSE_CM_0_0_0_20250103_F_0000.csv",
        [
            udiff_row("2025-01-03", "TCS", close=4040.0, turnover=9_500_000.0, trades=2_200),
            udiff_row("2025-01-03", "INFY", close=1490.0, turnover=2_800_000.0, trades=850),
        ],
    )
    return d


# --- parsing and pivoting --------------------------------------------------


def test_pivots_cross_sectional_files_into_per_symbol_series(bhav_dir):
    provider = BhavcopyProvider(bhav_dir)

    bars = provider.get_bars("TCS")

    assert [b.close for b in bars] == [4000.0, 4040.0]
    assert [b.timestamp.date().isoformat() for b in bars] == ["2025-01-02", "2025-01-03"]


def test_symbol_lookup_is_case_insensitive(bhav_dir):
    provider = BhavcopyProvider(bhav_dir)

    assert len(provider.get_bars("tcs")) == 2


def test_unknown_symbol_returns_empty_not_error(bhav_dir):
    provider = BhavcopyProvider(bhav_dir)

    assert provider.get_bars("NOTLISTED") == []


def test_timestamps_are_ist_aware_midnight(bhav_dir):
    provider = BhavcopyProvider(bhav_dir)

    bar = provider.get_bars("TCS")[0]

    assert bar.timestamp == datetime(2025, 1, 2, tzinfo=IST)
    assert bar.timestamp.tzinfo is not None


def test_records_provenance_naming_the_source_file(bhav_dir):
    provider = BhavcopyProvider(bhav_dir)

    bar = provider.get_bars("TCS")[0]

    assert bar.source.startswith("nse-udiff-bhavcopy:")
    assert "20250102" in bar.source
    assert bar.interval == "1d"


def test_bars_are_validated(bhav_dir):
    provider = BhavcopyProvider(bhav_dir)

    assert all(b.quality == DataQuality.OK for b in provider.get_bars("TCS"))


# --- instrument and series filtering ---------------------------------------


def test_excludes_non_equity_instruments(bhav_dir):
    """A gold bond sits in the same file as the shares. Series GB and
    instrument STK both differ, and it must not surface as a stock."""

    provider = BhavcopyProvider(bhav_dir)

    assert "SGBJUN28" not in provider.available_symbols()
    assert provider.get_bars("SGBJUN28") == []


def test_excludes_non_default_series_but_can_be_opted_into(tmp_path):
    d = tmp_path / "b"
    d.mkdir()
    write_bhavcopy(
        d, "day.csv",
        [
            udiff_row("2025-01-02", "NORMAL", series="EQ"),
            udiff_row("2025-01-02", "TRADETOTRADE", series="BE"),
        ],
    )

    assert BhavcopyProvider(d).available_symbols() == ["NORMAL"]

    widened = BhavcopyProvider(d, allowed_series={"EQ", "BE"})
    assert widened.available_symbols() == ["NORMAL", "TRADETOTRADE"]


# --- format rejection ------------------------------------------------------


def test_legacy_bhavcopy_is_rejected_with_an_actionable_message(tmp_path):
    """Legacy columns must never be parsed on a guess -- a wrong guess
    silently misreads prices rather than failing."""

    d = tmp_path / "b"
    d.mkdir()
    write_bhavcopy(
        d, "cm02JAN2024bhav.csv",
        [dict.fromkeys(LEGACY_HEADER, "1")],
        header=LEGACY_HEADER,
    )

    with pytest.raises(BhavcopyFormatError) as exc:
        BhavcopyProvider(d).get_bars("TCS")

    assert "LEGACY" in str(exc.value)
    assert "2024-07-08" in str(exc.value)


def test_file_missing_required_columns_is_rejected(tmp_path):
    d = tmp_path / "b"
    d.mkdir()
    partial = ["TradDt", "TckrSymb", "ClsPric"]
    write_bhavcopy(d, "odd.csv", [dict.fromkeys(partial, "x")], header=partial)

    with pytest.raises(BhavcopyFormatError, match="missing columns"):
        BhavcopyProvider(d).get_bars("TCS")


def test_non_numeric_price_is_rejected_not_coerced(tmp_path):
    d = tmp_path / "b"
    d.mkdir()
    row = udiff_row("2025-01-02", "TCS")
    row["ClsPric"] = "n/a"
    write_bhavcopy(d, "day.csv", [row])

    with pytest.raises(BhavcopyFormatError, match="non-numeric"):
        BhavcopyProvider(d).get_bars("TCS")


def test_duplicate_symbol_in_one_file_is_rejected(tmp_path):
    d = tmp_path / "b"
    d.mkdir()
    write_bhavcopy(
        d, "day.csv",
        [udiff_row("2025-01-02", "TCS"), udiff_row("2025-01-02", "TCS", close=1.0)],
    )

    with pytest.raises(BhavcopyFormatError, match="twice"):
        BhavcopyProvider(d).get_bars("TCS")


def test_empty_directory_names_the_fix(tmp_path):
    d = tmp_path / "b"
    d.mkdir()

    with pytest.raises(FileNotFoundError, match="nseindia.com"):
        BhavcopyProvider(d).get_bars("TCS")


def test_missing_directory_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError, match="directory not found"):
        BhavcopyProvider(tmp_path / "nope").get_bars("TCS")


# --- zip handling ----------------------------------------------------------


def test_reads_nse_zip_without_unpacking(tmp_path):
    d = tmp_path / "b"
    d.mkdir()
    inner = tmp_path / "BhavCopy_NSE_CM_0_0_0_20250102_F_0000.csv"
    with inner.open("w", newline="", encoding="ascii") as fh:
        writer = csv.DictWriter(fh, fieldnames=UDIFF_HEADER)
        writer.writeheader()
        writer.writerow(udiff_row("2025-01-02", "TCS", close=4000.0))

    with zipfile.ZipFile(d / "bhav.csv.zip", "w") as zf:
        zf.write(inner, arcname=inner.name)

    bars = BhavcopyProvider(d).get_bars("TCS")

    assert [b.close for b in bars] == [4000.0]


# --- interval and quote contract -------------------------------------------


def test_intraday_interval_is_refused(bhav_dir):
    """End-of-day data must never be served as if it were intraday."""

    with pytest.raises(ValueError, match="end-of-day"):
        BhavcopyProvider(bhav_dir).get_bars("TCS", interval="5m")


def test_no_quote_capability(bhav_dir):
    assert BhavcopyProvider(bhav_dir).get_latest_quote("TCS") is None


# --- date slicing ----------------------------------------------------------


def test_start_and_end_filter_inclusively(bhav_dir):
    provider = BhavcopyProvider(bhav_dir)

    only_second = provider.get_bars("TCS", start="2025-01-03")
    only_first = provider.get_bars("TCS", end="2025-01-02")

    assert [b.close for b in only_second] == [4040.0]
    assert [b.close for b in only_first] == [4000.0]


# --- liquidity -------------------------------------------------------------


def test_liquidity_exposes_turnover_and_trade_count(bhav_dir):
    records = BhavcopyProvider(bhav_dir).get_liquidity("TCS")

    assert [r.turnover for r in records] == [8_000_000.0, 9_500_000.0]
    assert [r.trade_count for r in records] == [2_000, 2_200]


def test_blank_turnover_yields_no_record_rather_than_zero(tmp_path):
    """Missing liquidity must read as unknown, never as zero -- zero would
    make a liquid stock look untradeable to a filter."""

    d = tmp_path / "b"
    d.mkdir()
    write_bhavcopy(
        d, "day.csv",
        [udiff_row("2025-01-02", "TCS", turnover="", trades="")],
    )
    provider = BhavcopyProvider(d)

    assert provider.get_liquidity("TCS") == []
    assert len(provider.get_bars("TCS")) == 1  # the price bar still loads


def test_liquidity_respects_date_slicing(bhav_dir):
    records = BhavcopyProvider(bhav_dir).get_liquidity("TCS", start="2025-01-03")

    assert [r.turnover for r in records] == [9_500_000.0]


# --- caching and introspection ---------------------------------------------


def test_available_symbols_is_the_prefilter_universe(bhav_dir):
    assert BhavcopyProvider(bhav_dir).available_symbols() == ["INFY", "TCS"]


def test_a_newly_added_file_is_picked_up_without_an_explicit_refresh(bhav_dir):
    """A long-lived provider must not keep serving yesterday's cache after
    the user drops in today's bhavcopy. Requiring a manual refresh() makes
    silently-stale data the default, which is the wrong default here."""

    provider = BhavcopyProvider(bhav_dir)
    assert len(provider.get_bars("TCS")) == 2

    write_bhavcopy(
        bhav_dir,
        "BhavCopy_NSE_CM_0_0_0_20250106_F_0000.csv",
        [udiff_row("2025-01-06", "TCS", close=4100.0)],
    )

    assert len(provider.get_bars("TCS")) == 3
    assert len(provider.files_loaded()) == 3


def test_removing_a_file_is_also_detected(bhav_dir):
    provider = BhavcopyProvider(bhav_dir)
    assert len(provider.get_bars("TCS")) == 2

    (bhav_dir / "BhavCopy_NSE_CM_0_0_0_20250103_F_0000.csv").unlink()

    assert len(provider.get_bars("TCS")) == 1


def test_unchanged_directory_is_not_reparsed(bhav_dir, monkeypatch):
    """The stamp check must short-circuit, not re-read every file on every
    symbol lookup — a full NSE universe would make that pathological."""

    provider = BhavcopyProvider(bhav_dir)
    provider.get_bars("TCS")

    calls = []
    original = BhavcopyProvider._read_rows

    def counting(self, *args, **kwargs):
        calls.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(BhavcopyProvider, "_read_rows", counting)
    for _ in range(5):
        provider.get_bars("TCS")
        provider.get_liquidity("INFY")

    assert calls == []


def test_refresh_forces_reload_after_in_place_edit(bhav_dir):
    """Editing a file's contents leaves the directory stamp untouched, so
    this is the case refresh() still exists for."""

    provider = BhavcopyProvider(bhav_dir)
    assert provider.get_bars("TCS")[-1].close == 4040.0

    write_bhavcopy(
        bhav_dir,
        "BhavCopy_NSE_CM_0_0_0_20250103_F_0000.csv",
        [udiff_row("2025-01-03", "TCS", close=4444.0)],
    )
    provider.refresh()

    assert provider.get_bars("TCS")[-1].close == 4444.0
