"""OHLCV validation.

Flags problems rather than silently fixing them: a strategy must never
receive bars that look fine but aren't. Anything invalid or stale must
result in NO_TRADE further up the stack, not a crash and not a guess.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from app.models import Bar, DataQuality

# Single-bar move beyond this fraction is flagged as a suspect gap, not
# silently trusted. This threshold is a configurable, documented assumption,
# not a verified market fact.
ABNORMAL_GAP_FRACTION = 0.20

# A bar older than this relative to "now" is considered stale for decision
# purposes. Overridable by callers; this is the Milestone-1 default.
DEFAULT_STALE_AFTER = timedelta(days=5)


class ValidationIssue(Exception):
    """Raised for structural problems that make a bar unusable (not just suspect)."""


def validate_bar_fields(bar: Bar) -> list[str]:
    """Return a list of problems with a single bar's own fields (not
    cross-bar checks like gaps/duplicates). Empty list means no problems."""

    problems: list[str] = []

    if bar.timestamp.tzinfo is None:
        problems.append("timestamp is not timezone-aware (must be Asia/Kolkata)")

    if bar.open <= 0 or bar.high <= 0 or bar.low <= 0 or bar.close <= 0:
        problems.append("zero or negative price")

    if bar.volume < 0:
        problems.append("negative volume")

    if bar.high < bar.low:
        problems.append("high < low")

    if not (bar.low <= bar.open <= bar.high):
        problems.append("open outside [low, high]")

    if not (bar.low <= bar.close <= bar.high):
        problems.append("close outside [low, high]")

    return problems


def validate_series(bars: list[Bar], now: datetime | None = None) -> list[Bar]:
    """Validate a time-ordered series of bars for one symbol.

    Returns a new list of Bars with `quality`/`quality_notes` set. Does not
    drop bars with field-level problems — it marks them INVALID so callers
    can decide to skip them, but a caller that ignores quality entirely would
    still see the problem instead of a wrong number.

    Raises ValidationIssue if the series is out of timestamp order or
    contains duplicate timestamps — those are structural bugs in the input,
    not data-quality judgment calls.
    """

    if not bars:
        return []

    for prev, curr in zip(bars, bars[1:]):
        if curr.timestamp < prev.timestamp:
            raise ValidationIssue(
                f"{curr.symbol}: bars out of order ({curr.timestamp} before {prev.timestamp})"
            )
        if curr.timestamp == prev.timestamp:
            raise ValidationIssue(f"{curr.symbol}: duplicate timestamp {curr.timestamp}")

    validated: list[Bar] = []
    prev_close: float | None = None

    for bar in bars:
        field_problems = validate_bar_fields(bar)
        notes: list[str] = list(field_problems)
        quality = DataQuality.INVALID if field_problems else DataQuality.OK

        if quality == DataQuality.OK and prev_close is not None and prev_close > 0:
            move = abs(bar.open - prev_close) / prev_close
            if move > ABNORMAL_GAP_FRACTION:
                quality = DataQuality.SUSPECT_GAP
                notes.append(
                    f"open gapped {move:.1%} from prior close ({prev_close} -> {bar.open})"
                )

        if quality == DataQuality.OK and now is not None:
            if now - bar.timestamp > DEFAULT_STALE_AFTER:
                quality = DataQuality.STALE
                notes.append(f"bar is older than {DEFAULT_STALE_AFTER} relative to now")

        if field_problems:
            prev_close = None  # don't compare the next bar's gap against a bad close
        else:
            prev_close = bar.close

        validated.append(replace(bar, quality=quality, quality_notes=tuple(notes)))

    return validated
