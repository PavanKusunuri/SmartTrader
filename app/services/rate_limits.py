"""API rate-limit budgeting, shaped against the published Groww Trading API limits.

PAPER TRADING — NO REAL ORDERS. This module makes no network call, holds no
credential, and contains no Groww endpoint. It is a *budget calculator and
gate*: it answers "would this scan design fit inside the API limits, and may
this request go now?" so the scanner's shape is known to be feasible before
any broker adapter exists. CLAUDE.md section 16 forbids a live connection in
v1, and nothing here creates one.

Why this belongs in a paper-trading app:

- A watchlist and scan interval that quietly need 900 quote requests a minute
  are not a configuration detail, they are a design error. Better to find out
  from a calculation than from an HTTP 429 later.
- The limits are per CATEGORY, not per endpoint, so quotes, order calls and
  position lookups each draw on a shared budget. Counting them separately
  would understate usage.
- After an outage, a recovering scanner must not fire a burst. The gate here
  refuses the burst, and the scanner reports the refusal instead of trading
  on whatever data it managed to grab.

**Provenance of the numbers below — read before relying on them.** They come
from a web-search summary of the official Groww Trading API documentation
(groww.in/trade-api/docs) retrieved 2026-10-22. The documentation page was
NOT read directly in this session: that fetch was cancelled, and third-party
sources found alongside it disagreed with each other (one listed 15/second
for orders, an older article 100 orders/minute). So these are
**user-configurable assumptions, not verified-current official limits**, and
`GrowwApiLimits.verified` is False to say so in code as well as in prose.
Re-check the official docs and set your own figures before depending on them.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from pydantic import BaseModel, Field


class ApiCategory(str, Enum):
    """Limits apply per category: every endpoint in a category shares one budget."""

    AUTH = "AUTH"                # access-token generation
    ORDERS = "ORDERS"            # create / modify / cancel
    LIVE_DATA = "LIVE_DATA"      # quote, LTP, OHLC
    NON_TRADING = "NON_TRADING"  # order status/list, trades, positions, holdings, margin


class CategoryLimit(BaseModel):
    """Requests allowed in one category. `per_day` is None when no daily cap
    is documented — None means "not stated", never "unlimited"."""

    per_second: int = Field(gt=0)
    per_minute: int = Field(gt=0)
    per_day: int | None = Field(default=None, gt=0)


class GrowwApiLimits(BaseModel):
    """Published-but-unverified Groww Trading API limits. See module docstring.

    `verified` stays False until someone reads the official rate-limit page
    and confirms these figures. Code that reports these numbers to a user must
    surface that flag rather than presenting them as current fact.
    """

    auth: CategoryLimit = Field(
        default_factory=lambda: CategoryLimit(per_second=5, per_minute=30, per_day=150)
    )
    orders: CategoryLimit = Field(
        default_factory=lambda: CategoryLimit(per_second=10, per_minute=250)
    )
    live_data: CategoryLimit = Field(
        default_factory=lambda: CategoryLimit(per_second=10, per_minute=300)
    )
    non_trading: CategoryLimit = Field(
        default_factory=lambda: CategoryLimit(per_second=20, per_minute=500)
    )

    verified: bool = Field(default=False)
    source: str = Field(
        default=(
            "web-search summary of groww.in/trade-api/docs, retrieved 2026-10-22; "
            "official page not read directly; third-party sources disagreed"
        )
    )

    def for_category(self, category: ApiCategory) -> CategoryLimit:
        mapping = {
            ApiCategory.AUTH: self.auth,
            ApiCategory.ORDERS: self.orders,
            ApiCategory.LIVE_DATA: self.live_data,
            ApiCategory.NON_TRADING: self.non_trading,
        }
        try:
            return mapping[category]
        except KeyError:
            raise ValueError(f"unknown API category: {category}") from None

    @property
    def caveat(self) -> str:
        """One line for the UI/logs, so a displayed limit is never mistaken
        for a verified one."""

        if self.verified:
            return "limits verified against official documentation"
        return (
            "UNVERIFIED assumed limits — not read from official docs in this "
            f"session. Source: {self.source}"
        )


@dataclass
class RateLimitBudget:
    """Sliding-window request gate for one API category.

    `now` is passed in by the caller rather than read from the clock, for the
    same reason the scanner takes it: a decision about whether a request is
    allowed must be reproducible in a test, and a backfilled run must not
    believe it is live.

    This gate REFUSES rather than sleeps. A paper-trading scan that cannot
    fetch fresh data should report that and skip the symbol, not block a
    thread and then act on data that aged while it waited.
    """

    limit: CategoryLimit
    category: ApiCategory = ApiCategory.LIVE_DATA
    # Timestamps of granted requests, newest last.
    _granted: deque[datetime] = field(default_factory=deque, repr=False)
    _day_count: int = field(default=0, repr=False)
    _day: object = field(default=None, repr=False)

    def _prune(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=60)
        while self._granted and self._granted[0] <= cutoff:
            self._granted.popleft()

    def _count_within(self, now: datetime, seconds: float) -> int:
        cutoff = now - timedelta(seconds=seconds)
        return sum(1 for ts in self._granted if ts > cutoff)

    def check(self, now: datetime) -> tuple[bool, str]:
        """Returns (allowed, reason). Does not consume the request."""

        self._prune(now)

        if self.limit.per_day is not None:
            today = now.date()
            used = self._day_count if self._day == today else 0
            if used >= self.limit.per_day:
                return False, (
                    f"{self.category.value} daily limit reached "
                    f"({used}/{self.limit.per_day} requests today)"
                )

        in_second = self._count_within(now, 1.0)
        if in_second >= self.limit.per_second:
            return False, (
                f"{self.category.value} per-second limit reached "
                f"({in_second}/{self.limit.per_second} in the last second)"
            )

        in_minute = len(self._granted)
        if in_minute >= self.limit.per_minute:
            return False, (
                f"{self.category.value} per-minute limit reached "
                f"({in_minute}/{self.limit.per_minute} in the last minute)"
            )

        return True, ""

    def try_consume(self, now: datetime) -> tuple[bool, str]:
        """Consumes one request if the budget allows it. Returns (allowed, reason)."""

        allowed, reason = self.check(now)
        if not allowed:
            return False, reason

        self._granted.append(now)
        if self.limit.per_day is not None:
            today = now.date()
            if self._day != today:
                self._day = today
                self._day_count = 0
            self._day_count += 1
        return True, ""

    def retry_after_seconds(self, now: datetime) -> float | None:
        """How long until the next request would be allowed, or None if one is
        allowed now. Used to tell an operator when the scan can resume rather
        than leaving them guessing."""

        allowed, _ = self.check(now)
        if allowed:
            return None
        self._prune(now)
        if not self._granted:
            return None

        waits: list[float] = []
        if self._count_within(now, 1.0) >= self.limit.per_second:
            nth_newest = list(self._granted)[-self.limit.per_second]
            waits.append(max(0.0, 1.0 - (now - nth_newest).total_seconds()))
        if len(self._granted) >= self.limit.per_minute:
            waits.append(max(0.0, 60.0 - (now - self._granted[0]).total_seconds()))
        if not waits:
            # Only the daily cap is blocking; it clears at the next IST midnight.
            midnight = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            return (midnight - now).total_seconds()
        return max(waits)

    @property
    def used_in_last_minute(self) -> int:
        return len(self._granted)


@dataclass(frozen=True)
class ScanPlan:
    """Whether a watchlist/interval combination fits inside an API budget."""

    watchlist_size: int
    requests_per_symbol: int
    interval_seconds: float
    category: ApiCategory
    requests_per_scan: int
    requests_per_minute_required: int
    max_symbols_per_second: int
    max_scans_per_minute: float
    min_interval_seconds: float
    max_watchlist_size_at_interval: int
    fits: bool
    warnings: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "watchlist_size": self.watchlist_size,
            "requests_per_symbol": self.requests_per_symbol,
            "interval_seconds": self.interval_seconds,
            "category": self.category.value,
            "requests_per_scan": self.requests_per_scan,
            "requests_per_minute_required": self.requests_per_minute_required,
            "max_symbols_per_second": self.max_symbols_per_second,
            "max_scans_per_minute": self.max_scans_per_minute,
            "min_interval_seconds": self.min_interval_seconds,
            "max_watchlist_size_at_interval": self.max_watchlist_size_at_interval,
            "fits": self.fits,
            "warnings": list(self.warnings),
        }


def plan_scan(
    watchlist_size: int,
    interval_seconds: float,
    limits: GrowwApiLimits | None = None,
    requests_per_symbol: int = 1,
    category: ApiCategory = ApiCategory.LIVE_DATA,
) -> ScanPlan:
    """Works out whether scanning `watchlist_size` symbols every
    `interval_seconds` fits inside the category's limits, and what the
    ceilings are.

    This is the calculation that turns "add 200 symbols to the watchlist" from
    a guess into a number: at 1 request per symbol per scan on a 60-second
    interval, 200 symbols needs 200 requests/minute against a 300/minute
    live-data budget — it fits, but a 30-second interval does not.
    """

    limits = limits or GrowwApiLimits()
    limit = limits.for_category(category)

    if watchlist_size < 0:
        raise ValueError("watchlist_size must not be negative")
    if requests_per_symbol <= 0:
        raise ValueError("requests_per_symbol must be positive")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")

    requests_per_scan = watchlist_size * requests_per_symbol
    scans_per_minute = 60.0 / interval_seconds
    required_per_minute = int(round(requests_per_scan * scans_per_minute))

    max_scans_per_minute = limit.per_minute / requests_per_scan if requests_per_scan else float("inf")
    min_interval = (
        60.0 / max_scans_per_minute if requests_per_scan and max_scans_per_minute > 0 else 0.0
    )
    max_watchlist = (
        int((limit.per_minute / scans_per_minute) // requests_per_symbol)
        if scans_per_minute > 0
        else 0
    )

    warnings: list[str] = []
    fits = True

    if required_per_minute > limit.per_minute:
        fits = False
        warnings.append(
            f"{required_per_minute} {category.value} requests/minute required but the "
            f"assumed limit is {limit.per_minute}/minute: either scan no more often "
            f"than every {min_interval:.0f}s, or cut the watchlist to "
            f"{max_watchlist} symbols at this interval"
        )

    # A scan that fires all its requests at once also has to respect the
    # per-second ceiling, which a per-minute average hides completely.
    if requests_per_scan > limit.per_second:
        warnings.append(
            f"a scan issues {requests_per_scan} requests at once but only "
            f"{limit.per_second}/second are allowed: spread each scan over at least "
            f"{requests_per_scan / limit.per_second:.1f}s instead of bursting"
        )

    if limit.per_day is not None:
        # Indian equity session is roughly 6.25 hours (09:15-15:30 IST).
        session_minutes = 375
        per_day = int(required_per_minute * session_minutes)
        if per_day > limit.per_day:
            fits = False
            warnings.append(
                f"about {per_day} requests over a 6.25-hour session exceeds the assumed "
                f"daily cap of {limit.per_day}"
            )

    if not limits.verified:
        warnings.append(limits.caveat)

    return ScanPlan(
        watchlist_size=watchlist_size,
        requests_per_symbol=requests_per_symbol,
        interval_seconds=interval_seconds,
        category=category,
        requests_per_scan=requests_per_scan,
        requests_per_minute_required=required_per_minute,
        max_symbols_per_second=limit.per_second // requests_per_symbol,
        max_scans_per_minute=max_scans_per_minute,
        min_interval_seconds=min_interval,
        max_watchlist_size_at_interval=max_watchlist,
        fits=fits,
        warnings=tuple(warnings),
    )
