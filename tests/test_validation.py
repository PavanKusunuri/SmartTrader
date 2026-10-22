from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.data.validation import ValidationIssue, validate_series
from app.models import Bar, DataQuality

IST = ZoneInfo("Asia/Kolkata")


def make_bar(day: int, close: float, **overrides) -> Bar:
    defaults = dict(
        symbol="TEST",
        timestamp=datetime(2024, 1, day, tzinfo=IST),
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=1000,
        source="unit-test",
        retrieved_at=datetime(2024, 1, day, tzinfo=IST),
        interval="1d",
    )
    defaults.update(overrides)
    return Bar(**defaults)


def test_clean_series_is_all_ok():
    bars = [make_bar(1, 100), make_bar(2, 101), make_bar(3, 102)]
    result = validate_series(bars)
    assert all(b.quality == DataQuality.OK for b in result)


def test_naive_timestamp_is_invalid():
    bar = make_bar(1, 100, timestamp=datetime(2024, 1, 1))  # no tzinfo
    result = validate_series([bar])
    assert result[0].quality == DataQuality.INVALID
    assert any("timezone" in n for n in result[0].quality_notes)


def test_zero_price_is_invalid():
    bar = make_bar(1, 0, open=0, high=0, low=0)
    result = validate_series([bar])
    assert result[0].quality == DataQuality.INVALID


def test_high_less_than_low_is_invalid():
    bar = make_bar(1, 100, high=90, low=95)
    result = validate_series([bar])
    assert result[0].quality == DataQuality.INVALID


def test_abnormal_gap_is_flagged():
    bars = [make_bar(1, 100), make_bar(2, 200, open=200, high=201, low=199)]
    result = validate_series(bars)
    assert result[0].quality == DataQuality.OK
    assert result[1].quality == DataQuality.SUSPECT_GAP


def test_stale_bar_is_flagged_relative_to_now():
    bar = make_bar(1, 100)
    now = datetime(2024, 2, 1, tzinfo=IST)  # far after Jan 1
    result = validate_series([bar], now=now)
    assert result[0].quality == DataQuality.STALE


def test_duplicate_timestamp_raises():
    bars = [make_bar(1, 100), make_bar(1, 101)]
    with pytest.raises(ValidationIssue):
        validate_series(bars)


def test_out_of_order_raises():
    bars = [make_bar(2, 100), make_bar(1, 101)]
    with pytest.raises(ValidationIssue):
        validate_series(bars)


def test_empty_series_returns_empty():
    assert validate_series([]) == []
