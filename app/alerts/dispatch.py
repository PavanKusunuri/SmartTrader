"""Turns scan results into alerts, throttles repeats, and records every alert locally.

CLAUDE.md §10: idempotent (a repeated poll must not re-alert), alerts and failures
are stored locally, and stale data / provider errors / risk-limit triggers alert.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.alerts.base import Alert, AlertKind, Notifier
from app.scanner.scanner import ScanOutcome, ScanReport
from app.storage.sqlite import SqliteRepository

COMPONENT = "alerts"


def alerts_from_scan(report: ScanReport, strategy: str) -> list[Alert]:
    now = report.scanned_at
    out: list[Alert] = []
    for r in report.results:
        bar = r.bar_timestamp.isoformat() if r.bar_timestamp else "-"
        if r.outcome is ScanOutcome.TRADED:
            out.append(Alert(
                AlertKind.SIGNAL, f"{r.symbol}: simulated trade ({strategy})",
                f"{r.detail}\nBar: {bar}\nFills are simulated, not exchange executions.",
                f"signal:{strategy}:{r.symbol}:{bar}", now))
        elif r.outcome is ScanOutcome.REJECTED_BROKER:
            out.append(Alert(
                AlertKind.RISK_LIMIT, f"{r.symbol}: simulated order refused",
                f"{r.detail}\nBar: {bar}", f"refused:{strategy}:{r.symbol}:{bar}", now))
        elif r.outcome is ScanOutcome.REJECTED_STALE:
            out.append(Alert(
                AlertKind.STALE_DATA, f"{r.symbol}: stale data, no trade",
                f"{r.detail}\nLast bar: {bar}", f"stale:{r.symbol}:{now:%Y%m%d%H}", now))
        elif r.outcome in (ScanOutcome.ERROR, ScanOutcome.REJECTED_DATA):
            out.append(Alert(
                AlertKind.DATA_ERROR, f"{r.symbol}: scan error, no trade",
                r.detail, f"error:{r.symbol}:{now:%Y%m%d%H}", now))
    return out


class AlertDispatcher:
    def __init__(
        self, notifier: Notifier, repo: SqliteRepository | None,
        throttle: timedelta = timedelta(hours=1),
    ) -> None:
        self._notifier = notifier
        self._repo = repo
        self._throttle = throttle
        self._seen: set[str] = set()

    def dispatch(self, alerts: list[Alert]) -> dict[str, int]:
        counts = {"sent": 0, "failed": 0, "throttled": 0}
        for alert in alerts:
            if alert.key in self._seen or self._recently_sent(alert.key):
                counts["throttled"] += 1
                continue
            self._seen.add(alert.key)
            delivered = self._notifier.send(alert)
            counts["sent" if delivered else "failed"] += 1
            self._record(alert, delivered)
        return counts

    # An alert is stored locally whether or not it was delivered, so a Telegram
    # outage shows up in the dashboard's event list instead of being silent.
    def _record(self, alert: Alert, delivered: bool) -> None:
        if self._repo is None:
            return
        self._repo.log_event(
            severity="INFO" if delivered else "WARNING", component=COMPONENT,
            message=f"{'sent' if delivered else 'NOT DELIVERED'}: [{alert.kind.value}] {alert.title}",
            detail={"key": alert.key, "delivered": delivered},
        )

    def _recently_sent(self, key: str) -> bool:
        if self._repo is None:
            return False
        since = (datetime.now() - self._throttle).isoformat()
        return self._repo.event_exists(COMPONENT, key, since)
