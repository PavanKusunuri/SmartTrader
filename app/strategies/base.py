"""Strategy interface.

A strategy is a pure function of "bars available up to now" -> Signal. It
must never see a bar timestamped after the decision point (no look-ahead),
must never place an order itself, and must return NO_TRADE with a reason
rather than guessing when data is insufficient/invalid.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from app.models import Bar, ChargeProfile, Position, Signal


class Strategy(ABC):
    name: str

    # Settlement profile this strategy's trades would be charged under.
    # Intraday strategies square off the same day (STT on the sell leg only,
    # no DP charge); swing strategies take delivery (STT on both legs plus a
    # flat DP charge on the sell). The strategy declares it because only the
    # strategy knows its own holding horizon; the broker and the viability
    # gate then cost the trade correctly.
    charge_profile: ChargeProfile = ChargeProfile.DELIVERY

    @property
    def required_history(self) -> int:
        """Minimum number of bars this strategy needs before it can emit any
        actionable signal. Evaluation code uses this to tell "the strategy
        found no setup" apart from "the window was too short to warm up the
        indicators" — two very different findings that look identical if you
        only count trades."""
        return 2

    @abstractmethod
    def evaluate(self, bars: list[Bar], position: Optional[Position] = None) -> Signal:
        """`bars` must be time-ordered, all with timestamp <= decision time,
        i.e. bars[-1] is "now". Returns exactly one Signal (possibly
        NO_TRADE) for bars[-1]'s timestamp.

        `position` is the currently open position in this symbol, or None.
        A strategy that holds a position must only ever return an exit
        (SELL) or NO_TRADE for it: pyramiding into an existing position is
        not supported by the paper broker, which rejects a duplicate entry.
        """
        raise NotImplementedError
