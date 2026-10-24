"""Alert model and notifier interface.

PAPER TRADING — NO REAL ORDERS. Alerts are informational. Every alert text
carries the paper-trading label so a forwarded message can never be mistaken
for a real order confirmation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

LABEL = "PAPER TRADING — NO REAL ORDERS"


class AlertKind(str, Enum):
    SIGNAL = "SIGNAL"          # a simulated entry/exit happened
    RISK_LIMIT = "RISK_LIMIT"  # broker refused an order on a risk rule
    STALE_DATA = "STALE_DATA"
    DATA_ERROR = "DATA_ERROR"
    SYSTEM = "SYSTEM"          # database / provider / scheduler failures


@dataclass(frozen=True)
class Alert:
    kind: AlertKind
    title: str
    body: str
    key: str                   # dedupe key; same key inside the throttle window sends once
    created_at: datetime

    def render(self) -> str:
        text = (
            f"{LABEL}\n[{self.kind.value}] {self.title}\n{self.body}\n"
            f"{self.created_at.isoformat(timespec='seconds')}"
        )
        return text[:3500]


class Notifier(ABC):
    """Returns True only if the message was actually delivered."""

    @abstractmethod
    def send(self, alert: Alert) -> bool:
        raise NotImplementedError


class NullNotifier(Notifier):
    """Used whenever alerts are disabled or not configured. Sends nothing."""

    def send(self, alert: Alert) -> bool:
        return False
