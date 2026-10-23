"""Persists the scanner's virtual portfolio and processed-bar memory.

Why this is separate from the backtest `positions` table: that table records
positions still open at the END OF A BACKTEST RUN, keyed by run_id, and the
dashboard presents it as exactly that. The scanner's state is different — one
live, resumable portfolio — and mixing the two would make a backtest's
leftovers look like the scanner's current holdings.

Tables are created here with CREATE TABLE IF NOT EXISTS and never dropped, so
this module needs no change to app/storage/sqlite.py. They are not in the
dashboard's explorable-table allowlist, so the dashboard does not expose them.

PAPER TRADING — NO REAL ORDERS. Nothing here is a brokerage account.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from app.models import ChargeProfile, Position

SCHEMA = """
CREATE TABLE IF NOT EXISTS scanner_portfolio (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    starting_cash REAL NOT NULL,
    cash REAL NOT NULL,
    realized_pnl REAL NOT NULL,
    current_day TEXT,
    day_start_equity REAL NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scanner_positions (
    symbol TEXT PRIMARY KEY,
    quantity INTEGER NOT NULL,
    avg_entry_price REAL NOT NULL,
    stop_loss REAL,
    target REAL,
    opened_at TEXT,
    entry_order_id TEXT,
    entry_costs REAL NOT NULL,
    charge_profile TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scanner_processed (
    symbol TEXT NOT NULL,
    bar_timestamp TEXT NOT NULL,
    PRIMARY KEY (symbol, bar_timestamp)
);
"""

STATE_TABLES = ("scanner_portfolio", "scanner_positions", "scanner_processed")


class ScannerStateStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def load_portfolio_state(self) -> dict | None:
        """Returns a dict for `Portfolio.from_state`, or None when the scanner
        has never saved state (a first run, which correctly starts fresh)."""

        row = self._conn.execute(
            "SELECT starting_cash, cash, realized_pnl, current_day, day_start_equity "
            "FROM scanner_portfolio WHERE id = 1"
        ).fetchone()
        if row is None:
            return None

        positions = []
        for p in self._conn.execute(
            "SELECT symbol, quantity, avg_entry_price, stop_loss, target, opened_at, "
            "entry_order_id, entry_costs, charge_profile FROM scanner_positions "
            "ORDER BY symbol"
        ):
            positions.append(
                Position(
                    symbol=p[0], quantity=p[1], avg_entry_price=p[2],
                    stop_loss=p[3], target=p[4],
                    opened_at=datetime.fromisoformat(p[5]) if p[5] else None,
                    entry_order_id=p[6] or "", entry_costs=p[7],
                    # Restored explicitly: a position that came back as DELIVERY
                    # would have its first exit costed under the wrong schedule.
                    charge_profile=ChargeProfile(p[8]),
                )
            )

        return {
            "starting_cash": row[0], "cash": row[1], "realized_pnl": row[2],
            "current_day": row[3], "day_start_equity": row[4], "positions": positions,
        }

    def save_portfolio_state(self, state: dict) -> None:
        """Replaces the saved portfolio atomically. Either the whole snapshot
        lands or none of it does, so a crash mid-save cannot leave cash from
        one scan paired with positions from another."""

        with self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO scanner_portfolio
                   (id, starting_cash, cash, realized_pnl, current_day,
                    day_start_equity, updated_at)
                   VALUES (1, ?, ?, ?, ?, ?, ?)""",
                (
                    state["starting_cash"], state["cash"], state["realized_pnl"],
                    state["current_day"], state["day_start_equity"],
                    datetime.now().isoformat(),
                ),
            )
            self._conn.execute("DELETE FROM scanner_positions")
            self._conn.executemany(
                """INSERT INTO scanner_positions
                   (symbol, quantity, avg_entry_price, stop_loss, target, opened_at,
                    entry_order_id, entry_costs, charge_profile)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        p.symbol, p.quantity, p.avg_entry_price, p.stop_loss, p.target,
                        p.opened_at.isoformat() if p.opened_at else None,
                        p.entry_order_id, p.entry_costs, p.charge_profile.value,
                    )
                    for p in state["positions"]
                ],
            )

    def load_processed(self) -> set[tuple[str, datetime]]:
        return {
            (symbol, datetime.fromisoformat(ts))
            for symbol, ts in self._conn.execute(
                "SELECT symbol, bar_timestamp FROM scanner_processed"
            )
        }

    def save_processed(self, processed: set[tuple[str, datetime]]) -> None:
        with self._conn:
            self._conn.executemany(
                "INSERT OR IGNORE INTO scanner_processed (symbol, bar_timestamp) VALUES (?, ?)",
                [(symbol, ts.isoformat()) for symbol, ts in processed],
            )

    def row_counts(self) -> dict[str, int]:
        return {
            table: self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in STATE_TABLES
        }

    def clear(self) -> dict[str, int]:
        """Deletes all saved scanner state. Called by the explicit, confirmed
        ledger reset so that a reset really does erase the simulation."""

        deleted = self.row_counts()
        with self._conn:
            for table in STATE_TABLES:
                self._conn.execute(f"DELETE FROM {table}")
        return deleted
