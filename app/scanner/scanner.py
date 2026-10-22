"""Watchlist scanner.

PAPER TRADING — NO REAL ORDERS. The scanner can only reach the simulated
broker in app/paper/broker.py.

Safety rules this module implements (CLAUDE.md section 10), all of which
exist to stop a recovering/misconfigured scan from doing damage:

- **Session gating.** Signal logic does not run outside the configured
  trading session. A closed market produces a SKIPPED outcome, not a trade.
- **Freshness revalidation.** Every entry is checked against the age of the
  newest bar at scan time. After a network outage or a missed scan, stale
  bars are REJECTED rather than replayed into a burst of entries.
- **Idempotency.** A given (symbol, bar timestamp) is processed once. Polling
  the same bar repeatedly cannot create a second order, both because the
  scanner tracks what it has seen and because signal IDs are deterministic
  per bar and the broker rejects duplicates.
- **Failure isolation.** A provider error or bad data for one symbol is
  recorded and does not abort the scan, and never becomes a trade signal.

A scan is a decision point, not a schedule. Driving it on an interval is the
caller's job (see `app/cli.py scan`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from app.data.base import DataProvider
from app.market_calendar import MarketSession
from app.models import Bar, DataQuality, Order, Signal, SignalAction
from app.paper.broker import PaperBroker
from app.strategies.base import Strategy


class ScanOutcome(str, Enum):
    TRADED = "TRADED"            # a simulated order was created
    NO_TRADE = "NO_TRADE"        # strategy declined
    SKIPPED_SESSION = "SKIPPED_SESSION"    # market closed / outside session
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"  # this bar was already processed
    REJECTED_STALE = "REJECTED_STALE"      # newest bar too old to act on
    REJECTED_DATA = "REJECTED_DATA"        # invalid/insufficient data
    ERROR = "ERROR"              # provider/unexpected failure, isolated


@dataclass
class SymbolScanResult:
    symbol: str
    outcome: ScanOutcome
    detail: str
    bar_timestamp: Optional[datetime] = None
    signal: Optional[Signal] = None
    order: Optional[Order] = None


@dataclass
class ScanReport:
    scanned_at: datetime
    results: list[SymbolScanResult] = field(default_factory=list)

    @property
    def warnings(self) -> list[str]:
        """Conditions an operator should see: anything that stopped a symbol
        from being evaluated normally."""
        flagged = {
            ScanOutcome.REJECTED_STALE,
            ScanOutcome.REJECTED_DATA,
            ScanOutcome.ERROR,
        }
        return [
            f"{r.symbol}: {r.outcome.value} — {r.detail}"
            for r in self.results
            if r.outcome in flagged
        ]

    @property
    def orders_created(self) -> list[Order]:
        return [r.order for r in self.results if r.order is not None]

    def count(self, outcome: ScanOutcome) -> int:
        return sum(1 for r in self.results if r.outcome == outcome)


class Scanner:
    def __init__(
        self,
        provider: DataProvider,
        strategy: Strategy,
        broker: PaperBroker,
        watchlist,
        session: MarketSession | None = None,
        interval: str = "1d",
        max_bar_age: timedelta = timedelta(days=5),
    ):
        self.provider = provider
        self.strategy = strategy
        self.broker = broker
        self.watchlist = watchlist
        self.session = session
        self.interval = interval
        self.max_bar_age = max_bar_age
        # (symbol, bar timestamp) pairs already acted on, for idempotency.
        self._processed: set[tuple[str, datetime]] = set()

    def scan_once(self, now: datetime) -> ScanReport:
        """Evaluates every watchlist symbol as of `now`.

        `now` is passed in rather than read from the clock so scans are
        testable and reproducible, and so a backfilled scan cannot
        accidentally believe stale data is current.
        """

        report = ScanReport(scanned_at=now)

        if self.session is not None and not self.session.is_in_session(now):
            for symbol in self.watchlist:
                report.results.append(
                    SymbolScanResult(
                        symbol=symbol,
                        outcome=ScanOutcome.SKIPPED_SESSION,
                        detail=f"market session closed at {now.isoformat()}",
                    )
                )
            return report

        for symbol in self.watchlist:
            report.results.append(self._scan_symbol(symbol, now))
        return report

    def _scan_symbol(self, symbol: str, now: datetime) -> SymbolScanResult:
        try:
            bars = self.provider.get_bars(symbol, interval=self.interval)
        except Exception as exc:  # noqa: BLE001 — isolate one symbol's failure
            return SymbolScanResult(
                symbol=symbol,
                outcome=ScanOutcome.ERROR,
                detail=f"provider error: {type(exc).__name__}: {exc}",
            )

        if not bars:
            return SymbolScanResult(
                symbol=symbol, outcome=ScanOutcome.REJECTED_DATA,
                detail="provider returned no bars",
            )

        # Only bars at or before `now` may inform a decision.
        visible = [b for b in bars if b.timestamp <= now]
        if not visible:
            return SymbolScanResult(
                symbol=symbol, outcome=ScanOutcome.REJECTED_DATA,
                detail=f"no bars at or before {now.isoformat()}",
            )

        latest = visible[-1]

        if (symbol, latest.timestamp) in self._processed:
            return SymbolScanResult(
                symbol=symbol, outcome=ScanOutcome.SKIPPED_DUPLICATE,
                detail=f"bar {latest.timestamp.isoformat()} already processed",
                bar_timestamp=latest.timestamp,
            )

        if latest.quality == DataQuality.INVALID:
            return SymbolScanResult(
                symbol=symbol, outcome=ScanOutcome.REJECTED_DATA,
                detail=f"latest bar is INVALID: {', '.join(latest.quality_notes) or 'no detail'}",
                bar_timestamp=latest.timestamp,
            )

        age = now - latest.timestamp
        if age > self.max_bar_age:
            return SymbolScanResult(
                symbol=symbol, outcome=ScanOutcome.REJECTED_STALE,
                detail=(
                    f"newest bar is {age} old (limit {self.max_bar_age}); refusing to "
                    "trade on stale data"
                ),
                bar_timestamp=latest.timestamp,
            )

        position = self.broker.portfolio.positions.get(symbol)
        try:
            signal = self.strategy.evaluate(visible, position=position)
        except Exception as exc:  # noqa: BLE001
            return SymbolScanResult(
                symbol=symbol, outcome=ScanOutcome.ERROR,
                detail=f"strategy error: {type(exc).__name__}: {exc}",
                bar_timestamp=latest.timestamp,
            )

        # Mark processed regardless of the decision, so a NO_TRADE bar is not
        # re-evaluated on the next poll of the same bar.
        self._processed.add((symbol, latest.timestamp))

        if signal.action == SignalAction.NO_TRADE:
            return SymbolScanResult(
                symbol=symbol, outcome=ScanOutcome.NO_TRADE, detail=signal.reason,
                bar_timestamp=latest.timestamp, signal=signal,
            )

        if (
            self.session is not None
            and signal.action == SignalAction.BUY
            and not self.session.can_enter(now)
        ):
            return SymbolScanResult(
                symbol=symbol, outcome=ScanOutcome.SKIPPED_SESSION,
                detail=f"entry window closed (cutoff {self.session.entry_cutoff})",
                bar_timestamp=latest.timestamp, signal=signal,
            )

        # Live scanning has no "next bar" yet, so the only price available to
        # act on is the latest close. This is explicitly weaker than the
        # backtest's next-bar-open model and is documented as such.
        order = self.broker.process_signal(
            signal,
            fill_price_basis=latest.close,
            current_prices={symbol: latest.close},
            fill_timestamp=now,
        )

        return SymbolScanResult(
            symbol=symbol,
            outcome=ScanOutcome.TRADED if order is not None else ScanOutcome.NO_TRADE,
            detail=signal.reason,
            bar_timestamp=latest.timestamp,
            signal=signal,
            order=order,
        )
