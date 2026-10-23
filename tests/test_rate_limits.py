"""API rate-limit budget tests.

These test the budgeting arithmetic and the gate's behaviour. They assert
nothing about what Groww's real limits are — those are unverified assumptions
(see app/services/rate_limits.py), so every test sets the limits it needs.

No test here opens a network connection; the module under test cannot.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.rate_limits import (
    ApiCategory,
    CategoryLimit,
    GrowwApiLimits,
    RateLimitBudget,
    plan_scan,
)

IST = ZoneInfo("Asia/Kolkata")
T0 = datetime(2024, 1, 10, 10, 0, 0, tzinfo=IST)


def budget(per_second=2, per_minute=10, per_day=None) -> RateLimitBudget:
    return RateLimitBudget(
        limit=CategoryLimit(per_second=per_second, per_minute=per_minute, per_day=per_day),
        category=ApiCategory.LIVE_DATA,
    )


# --- the gate ---------------------------------------------------------------


def test_requests_within_budget_are_allowed():
    b = budget()
    assert b.try_consume(T0)[0] is True
    assert b.try_consume(T0)[0] is True


def test_per_second_limit_refuses_a_burst():
    b = budget(per_second=2, per_minute=100)
    b.try_consume(T0)
    b.try_consume(T0)
    allowed, reason = b.try_consume(T0)
    assert allowed is False
    assert "per-second limit" in reason


def test_budget_recovers_after_a_second_passes():
    b = budget(per_second=2, per_minute=100)
    b.try_consume(T0)
    b.try_consume(T0)
    assert b.try_consume(T0)[0] is False
    assert b.try_consume(T0 + timedelta(seconds=1.1))[0] is True


def test_per_minute_limit_refuses_even_when_spread_out():
    b = budget(per_second=10, per_minute=5)
    for i in range(5):
        assert b.try_consume(T0 + timedelta(seconds=i * 2))[0] is True
    allowed, reason = b.try_consume(T0 + timedelta(seconds=11))
    assert allowed is False
    assert "per-minute limit" in reason


def test_minute_window_slides():
    b = budget(per_second=10, per_minute=5)
    for i in range(5):
        b.try_consume(T0 + timedelta(seconds=i))
    # Just over a minute after the first request, it has left the window.
    assert b.try_consume(T0 + timedelta(seconds=61))[0] is True


def test_daily_cap_is_enforced():
    b = budget(per_second=10, per_minute=100, per_day=3)
    for i in range(3):
        assert b.try_consume(T0 + timedelta(seconds=i * 10))[0] is True
    allowed, reason = b.try_consume(T0 + timedelta(seconds=100))
    assert allowed is False
    assert "daily limit" in reason


def test_daily_cap_resets_on_the_next_day():
    b = budget(per_second=10, per_minute=100, per_day=2)
    b.try_consume(T0)
    b.try_consume(T0 + timedelta(seconds=5))
    assert b.try_consume(T0 + timedelta(seconds=10))[0] is False
    assert b.try_consume(T0 + timedelta(days=1))[0] is True


def test_refused_request_is_not_counted_against_the_budget():
    b = budget(per_second=1, per_minute=100)
    b.try_consume(T0)
    b.try_consume(T0)  # refused
    b.try_consume(T0)  # refused
    # Only the granted one occupies the window, so the minute budget is intact.
    assert b.used_in_last_minute == 1


def test_check_does_not_consume():
    b = budget(per_second=1, per_minute=100)
    assert b.check(T0)[0] is True
    assert b.check(T0)[0] is True
    assert b.used_in_last_minute == 0


def test_retry_after_is_none_when_a_request_is_allowed():
    assert budget().retry_after_seconds(T0) is None


def test_retry_after_reports_the_wait_for_a_per_second_block():
    b = budget(per_second=2, per_minute=100)
    b.try_consume(T0)
    b.try_consume(T0 + timedelta(seconds=0.4))
    wait = b.retry_after_seconds(T0 + timedelta(seconds=0.5))
    assert wait is not None
    # Requests sit at T0 and T0+0.4; now is T0+0.5. A slot frees when the
    # OLDER one leaves the 1s window, at T0+1.0 — i.e. 0.5s from now.
    assert wait == pytest.approx(0.5, abs=0.01)
    # And at that point a request really is allowed again.
    assert b.check(T0 + timedelta(seconds=0.5) + timedelta(seconds=wait))[0] is True


def test_retry_after_reports_the_wait_for_a_per_minute_block():
    b = budget(per_second=10, per_minute=2)
    b.try_consume(T0)
    b.try_consume(T0 + timedelta(seconds=1))
    wait = b.retry_after_seconds(T0 + timedelta(seconds=2))
    assert wait is not None
    assert 55 < wait <= 60


# --- published limits are labelled unverified -------------------------------


def test_default_limits_are_marked_unverified():
    limits = GrowwApiLimits()
    assert limits.verified is False
    assert "UNVERIFIED" in limits.caveat
    assert "groww.in/trade-api/docs" in limits.source


def test_every_category_has_a_limit():
    limits = GrowwApiLimits()
    for category in ApiCategory:
        limit = limits.for_category(category)
        assert limit.per_second > 0 and limit.per_minute > 0


def test_unknown_category_is_rejected():
    with pytest.raises(ValueError):
        GrowwApiLimits().for_category("MARKET_DEPTH")


def test_auth_category_carries_a_daily_cap():
    # Token generation is the one category documented with a 24-hour cap.
    assert GrowwApiLimits().auth.per_day is not None


# --- scan planning ----------------------------------------------------------


def test_small_watchlist_on_a_slow_interval_fits():
    plan = plan_scan(watchlist_size=10, interval_seconds=60.0)
    assert plan.fits is True
    assert plan.requests_per_minute_required == 10


def test_large_watchlist_on_a_fast_interval_does_not_fit_and_says_why():
    plan = plan_scan(watchlist_size=200, interval_seconds=30.0)
    assert plan.fits is False
    assert plan.requests_per_minute_required == 400
    joined = " ".join(plan.warnings)
    assert "requests/minute required" in joined
    # The advice is actionable: both a safe interval and a safe size.
    assert plan.min_interval_seconds > 30.0
    assert 0 < plan.max_watchlist_size_at_interval < 200


def test_burst_warning_fires_even_when_the_per_minute_average_fits():
    # 200 requests once a minute averages fine but bursts past 10/second.
    plan = plan_scan(watchlist_size=200, interval_seconds=60.0)
    assert plan.fits is True
    assert any("at once" in w for w in plan.warnings)


def test_multiple_requests_per_symbol_multiply_the_cost():
    one = plan_scan(watchlist_size=50, interval_seconds=60.0, requests_per_symbol=1)
    two = plan_scan(watchlist_size=50, interval_seconds=60.0, requests_per_symbol=2)
    assert two.requests_per_minute_required == 2 * one.requests_per_minute_required
    assert two.max_watchlist_size_at_interval < one.max_watchlist_size_at_interval


def test_plan_always_carries_the_unverified_caveat():
    plan = plan_scan(watchlist_size=5, interval_seconds=60.0)
    assert any("UNVERIFIED" in w for w in plan.warnings)


def test_plan_uses_the_category_the_caller_names():
    orders = plan_scan(watchlist_size=100, interval_seconds=60.0, category=ApiCategory.ORDERS)
    data = plan_scan(watchlist_size=100, interval_seconds=60.0, category=ApiCategory.LIVE_DATA)
    assert orders.category == ApiCategory.ORDERS
    # Orders allow 250/minute vs live data's 300, so the ceilings differ.
    assert orders.max_watchlist_size_at_interval != data.max_watchlist_size_at_interval


def test_auth_daily_cap_blocks_an_absurdly_frequent_token_refresh():
    plan = plan_scan(
        watchlist_size=1, interval_seconds=60.0, category=ApiCategory.AUTH,
    )
    # One token per minute over a 6.25-hour session is 375 > the 150/day cap.
    assert plan.fits is False
    assert any("daily cap" in w for w in plan.warnings)


def test_plan_rejects_nonsense_inputs():
    with pytest.raises(ValueError):
        plan_scan(watchlist_size=-1, interval_seconds=60.0)
    with pytest.raises(ValueError):
        plan_scan(watchlist_size=10, interval_seconds=0)
    with pytest.raises(ValueError):
        plan_scan(watchlist_size=10, interval_seconds=60.0, requests_per_symbol=0)


def test_plan_is_json_serialisable_for_the_dashboard():
    plan = plan_scan(watchlist_size=10, interval_seconds=60.0)
    data = plan.as_dict()
    assert data["fits"] is True
    assert isinstance(data["warnings"], list)
