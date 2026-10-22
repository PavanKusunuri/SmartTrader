from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.market_calendar import MarketSession, load_holidays

IST = ZoneInfo("Asia/Kolkata")

# 2024-01-02 is a Tuesday; 2024-01-06/07 are Sat/Sun.
TUESDAY = date(2024, 1, 2)
SATURDAY = date(2024, 1, 6)
SUNDAY = date(2024, 1, 7)


def at(day: date, hh: int, mm: int) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)


def test_weekends_are_never_trading_days():
    session = MarketSession()
    assert session.is_trading_day(TUESDAY) is True
    assert session.is_trading_day(SATURDAY) is False
    assert session.is_trading_day(SUNDAY) is False


def test_holiday_list_is_empty_by_default():
    """CLAUDE.md forbids shipping an unverified hardcoded holiday list."""
    assert MarketSession().holidays == frozenset()


def test_user_supplied_holiday_is_not_a_trading_day():
    session = MarketSession(holidays=frozenset({TUESDAY}))
    assert session.is_trading_day(TUESDAY) is False


def test_in_session_only_within_open_close_on_a_trading_day():
    session = MarketSession()
    assert session.is_in_session(at(TUESDAY, 9, 15)) is True   # exactly open
    assert session.is_in_session(at(TUESDAY, 12, 0)) is True
    assert session.is_in_session(at(TUESDAY, 15, 30)) is True   # exactly close
    assert session.is_in_session(at(TUESDAY, 9, 0)) is False    # pre-open
    assert session.is_in_session(at(TUESDAY, 15, 45)) is False  # post-close
    assert session.is_in_session(at(SATURDAY, 12, 0)) is False  # weekend


def test_can_enter_respects_entry_cutoff():
    session = MarketSession(entry_cutoff=time(14, 30))
    assert session.can_enter(at(TUESDAY, 10, 0)) is True
    assert session.can_enter(at(TUESDAY, 14, 29)) is True
    assert session.can_enter(at(TUESDAY, 14, 30)) is False  # cutoff is exclusive
    assert session.can_enter(at(TUESDAY, 15, 0)) is False


def test_can_enter_is_false_on_holidays_and_weekends():
    session = MarketSession(holidays=frozenset({TUESDAY}))
    assert session.can_enter(at(TUESDAY, 10, 0)) is False
    assert session.can_enter(at(SATURDAY, 10, 0)) is False


def test_must_flatten_at_and_after_flatten_time():
    session = MarketSession(flatten_time=time(15, 15))
    assert session.must_flatten(at(TUESDAY, 15, 14)) is False
    assert session.must_flatten(at(TUESDAY, 15, 15)) is True  # inclusive
    assert session.must_flatten(at(TUESDAY, 15, 25)) is True


def test_invalid_session_times_are_rejected_at_construction():
    with pytest.raises(ValueError):
        MarketSession(open_time=time(15, 30), close_time=time(9, 15))
    with pytest.raises(ValueError):
        MarketSession(entry_cutoff=time(8, 0))  # before the open
    with pytest.raises(ValueError):
        MarketSession(flatten_time=time(16, 0))  # after the close
    with pytest.raises(ValueError):
        # Entering after the forced-flatten time would be incoherent.
        MarketSession(entry_cutoff=time(15, 20), flatten_time=time(15, 15))


def test_load_holidays_reads_dates(tmp_path):
    path = tmp_path / "holidays.csv"
    path.write_text("date\n2024-01-26\n2024-03-25\n", encoding="utf-8")
    holidays = load_holidays(path)
    assert holidays == frozenset({date(2024, 1, 26), date(2024, 3, 25)})


def test_load_holidays_skips_blank_rows(tmp_path):
    path = tmp_path / "holidays.csv"
    path.write_text("date\n2024-01-26\n\n", encoding="utf-8")
    assert load_holidays(path) == frozenset({date(2024, 1, 26)})


def test_load_holidays_errors_loudly_on_missing_file(tmp_path):
    """A silently empty holiday set would be worse than a clear failure."""
    with pytest.raises(FileNotFoundError):
        load_holidays(tmp_path / "nope.csv")


def test_load_holidays_rejects_missing_column_and_bad_dates(tmp_path):
    no_col = tmp_path / "a.csv"
    no_col.write_text("day\n2024-01-26\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required 'date' column"):
        load_holidays(no_col)

    bad = tmp_path / "b.csv"
    bad.write_text("date\n26-01-2024\n", encoding="utf-8")
    with pytest.raises(ValueError, match="bad date"):
        load_holidays(bad)
