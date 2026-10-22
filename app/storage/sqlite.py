"""SQLite persistence for the paper-trading ledger.

Restart-safe: trades/orders written here survive the process exiting. No
table here represents a live order or a real brokerage account.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from app.models import Order, Trade

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    entry_order_id TEXT NOT NULL,
    exit_order_id TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    quantity INTEGER NOT NULL,
    entry_at TEXT NOT NULL,
    exit_at TEXT NOT NULL,
    gross_pnl REAL NOT NULL,
    costs REAL NOT NULL,
    net_pnl REAL NOT NULL,
    exit_reason TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    submitted_at TEXT NOT NULL,
    status TEXT NOT NULL,
    filled_price REAL,
    filled_quantity INTEGER,
    filled_at TEXT,
    rejection_reason TEXT,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    symbols TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    starting_cash REAL NOT NULL,
    final_equity REAL,
    metrics_json TEXT
);
"""


class SqliteRepository:
    """PAPER TRADING — NO REAL ORDERS. Stores simulated trades/orders only."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def save_trade(self, trade: Trade) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO trades
               (trade_id, symbol, entry_order_id, exit_order_id, entry_price, exit_price,
                quantity, entry_at, exit_at, gross_pnl, costs, net_pnl, exit_reason, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade.trade_id, trade.symbol, trade.entry_order_id, trade.exit_order_id,
                trade.entry_price, trade.exit_price, trade.quantity,
                trade.entry_at.isoformat(), trade.exit_at.isoformat(),
                trade.gross_pnl, trade.costs, trade.net_pnl, trade.exit_reason,
                datetime.now().isoformat(),
            ),
        )
        self._conn.commit()

    def save_order(self, order: Order) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO orders
               (order_id, signal_id, symbol, side, quantity, submitted_at, status,
                filled_price, filled_quantity, filled_at, rejection_reason, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order.order_id, order.signal_id, order.symbol, order.side.value, order.quantity,
                order.submitted_at.isoformat(), order.status.value, order.filled_price,
                order.filled_quantity,
                order.filled_at.isoformat() if order.filled_at else None,
                order.rejection_reason,
                datetime.now().isoformat(),
            ),
        )
        self._conn.commit()

    def save_run_summary(
        self, run_id: str, symbols: list[str], strategy_name: str,
        starting_cash: float, final_equity: float, metrics_dict: dict,
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO runs
               (run_id, started_at, symbols, strategy_name, starting_cash, final_equity, metrics_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, datetime.now().isoformat(), json.dumps(symbols), strategy_name,
                starting_cash, final_equity, json.dumps(metrics_dict, default=str),
            ),
        )
        self._conn.commit()

    def all_trades(self) -> list[sqlite3.Row]:
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute("SELECT * FROM trades ORDER BY exit_at")
        return cur.fetchall()

    def trade_count(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM trades")
        return cur.fetchone()[0]
