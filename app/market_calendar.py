"""Market session and trading-calendar rules for Indian cash equities (IST).

IMPORTANT — the session times below are DEFAULT ASSUMPTIONS, configurable by
the user. They reflect the commonly published NSE/BSE regular equity session
(09:15–15:30 IST) but have NOT been verified against a current exchange
circular in this project. Verify before relying on them.

The holiday list is intentionally EMPTY by default. CLAUDE.md forbids
hardcoding an unverified holiday list, so holidays must be supplied by the
user (see `load_holidays`). With no holiday file, only weekends are treated
as non-trading days, and the engine will happily process any dated bar your
data contains — it cannot tell you that a date was actually a holiday.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class MarketSession:
    """Configurable intraday session boundaries.

    - `open_time`/`close_time`: regular session bounds.
    - `entry_cutoff`: no NEW entries at or after this time, so the strategy
      is not opening fresh intraday risk minutes before the close.
    - `flatten_time`: any open intraday position is force-closed at or after
      this time, implementing "no overnight positions by default".
    """

    open_time: time = time(9, 15)
    close_time: time = time(15, 30)
    entry_cutoff: time = time(14, 30)
    flatten_time: time = time(15, 15)
    holidays: frozenset[date] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.open_time < self.close_time:
            raise ValueError("open_time must be before close_time")
        if not self.open_time < self.entry_cutoff <= self.close_time:
            raise ValueError("entry_cutoff must fall inside the session")
        if not self.open_time < self.flatten_time <= self.close_time:
            raise ValueError("flatten_time must fall inside the session")
        if self.entry_cutoff > self.flatten_time:
            raise ValueError("entry_cutoff must not be after flatten_time")

    def is_trading_day(self, day: date) -> bool:
        """Weekends are never trading days. Beyond that, this is only as
        accurate as the holiday set the user supplied."""
        if day.weekday() >= 5:
            return False
        return day not in self.holidays

    def is_in_session(self, ts: datetime) -> bool:
        if not self.is_trading_day(ts.date()):
            return False
        return self.open_time <= ts.timetz().replace(tzinfo=None) <= self.close_time

    def can_enter(self, ts: datetime) -> bool:
        """True only if a new intraday entry is permitted at this timestamp."""
        if not self.is_in_session(ts):
            return False
        return ts.time() < self.entry_cutoff

    def must_flatten(self, ts: datetime) -> bool:
        """True once open intraday positions should be force-closed."""
        return ts.time() >= self.flatten_time


def load_holidays(path: str | Path) -> frozenset[date]:
    """Loads user-supplied market holidays from a CSV with a `date` column
    (YYYY-MM-DD). Raises if the file is missing — a silently empty holiday
    set would be worse than a clear error."""

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"holiday file not found: {path}")

    holidays: set[date] = set()
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "date" not in [h.strip().lower() for h in reader.fieldnames]:
            raise ValueError(f"{path.name}: missing required 'date' column")
        for row_num, row in enumerate(reader, start=2):
            row = {k.strip().lower(): v for k, v in row.items()}
            raw = (row.get("date") or "").strip()
            if not raw:
                continue
            try:
                holidays.add(datetime.strptime(raw, "%Y-%m-%d").date())
            except ValueError as exc:
                raise ValueError(f"{path.name}:{row_num}: bad date '{raw}'") from exc
    return frozenset(holidays)
