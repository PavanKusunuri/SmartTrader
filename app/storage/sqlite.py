"""SQLite persistence for the paper-trading ledger.

Restart-safe: everything written here survives the process exiting. No table
here represents a live order or a real brokerage account.

Schema evolution is ADDITIVE ONLY. `_ensure_columns` issues
`ALTER TABLE ... ADD COLUMN` for columns that are missing from an older
database file, and every table is created with `IF NOT EXISTS`. Nothing in
this module drops, truncates, or recreates a table, so opening an existing
ledger with a newer build of the app cannot lose previously recorded trades.
The only destructive operation in the codebase is
`SqliteRepository.reset_ledger()`, which requires an explicit confirmation
token and is never called implicitly.

Durability notes:
- `journal_mode=WAL` lets the dashboard read the ledger while a backtest is
  writing to it, instead of blocking on a locked database.
- `open_readonly()` returns a connection opened with SQLite's `mode=ro` URI
  flag. The dashboard's database explorer uses it so a read path cannot
  write even if a query were wrong.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.models import Order, Position, Signal, Trade

# Tables the dashboard is allowed to expose. Anything not listed here is not
# reachable through the read-only explorer API, so adding an internal table
# later does not silently publish it.
EXPLORABLE_TABLES = (
    "trades",
    "orders",
    "signals",
    "positions",
    "equity_snapshots",
    "runs",
    "app_events",
)

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

-- Every signal a strategy produced, including NO_TRADE. A signal that never
-- became an order is the whole point of this table: it is the audit trail for
-- "why did the system not trade here".
CREATE TABLE IF NOT EXISTS signals (
    run_id TEXT NOT NULL,
    signal_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    inputs_json TEXT,
    stop_loss REAL,
    target REAL,
    outcome TEXT,
    order_id TEXT,
    trade_id TEXT,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (run_id, signal_id)
);

-- Positions still OPEN when a run finished. Closed positions live in `trades`.
CREATE TABLE IF NOT EXISTS positions (
    run_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    avg_entry_price REAL NOT NULL,
    stop_loss REAL,
    target REAL,
    opened_at TEXT,
    entry_order_id TEXT,
    entry_costs REAL,
    last_price REAL,
    last_price_at TEXT,
    last_price_source TEXT,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (run_id, symbol)
);

-- The equity curve, one row per evaluated bar event, so the dashboard can
-- draw equity/drawdown from recorded values rather than recomputing them.
CREATE TABLE IF NOT EXISTS equity_snapshots (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    timestamp TEXT,
    equity REAL NOT NULL,
    cash REAL,
    position_count INTEGER,
    PRIMARY KEY (run_id, seq)
);

-- Application events for the health page. Written explicitly by the app; an
-- empty table means nothing was recorded, NOT that nothing went wrong.
CREATE TABLE IF NOT EXISTS app_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    severity TEXT NOT NULL,
    component TEXT NOT NULL,
    message TEXT NOT NULL,
    run_id TEXT,
    detail_json TEXT
);
"""

# Columns added after the first released schema. Each entry is
# (table, column, SQL type) and is applied only if absent.
ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # Attribution: which run produced this row. Without it, re-running a
    # backtest leaves indistinguishable duplicate trades in the ledger.
    ("trades", "run_id", "TEXT"),
    ("trades", "strategy_name", "TEXT"),
    ("trades", "side", "TEXT"),
    ("trades", "stop_loss", "REAL"),
    ("trades", "target", "REAL"),
    ("trades", "status", "TEXT"),
    ("trades", "ambiguous_exit", "INTEGER"),
    ("orders", "run_id", "TEXT"),
    ("orders", "trade_id", "TEXT"),
    ("orders", "order_type", "TEXT"),
    # Which charge profile (delivery/intraday) priced this leg. Required to
    # re-derive the leg's costs correctly: the two profiles have different
    # STT, stamp duty and DP charges, so assuming delivery for an intraday
    # order would report the wrong number.
    ("orders", "charge_profile", "TEXT"),
    ("trades", "charge_profile", "TEXT"),
    # fill_price_basis/slippage/costs are nullable because the Order model
    # does not currently carry them. They are left NULL rather than guessed;
    # the API derives slippage and costs from the run's recorded cost
    # assumptions and labels them as derived. See app/api/derive.py.
    ("orders", "fill_price_basis", "REAL"),
    ("orders", "slippage", "REAL"),
    ("orders", "costs", "REAL"),
    ("runs", "finished_at", "TEXT"),
    ("runs", "interval", "TEXT"),
    ("runs", "start_date", "TEXT"),
    ("runs", "end_date", "TEXT"),
    ("runs", "data_source_json", "TEXT"),
    ("runs", "params_json", "TEXT"),
    ("runs", "costs_json", "TEXT"),
    ("runs", "risk_json", "TEXT"),
    ("runs", "reconciliation_json", "TEXT"),
    ("runs", "signal_count", "INTEGER"),
    ("runs", "expired_signal_count", "INTEGER"),
    ("runs", "ambiguous_trade_count", "INTEGER"),
    ("runs", "notes", "TEXT"),
)

