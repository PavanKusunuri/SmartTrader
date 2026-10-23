"""Shared value objects used across data, strategy, backtest, and paper-broker code."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class DataQuality(str, Enum):
    """How trustworthy a bar is, set by validation before any strategy sees it."""

    OK = "OK"
    STALE = "STALE"
    SUSPECT_GAP = "SUSPECT_GAP"
    INVALID = "INVALID"


@dataclass(frozen=True)
class Bar:
    """One OHLCV bar for one symbol, timestamped in Asia/Kolkata.

    `source` and `retrieved_at` exist so every downstream decision can be
    traced back to where the data came from and when it was fetched/loaded,
    per CLAUDE.md's data-provenance requirement.
    """

    symbol: str
    timestamp: datetime  # must be tz-aware, Asia/Kolkata
    open: float
    high: float
    low: float
    close: float
    volume: int
    source: str
    retrieved_at: datetime
    interval: str  # e.g. "1d", "5m"
    quality: DataQuality = DataQuality.OK
    quality_notes: tuple[str, ...] = field(default_factory=tuple)


class ChargeProfile(str, Enum):
    """Which statutory charge schedule a trade is settled under.

    Indian cash-equity charges differ materially between a delivery trade
    (held overnight, STT on both legs, DP charge on the sell leg) and an
    intraday trade (squared off same day, STT on the sell leg only, no DP
    charge). Carrying the profile on the signal and the position means the
    exit leg is costed the same way the entry was, instead of a single
    blended guess.
    """

    DELIVERY = "DELIVERY"
    INTRADAY = "INTRADAY"


class SignalAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True)
class Signal:
    """A strategy's decision for one symbol at one bar, always explained.

    `signal_id` is deterministic (symbol + strategy + bar timestamp) so the
    same bar never produces two different simulated orders, per the
    duplicate-signal rule in CLAUDE.md section 7C.
    """

    signal_id: str
    symbol: str
    timestamp: datetime
    strategy_name: str
    action: SignalAction
    reason: str
    inputs: dict
    stop_loss: Optional[float] = None
    target: Optional[float] = None
    # Charge schedule this trade would settle under, set by the strategy
    # (intraday strategies square off same day; swing strategies take
    # delivery). Drives the cost estimate and the viability gate.
    charge_profile: ChargeProfile = ChargeProfile.DELIVERY


class OrderStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass
class Order:
    order_id: str
    signal_id: str
    symbol: str
    side: SignalAction  # BUY or SELL only
    quantity: int
    submitted_at: datetime
    status: OrderStatus = OrderStatus.SUBMITTED
    filled_price: Optional[float] = None
    filled_quantity: int = 0
    filled_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    # Charge schedule this leg was priced under; recorded so the leg's costs
    # can be re-derived later instead of assumed.
    charge_profile: ChargeProfile = ChargeProfile.DELIVERY


@dataclass
class Position:
    symbol: str
    quantity: int
    avg_entry_price: float
    stop_loss: Optional[float] = None
    target: Optional[float] = None
    opened_at: Optional[datetime] = None
    entry_order_id: str = ""
    entry_costs: float = 0.0
    charge_profile: ChargeProfile = ChargeProfile.DELIVERY


@dataclass(frozen=True)
class Trade:
    """A closed round-trip, recorded for the ledger and backtest metrics."""

    trade_id: str
    symbol: str
    entry_order_id: str
    exit_order_id: str
    entry_price: float
    exit_price: float
    quantity: int
    entry_at: datetime
    exit_at: datetime
    gross_pnl: float
    costs: float
    net_pnl: float
    exit_reason: str
    # True when stop-loss and target were both touched inside the same bar,
    # so the real order of events is unknowable from OHLC data alone. The
    # engine assumes the stop filled first (conservative), but the outcome
    # is marked here so these trades can be excluded or scrutinised.
    ambiguous_exit: bool = False
    charge_profile: ChargeProfile = ChargeProfile.DELIVERY
