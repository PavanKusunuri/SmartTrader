"""Tests for the dashboard's market-data sources and their labelling.

Two real data sources now exist (CSV files, and NSE UDiFF bhavcopy files the
user downloads by hand) and they are different kinds of data. The tests here
pin down the labelling, because a dashboard that mislabels synthetic data as
real, or end-of-day data as a quote, is worse than one that shows nothing.
"""

import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import SOURCE_CAVEATS, _data_kind, create_app
from app.storage.sqlite import SqliteRepository

LOCAL = "http://127.0.0.1:8000"  # browsers send a loopback Host; so do the tests
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"

# A minimal UDiFF bhavcopy. Column names match the real 2024-07-08-onwards
# format that app/data/bhavcopy_provider.py parses; anything else is rejected
# by design rather than guessed at.
UDIFF_HEADER = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,"
    "XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,"
    "LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,"
    "ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,"
    "Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4"
)


def _udiff_row(date: str, symbol: str, o, h, l, c, vol) -> str:
    return (
        f"{date},{date},CM,NSE,STK,1234,INE001A01036,{symbol},EQ,,,,,"
        f"{symbol} LIMITED,{o},{h},{l},{c},{c},{c},,,0,0,{vol},"
        f"{float(vol) * float(c)},100,F1,1,,,,,"
    )


@pytest.fixture
def bhavcopy_dir(tmp_path) -> Path:
    """Two days of a real-format bhavcopy, one as .csv and one as .csv.zip, to
    cover both ways NSE serves the file."""
    directory = tmp_path / "bhavcopy"
    directory.mkdir()

    plain = directory / "BhavCopy_NSE_CM_0_0_0_20250102_F_0000.csv"
    plain.write_text(
        "\n".join([
            UDIFF_HEADER,
            _udiff_row("2025-01-02", "RELIND", 1200.0, 1225.0, 1195.0, 1220.0, 500000),
            _udiff_row("2025-01-02", "TCS", 4000.0, 4050.0, 3990.0, 4040.0, 200000),
        ]) + "\n",
        encoding="utf-8",
    )

    zipped = directory / "BhavCopy_NSE_CM_0_0_0_20250103_F_0000.csv.zip"
    inner = "\n".join([
        UDIFF_HEADER,
        _udiff_row("2025-01-03", "RELIND", 1220.0, 1240.0, 1215.0, 1235.0, 450000),
        _udiff_row("2025-01-03", "TCS", 4040.0, 4060.0, 4010.0, 4025.0, 180000),
    ]) + "\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("BhavCopy_NSE_CM_0_0_0_20250103_F_0000.csv", inner)
    zipped.write_bytes(buffer.getvalue())

    return directory


@pytest.fixture
def ledger(tmp_path) -> str:
    path = tmp_path / "ledger.db"
    SqliteRepository(path).close()
    return str(path)


@pytest.fixture
def client(ledger, bhavcopy_dir) -> TestClient:
    return TestClient(create_app(
        db_path=ledger, samples_dir=str(SAMPLES_DIR), bhavcopy_dir=str(bhavcopy_dir)
    ), base_url=LOCAL)


@pytest.fixture
def csv_only_client(ledger) -> TestClient:
    """No bhavcopy directory configured — the common case."""
    return TestClient(create_app(db_path=ledger, samples_dir=str(SAMPLES_DIR)), base_url=LOCAL)


# --- labelling ---------------------------------------------------------------


def test_data_kind_distinguishes_the_three_real_kinds():
    assert _data_kind("csv:SAMPLE_TCS.csv") == "SAMPLE_SYNTHETIC"
    assert _data_kind(None, "SAMPLE_TCS.csv") == "SAMPLE_SYNTHETIC"
    assert _data_kind("csv:TCS.csv") == "HISTORICAL_CSV"
    assert _data_kind("nse-udiff-bhavcopy:BhavCopy_NSE_CM_0_0_0_20250102_F_0000.csv") == "NSE_BHAVCOPY_EOD"


def test_real_bhavcopy_data_is_never_labelled_synthetic_or_plain_csv():
    """Both other labels would be wrong: this is real exchange-published data,
    not generated, and not merely 'a CSV the user supplied'."""
    kind = _data_kind("nse-udiff-bhavcopy:BhavCopy_20250102.csv")
    assert kind == "NSE_BHAVCOPY_EOD"
    assert kind not in ("SAMPLE_SYNTHETIC", "HISTORICAL_CSV")


def test_no_data_kind_can_ever_claim_live_or_delayed():
    for source in (
        "csv:SAMPLE_TCS.csv", "csv:TCS.csv", "nse-udiff-bhavcopy:x.csv",
        None, "", "something-unexpected",
    ):
        kind = _data_kind(source)
        assert "LIVE" not in kind.upper()
        assert "DELAYED" not in kind.upper()
        assert "REALTIME" not in kind.upper().replace("_", "")


# --- sources listing ---------------------------------------------------------


def test_sources_lists_both_and_marks_network_free(client):
    body = client.get("/api/marketdata/sources").json()
    keys = {s["key"]: s for s in body["sources"]}

    assert set(keys) == {"csv", "bhavcopy"}
    assert body["streaming"] is False
    assert body["network_calls"] is False
    assert keys["bhavcopy"]["configured"] is True
    assert keys["csv"]["configured"] is True


def test_unconfigured_bhavcopy_is_reported_with_instructions(csv_only_client):
    body = csv_only_client.get("/api/marketdata/sources").json()
    bhav = {s["key"]: s for s in body["sources"]}["bhavcopy"]

    assert bhav["configured"] is False
    assert bhav["directory"] is None
    assert "--bhavcopy-dir" in bhav["how_to_enable"]


