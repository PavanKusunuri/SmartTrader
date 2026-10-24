"""GrowwProvider against a fake client: windowing, parsing, validation, failures."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.data.groww_client import GrowwDataError
from app.data.groww_provider import GrowwProvider
from app.models import DataQuality

IST = ZoneInfo("Asia/Kolkata")


class FakeClient:
    def __init__(self, candles_by_call=None, quote=None, raises=None):
        self.calls = []
        self._candles = candles_by_call or (lambda **kw: [])
        self._quote = quote
        self._raises = raises

    def historical_candles(self, **kw):
        self.calls.append(kw)
        if self._raises:
            raise self._raises
        return {"candles": self._candles(**kw)}

    def quote(self, **kw):
        self.calls.append(kw)
        return self._quote


def row(ts, o=100.0, h=101.0, l=99.0, c=100.5, v=1000):
    return [ts, o, h, l, c, v, None]


def test_long_range_is_split_into_documented_windows():
    fake = FakeClient()
    GrowwProvider(fake).get_bars("RELIANCE", "5m", start="2025-01-01", end="2025-03-12")
    assert len(fake.calls) == 3                      # 70 days / 29-day windows
    assert all(c["candle_interval"] == "5minute" and c["groww_symbol"] == "NSE-RELIANCE" for c in fake.calls)
    for c in fake.calls:
        s = datetime.strptime(c["start_time"], "%Y-%m-%d %H:%M:%S")
        e = datetime.strptime(c["end_time"], "%Y-%m-%d %H:%M:%S")
        assert e - s <= timedelta(days=30)           # never beyond Groww's documented 30-day cap


def test_daily_windows_stay_under_180_days():
    fake = FakeClient()
    GrowwProvider(fake).get_bars("TCS", "1d", start="2024-01-01", end="2025-01-01")
    for c in fake.calls:
        s = datetime.strptime(c["start_time"], "%Y-%m-%d %H:%M:%S")
        e = datetime.strptime(c["end_time"], "%Y-%m-%d %H:%M:%S")
        assert e - s <= timedelta(days=180)


def test_rows_parsed_sorted_deduped_and_tagged_ist_with_provenance():
    rows = [row("2025-01-02T09:20:00"), row("2025-01-02T09:15:00"), row("2025-01-02 09:20:00", c=100.7)]
    bars = GrowwProvider(FakeClient(lambda **kw: rows)).get_bars("INFY", "5m", start="2025-01-02", end="2025-01-03")
    assert [b.timestamp.strftime("%H:%M") for b in bars] == ["09:15", "09:20"]
    assert bars[1].close == 100.7                    # later duplicate wins, no double bar
    assert bars[0].timestamp.tzinfo is not None and bars[0].timestamp.utcoffset() == timedelta(hours=5, minutes=30)
    assert "groww:candles" in bars[0].source and "tz-assumed-IST" in bars[0].source
    assert bars[0].interval == "5m" and bars[0].retrieved_at.tzinfo is not None


def test_epoch_seconds_and_millis_timestamps():
    epoch = int(datetime(2025, 1, 2, 9, 15, tzinfo=IST).timestamp())
    bars = GrowwProvider(FakeClient(lambda **kw: [row(epoch), row((epoch + 300) * 1000)])).get_bars(
        "INFY", "5m", start="2025-01-02", end="2025-01-03")
    assert [b.timestamp.strftime("%H:%M") for b in bars] == ["09:15", "09:20"]


def test_bad_ohlc_is_flagged_not_silently_accepted():
    bars = GrowwProvider(FakeClient(lambda **kw: [row("2025-01-02T09:15:00", h=90.0)])).get_bars(
        "INFY", "5m", start="2025-01-02", end="2025-01-03")
    assert bars[0].quality is DataQuality.INVALID


def test_malformed_row_raises_instead_of_producing_a_bar():
    with pytest.raises(GrowwDataError, match="malformed"):
        GrowwProvider(FakeClient(lambda **kw: [["2025-01-02T09:15:00", "x", 1, 1, 1, 1, None]])).get_bars(
            "INFY", "5m", start="2025-01-02", end="2025-01-03")


def test_empty_response_returns_no_bars_not_an_error():
    assert GrowwProvider(FakeClient()).get_bars("INFY", "5m", start="2025-01-02", end="2025-01-03") == []


def test_provider_error_propagates_and_never_becomes_data():
    with pytest.raises(GrowwDataError):
        GrowwProvider(FakeClient(raises=GrowwDataError("down"))).get_bars("INFY", "1d", start="2025-01-02", end="2025-01-09")


@pytest.mark.parametrize("bad", ["", "../etc", "A B", "RELIANCE&x=1;", "X" * 30, "a/b"])
def test_symbol_injection_rejected_before_any_call(bad):
    fake = FakeClient()
    with pytest.raises(ValueError):
        GrowwProvider(fake).get_bars(bad, "1d")
    assert fake.calls == []


def test_unsupported_interval_and_segment():
    with pytest.raises(ValueError):
        GrowwProvider(FakeClient()).get_bars("INFY", "7m")
    with pytest.raises(ValueError):
        GrowwProvider(FakeClient(), segment="FNO")


def test_start_must_precede_end():
    with pytest.raises(ValueError):
        GrowwProvider(FakeClient()).get_bars("INFY", "1d", start="2025-02-01", end="2025-01-01")


def test_future_end_is_clamped_to_now():
    fake = FakeClient()
    GrowwProvider(fake).get_bars("INFY", "1d", start="2020-01-01", end="2999-01-01")
    assert max(datetime.strptime(c["end_time"], "%Y-%m-%d %H:%M:%S") for c in fake.calls) <= datetime.now() + timedelta(days=1)


# ---------------------------------------------------------------------- quote

def test_quote_is_labelled_a_snapshot_not_streaming():
    ms = int(datetime(2025, 1, 2, 10, 0, tzinfo=IST).timestamp() * 1000)
    q = {"ohlc": {"open": 100, "high": 105, "low": 99, "close": 101}, "last_price": 103.5,
         "last_trade_time": ms, "volume": 5000}
    bar = GrowwProvider(FakeClient(quote=q)).get_latest_quote("INFY")
    assert bar.close == 103.5 and bar.interval == "quote"
    assert "not-streaming" in bar.source and "snapshot" in bar.source


@pytest.mark.parametrize("q", [{}, {"ohlc": {}, "last_price": 1}, {"ohlc": {"open": 1, "high": 1, "low": 1, "close": 1},
                                                                    "last_price": 1}])
def test_malformed_quote_raises_not_none(q):
    with pytest.raises(GrowwDataError):
        GrowwProvider(FakeClient(quote=q)).get_latest_quote("INFY")
