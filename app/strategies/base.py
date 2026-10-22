"""Strategy interface.

A strategy is a pure function of "bars available up to now" -> Signal. It
must never see a bar timestamped after the decision point (no look-ahead),
must never place an order itself, and must return NO_TRADE with a reason
rather than guessing when data is insufficient/invalid.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.models import Bar, Signal


class Strategy(ABC):
    name: str

    @abstractmethod
    def evaluate(self, bars: list[Bar]) -> Signal:
        """`bars` must be time-ordered, all with timestamp <= decision time,
        i.e. bars[-1] is "now". Returns exactly one Signal (possibly
        NO_TRADE) for bars[-1]'s timestamp."""
        raise NotImplementedError