def test_bhavcopy_caveats_state_what_the_data_is_not(client):
    body = client.get("/api/marketdata/sources").json()
    caveats = " ".join({s["key"]: s for s in body["sources"]}["bhavcopy"]["caveats"]).lower()

    # The three things a dashboard would normally hide.
    assert "unadjusted" in caveats          # corporate actions
    assert "end-of-day" in caveats          # no intraday, no quote
    assert "never fetches" in caveats       # not scraped


def test_requesting_bhavcopy_when_unconfigured_is_404_not_500(csv_only_client):
    response = csv_only_client.get("/api/marketdata/bars/RELIND?source=bhavcopy")
    assert response.status_code == 404
    assert "--bhavcopy-dir" in response.json()["detail"]


def test_unknown_source_is_rejected(client):
    response = client.get("/api/marketdata/bars/RELIND?source=yfinance")
    assert response.status_code == 400
    assert "unknown data source" in response.json()["detail"]


# --- watchlist ---------------------------------------------------------------


def test_csv_watchlist_labels_bundled_samples_as_synthetic(client):
    rows = client.get("/api/marketdata/watchlist?source=csv").json()["rows"]
    assert rows
    assert all(r["data_kind"] == "SAMPLE_SYNTHETIC" for r in rows)


def test_bhavcopy_watchlist_pivots_files_into_symbols(client):
    body = client.get("/api/marketdata/watchlist?source=bhavcopy").json()
    symbols = {r["symbol"] for r in body["rows"]}

    assert symbols == {"RELIND", "TCS"}
    assert all(r["data_kind"] == "NSE_BHAVCOPY_EOD" for r in body["rows"])
    # Both the plain .csv and the .csv.zip must have been read.
    assert len(body["files_loaded"]) == 2
    # A bhavcopy is one file per DATE, so there is no per-symbol file.
    assert all(r["file"] is None for r in body["rows"])


def test_bhavcopy_watchlist_reports_a_bad_directory_without_a_500(ledger, tmp_path):
    empty = tmp_path / "nothing-here"
    empty.mkdir()
    bad_client = TestClient(create_app(
        db_path=ledger, samples_dir=str(SAMPLES_DIR), bhavcopy_dir=str(empty)
    ), base_url=LOCAL)
    body = bad_client.get("/api/marketdata/watchlist?source=bhavcopy").json()

    assert body["rows"] == []
    assert "error" in body
    assert "download" in body["error"].lower()


# --- bars --------------------------------------------------------------------


def test_bhavcopy_bars_are_served_and_labelled(client):
    body = client.get("/api/marketdata/bars/RELIND?source=bhavcopy").json()

    assert body["provider_status"] == "OK"
    assert body["data_kind"] == "NSE_BHAVCOPY_EOD"
    assert body["provider"] == "BhavcopyProvider"
    assert body["bar_count"] == 2
    assert body["bar_source"].startswith("nse-udiff-bhavcopy")
    assert body["latest_bar"]["close"] == 1235.0


def test_bhavcopy_reports_no_quote_available(client):
    """A bhavcopy is published after the close. Presenting its last row as a
    quote would misrepresent end-of-day data as live."""
    body = client.get("/api/marketdata/bars/RELIND?source=bhavcopy").json()
    assert body["quote_available"] is False


def test_bhavcopy_rejects_an_intraday_interval_with_an_explanation(client):
    body = client.get("/api/marketdata/bars/RELIND?source=bhavcopy&interval=5m").json()
    assert body["provider_status"] == "ERROR"
    assert "end-of-day" in body["error"].lower()


def test_unknown_symbol_on_bhavcopy_reports_empty_not_error(client):
    body = client.get("/api/marketdata/bars/NOSUCHSYMBOL?source=bhavcopy").json()
    assert body["provider_status"] == "EMPTY"
    assert body["rows"] == []


def test_bars_default_to_the_csv_source(client):
    body = client.get("/api/marketdata/bars/SAMPLE_TCS").json()
    assert body["source"] == "csv"
    assert body["data_kind"] == "SAMPLE_SYNTHETIC"


def test_bars_carry_their_source_caveats(client):
    body = client.get("/api/marketdata/bars/RELIND?source=bhavcopy").json()
    assert body["caveats"] == SOURCE_CAVEATS["bhavcopy"]


# --- claims ------------------------------------------------------------------


def test_status_reports_network_free_and_separates_the_order_guarantee(client):
    body = client.get("/api/status").json()
    provider = body["data_provider"]

    assert provider["network_calls"] is False
    assert provider["streaming"] is False
    assert provider["bhavcopy_configured"] is True
    # The order guarantee is a separate, unconditional claim.
    assert body["live_orders_possible"] is False


def test_no_endpoint_describes_any_source_as_live_or_streaming(client):
    for path in (
        "/api/status", "/api/marketdata/sources",
        "/api/marketdata/watchlist?source=csv",
        "/api/marketdata/watchlist?source=bhavcopy",
        "/api/marketdata/bars/RELIND?source=bhavcopy",
        "/api/marketdata/bars/SAMPLE_TCS?source=csv",
    ):
        text = client.get(path).text.lower()
        # "not a live feed" / "no network call" are fine; a positive claim is not.
        for bad in ("real-time quote", "live feed from", "streaming quote", "live data feed from"):
            assert bad not in text, f"{path} contains {bad!r}"


def test_footer_separates_the_order_guarantee_from_the_network_claim():
    page = (Path("app/api/static/index.html")).read_text(encoding="utf-8")
    assert "No real orders, ever" in page
    assert "regardless of how it is configured" in page
    assert "make no network call" in page
