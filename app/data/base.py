"""DataProvider interface.

Every provider adapter (CSV now, a lawful external provider later) must
implement this interface so strategy/backtest code never depends on where
bars came from. No provider in v1 does live streaming; nothing here may be
described as real-time.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from app.models import Bar


class DataProvider(ABC):
    @abstractmethod
    def get_bars(
        self,
        symbol: str,
        interval: str = "1d",
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> list[Bar]:
        """Return validated bars for `symbol`, oldest first. May return bars
        flagged with a non-OK DataQuality rather than raising, so callers can
        decide whether to trade on them (usually: no)."""
        raise NotImplementedError

    def get_latest_quote(self, symbol: str) -> Optional[Bar]:
        """Optional. Returns None if the provider has no quote capability.
        A provider that only has delayed/EOD data must return None here
        rather than pretend to offer a live quote."""
        return None
