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

    @property
    def required_history(self) -> int:
        """Minimum number of bars this strategy needs before it can emit any
        actionable signal. Evaluation code uses this to tell "the strategy
        found no setup" apart from "the window was too short to warm up the
        indicators" — two very different findings that look identical if you
        only count trades."""
        return 2

    @abstractmethod
    def evaluate(self, bars: list[Bar]) -> Signal:
        """`bars` must be time-ordered, all with timestamp <= decision time,
        i.e. bars[-1] is "now". Returns exactly one Signal (possibly
        NO_TRADE) for bars[-1]'s timestamp."""
        raise NotImplementedError