# Indexes are applied AFTER _ensure_columns, because an index may reference a
# column that an older database file does not have yet.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_trades_exit_at ON trades(exit_at);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_run ON trades(run_id);
CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol);
CREATE INDEX IF NOT EXISTS idx_orders_run ON orders(run_id);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(timestamp);
CREATE INDEX IF NOT EXISTS idx_signals_action ON signals(action);
CREATE INDEX IF NOT EXISTS idx_events_ts ON app_events(ts);
"""

RESET_CONFIRMATION_TOKEN = "DELETE MY PAPER TRADING HISTORY"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _enum_value(value: Any) -> Any:
    """Stores an Enum by its value, so the column holds 'DELIVERY' rather than
    a Python repr. Returns None/plain values unchanged, so a model that does
    not carry the field yet simply records NULL."""

    return getattr(value, "value", value)


class SqliteRepository:
    """PAPER TRADING — NO REAL ORDERS. Stores simulated trades/orders only."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("PRAGMA foreign_keys = ON")
        # WAL so the dashboard can read while a backtest writes.
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)
        self._ensure_columns()
        self._conn.executescript(INDEXES)
        self._conn.commit()

    # ---------------------------------------------------------------- schema

    def _ensure_columns(self) -> None:
        """Add any missing columns to an older database file. Additive only."""
        for table, column, sql_type in ADDED_COLUMNS:
            existing = {
                row[1] for row in self._conn.execute(f'PRAGMA table_info("{table}")')
            }
            if column not in existing:
                self._conn.execute(
                    f'ALTER TABLE "{table}" ADD COLUMN "{column}" {sql_type}'
                )

    def close(self) -> None:
        self._conn.close()

    # ----------------------------------------------------------- ledger write

    def save_trade(self, trade: Trade, run_id: str | None = None,
                   strategy_name: str | None = None) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO trades
               (trade_id, symbol, entry_order_id, exit_order_id, entry_price, exit_price,
                quantity, entry_at, exit_at, gross_pnl, costs, net_pnl, exit_reason,
                recorded_at, run_id, strategy_name, side, stop_loss, target, status,
                ambiguous_exit, charge_profile)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade.trade_id, trade.symbol, trade.entry_order_id, trade.exit_order_id,
                trade.entry_price, trade.exit_price, trade.quantity,
                trade.entry_at.isoformat(), trade.exit_at.isoformat(),
                trade.gross_pnl, trade.costs, trade.net_pnl, trade.exit_reason,
                datetime.now().isoformat(),
                run_id, strategy_name,
                # v1 is long-only cash equity: a closed trade is always a BUY
                # entry followed by a SELL exit.
                "LONG",
                getattr(trade, "stop_loss", None),
                getattr(trade, "target", None),
                "CLOSED",
                1 if getattr(trade, "ambiguous_exit", False) else 0,
                _enum_value(getattr(trade, "charge_profile", None)),
            ),
        )
        self._conn.commit()

    def save_order(self, order: Order, run_id: str | None = None,
                   trade_id: str | None = None) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO orders
               (order_id, signal_id, symbol, side, quantity, submitted_at, status,
                filled_price, filled_quantity, filled_at, rejection_reason, recorded_at,
                run_id, trade_id, order_type, charge_profile)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order.order_id, order.signal_id, order.symbol, order.side.value,
                order.quantity, order.submitted_at.isoformat(), order.status.value,
                order.filled_price, order.filled_quantity, _iso(order.filled_at),
                order.rejection_reason, datetime.now().isoformat(),
                run_id, trade_id,
                # The paper broker simulates market-style fills only; it has no
                # limit/stop order types to record.
                "MARKET",
                _enum_value(getattr(order, "charge_profile", None)),
            ),
        )
        self._conn.commit()

    def save_signal(
        self,
        signal: Signal,
        run_id: str,
        outcome: str | None = None,
        order_id: str | None = None,
        trade_id: str | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO signals
               (run_id, signal_id, symbol, timestamp, strategy_name, action, reason,
                inputs_json, stop_loss, target, outcome, order_id, trade_id, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, signal.signal_id, signal.symbol, signal.timestamp.isoformat(),
                signal.strategy_name, signal.action.value, signal.reason,
                json.dumps(signal.inputs, default=str), signal.stop_loss, signal.target,
                outcome, order_id, trade_id, datetime.now().isoformat(),
            ),
        )
        self._conn.commit()

    def save_signals(self, rows: Iterable[tuple[Signal, str, str | None, str | None, str | None]]) -> int:
        """Bulk form of save_signal in a single transaction. `rows` yields
        (signal, run_id, outcome, order_id, trade_id)."""

        now = datetime.now().isoformat()
        payload = [
            (
                run_id, s.signal_id, s.symbol, s.timestamp.isoformat(), s.strategy_name,
                s.action.value, s.reason, json.dumps(s.inputs, default=str),
                s.stop_loss, s.target, outcome, order_id, trade_id, now,
            )
            for s, run_id, outcome, order_id, trade_id in rows
        ]
        with self._conn:
            self._conn.executemany(
                """INSERT OR REPLACE INTO signals
                   (run_id, signal_id, symbol, timestamp, strategy_name, action, reason,
                    inputs_json, stop_loss, target, outcome, order_id, trade_id, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                payload,
            )
        return len(payload)

    def save_open_position(
        self,
        position: Position,
        run_id: str,
        last_price: float | None = None,
        last_price_at: datetime | None = None,
        last_price_source: str | None = None,
    ) -> None:
        """Records a position that was still open when the run ended.

        `last_price` must be a price the data provider actually supplied. Pass
        None when no price is available — the dashboard shows "no price" rather
        than falling back to the entry price, which would understate risk by
        reporting zero unrealised P&L.
        """

        self._conn.execute(
            """INSERT OR REPLACE INTO positions
               (run_id, symbol, quantity, avg_entry_price, stop_loss, target, opened_at,
                entry_order_id, entry_costs, last_price, last_price_at, last_price_source,
                recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, position.symbol, position.quantity, position.avg_entry_price,
                position.stop_loss, position.target, _iso(position.opened_at),
                position.entry_order_id, position.entry_costs,
                last_price, _iso(last_price_at), last_price_source,
                datetime.now().isoformat(),
            ),
        )
        self._conn.commit()

    def save_equity_curve(
        self, run_id: str, points: Sequence[tuple[datetime | None, float, float | None, int | None]]
    ) -> int:
        """Stores the run's equity curve. `points` is an ordered sequence of
        (timestamp, equity, cash, position_count)."""

        payload = [
            (run_id, seq, _iso(ts), equity, cash, position_count)
            for seq, (ts, equity, cash, position_count) in enumerate(points)
        ]
        with self._conn:
            self._conn.executemany(
                """INSERT OR REPLACE INTO equity_snapshots
                   (run_id, seq, timestamp, equity, cash, position_count)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                payload,
            )
        return len(payload)

    def save_run_summary(
        self,
        run_id: str,
        symbols: list[str],
        strategy_name: str,
        starting_cash: float,
        final_equity: float,
        metrics_dict: dict,
        interval: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        data_sources: dict | None = None,
        params: dict | None = None,
        costs: dict | None = None,
        risk: dict | None = None,
        reconciliation_problems: list[str] | None = None,
        signal_count: int | None = None,
        expired_signal_count: int | None = None,
        ambiguous_trade_count: int | None = None,
        notes: str | None = None,
    ) -> None:
        """Stores a run summary.

        CLAUDE.md section 8 requires the date range, symbols, interval, data
        source, costs and assumptions to be reported alongside every result,
        so they are columns here rather than context the reader has to
        remember.
        """

        now = datetime.now().isoformat()
        self._conn.execute(
            """INSERT OR REPLACE INTO runs
               (run_id, started_at, symbols, strategy_name, starting_cash, final_equity,
                metrics_json, finished_at, interval, start_date, end_date,
                data_source_json, params_json, costs_json, risk_json,
                reconciliation_json, signal_count, expired_signal_count,
                ambiguous_trade_count, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, now, json.dumps(symbols), strategy_name, starting_cash,
                final_equity, json.dumps(metrics_dict, default=str), now, interval,
                start_date, end_date,
                json.dumps(data_sources or {}, default=str),
                json.dumps(params or {}, default=str),
                json.dumps(costs or {}, default=str),
                json.dumps(risk or {}, default=str),
                json.dumps(reconciliation_problems or [], default=str),
                signal_count, expired_signal_count, ambiguous_trade_count, notes,
            ),
        )
        self._conn.commit()

    def log_event(
        self,
        severity: str,
        component: str,
        message: str,
        run_id: str | None = None,
        detail: dict | None = None,
    ) -> None:
        """Records an application event for the health page. Keep secrets out
        of `message`/`detail`; this table is readable from the dashboard."""

        self._conn.execute(
            """INSERT INTO app_events (ts, severity, component, message, run_id, detail_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(), severity.upper(), component, message,
                run_id, json.dumps(detail or {}, default=str),
            ),
        )
        self._conn.commit()

    # ------------------------------------------------------------ ledger read

    def all_trades(self) -> list[sqlite3.Row]:
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute("SELECT * FROM trades ORDER BY exit_at")
        return cur.fetchall()

    def trade_count(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM trades")
        return cur.fetchone()[0]

    def table_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table in EXPLORABLE_TABLES:
            cur = self._conn.execute(f'SELECT COUNT(*) FROM "{table}"')
            counts[table] = cur.fetchone()[0]
        return counts

    def last_write_at(self) -> str | None:
        """Most recent recorded_at across the write-tracked tables, or None if
        the ledger has never been written to."""

        stamps: list[str] = []
        for table in ("trades", "orders", "signals", "positions"):
            cur = self._conn.execute(f'SELECT MAX(recorded_at) FROM "{table}"')
            value = cur.fetchone()[0]
            if value:
                stamps.append(value)
        cur = self._conn.execute("SELECT MAX(ts) FROM app_events")
        value = cur.fetchone()[0]
        if value:
            stamps.append(value)
        return max(stamps) if stamps else None

    # ----------------------------------------------------------------- reset

    def reset_ledger(self, confirmation: str) -> dict[str, int]:
        """Deletes all simulated history. Requires the exact confirmation
        token, so an accidental or programmatic call cannot erase the ledger.

        This is the ONLY destructive operation in the codebase. It is not
        exposed through the dashboard API at all — it is CLI-only and
        interactive (see app/cli.py). Returns the row counts that were
        deleted, and records an app_event noting the reset so the fact of the
        deletion itself is not lost.
        """

        if confirmation != RESET_CONFIRMATION_TOKEN:
            raise ValueError(
                "reset_ledger requires the exact confirmation token "
                f"{RESET_CONFIRMATION_TOKEN!r}; nothing was deleted"
            )

        deleted = self.table_counts()
        with self._conn:
            for table in EXPLORABLE_TABLES:
                if table == "app_events":
                    continue  # keep the audit trail of the reset itself
                self._conn.execute(f'DELETE FROM "{table}"')
        self.log_event(
            severity="WARNING",
            component="storage",
            message="ledger reset: all simulated trades/orders/signals deleted",
            detail=deleted,
        )
        return deleted


def open_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Opens the ledger read-only via SQLite's `mode=ro` URI flag.

    Used by the dashboard API so a read path physically cannot write, even if
    a query were wrong. Raises sqlite3.OperationalError if the file does not
    exist — `mode=ro` deliberately will not create one.

    `check_same_thread=False` is required, not a shortcut: FastAPI runs a sync
    generator dependency and the endpoint that consumes it on DIFFERENT
    threadpool threads, so a connection created in the dependency is used in
    the endpoint and sqlite3's default same-thread guard rejects it. It is safe
    here because each request gets its OWN connection and uses it
    sequentially — create, query, close — so no connection is ever touched by
    two threads at once. Do not share a connection returned by this function
    between concurrent requests.

    `busy_timeout` makes a reader wait briefly rather than fail outright if a
    backtest is mid-write when the dashboard reads.
    """

    path = Path(db_path).resolve()
    conn = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro", uri=True, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}
