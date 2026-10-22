"""Parameterized read queries for the dashboard API.

Rules this module enforces, so the HTTP layer cannot get them wrong:

- Every value reaching SQL goes through a `?` placeholder. No f-string ever
  interpolates a user-supplied value into SQL.
- Table and column names cannot be user-supplied strings either: a table must
  be a member of `EXPLORABLE_TABLES`, and a sort column must appear in that
  table's actual `PRAGMA table_info` output. Anything else raises
  `InvalidQuery`, which the API turns into a 400.
- Filtering, sorting and pagination all happen in SQL, so the browser never
  receives a whole table.
- There is no endpoint, and no function here, that executes caller-supplied
  SQL.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Sequence

from app.storage.sqlite import EXPLORABLE_TABLES, row_to_dict

MAX_PAGE_SIZE = 500
DEFAULT_PAGE_SIZE = 50


class InvalidQuery(ValueError):
    """Caller asked for something not allowed (unknown table/column, bad page)."""


@dataclass
class Page:
    rows: list[dict[str, Any]]
    total: int
    page: int
    page_size: int

    @property
    def total_pages(self) -> int:
        if self.page_size <= 0:
            return 0
        return (self.total + self.page_size - 1) // self.page_size

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "total": self.total,
            "page": self.page,
            "page_size": self.page_size,
            "total_pages": self.total_pages,
        }


def clamp_paging(page: int, page_size: int) -> tuple[int, int]:
    if page < 1:
        raise InvalidQuery("page must be 1 or greater")
    if page_size < 1:
        raise InvalidQuery("page_size must be 1 or greater")
    return page, min(page_size, MAX_PAGE_SIZE)


def table_columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    """Column metadata for an allowlisted table. Raises InvalidQuery for any
    table not explicitly exposed, so a new internal table is never published
    by accident."""

    if table not in EXPLORABLE_TABLES:
        raise InvalidQuery(f"unknown or non-exposed table: {table!r}")
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    if not rows:
        raise InvalidQuery(f"table {table!r} does not exist in this database")
    return [
        {
            "name": r["name"],
            "type": r["type"] or "(untyped)",
            "notnull": bool(r["notnull"]),
            "primary_key": bool(r["pk"]),
        }
        for r in rows
    ]


def _validated_sort(
    conn: sqlite3.Connection, table: str, sort: str | None, allowed: Sequence[str],
    default: str,
) -> str:
    """Returns a safe ORDER BY column name. The column must be both in the
    endpoint's allowlist and a real column on the table."""

    column = sort or default
    if column not in allowed:
        raise InvalidQuery(
            f"cannot sort by {column!r}; allowed: {', '.join(sorted(allowed))}"
        )
    real = {c["name"] for c in table_columns(conn, table)}
    if column not in real:
        raise InvalidQuery(f"column {column!r} does not exist on {table!r}")
    return column


def _direction(order: str | None) -> str:
    value = (order or "desc").lower()
    if value not in ("asc", "desc"):
        raise InvalidQuery("order must be 'asc' or 'desc'")
    return value.upper()


def _paged(
    conn: sqlite3.Connection,
    base_from: str,
    where: list[str],
    params: list[Any],
    order_by: str,
    page: int,
    page_size: int,
    select: str = "*",
) -> Page:
    page, page_size = clamp_paging(page, page_size)
    clause = f" WHERE {' AND '.join(where)}" if where else ""

    total = conn.execute(
        f"SELECT COUNT(*) FROM {base_from}{clause}", params
    ).fetchone()[0]

    rows = conn.execute(
        f"SELECT {select} FROM {base_from}{clause} ORDER BY {order_by} "
        "LIMIT ? OFFSET ?",
        [*params, page_size, (page - 1) * page_size],
    ).fetchall()

    return Page([row_to_dict(r) for r in rows], total, page, page_size)


# --------------------------------------------------------------------- trades

TRADE_SORTS = (
    "exit_at", "entry_at", "net_pnl", "gross_pnl", "symbol", "quantity",
    "costs", "recorded_at",
)


def query_trades(
    conn: sqlite3.Connection,
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    search: str | None = None,
    symbol: str | None = None,
    strategy: str | None = None,
    status: str | None = None,
    direction: str | None = None,
    run_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort: str | None = None,
    order: str | None = None,
) -> Page:
    where: list[str] = []
    params: list[Any] = []

    if search:
        # Symbol or trade id, case-insensitive substring.
        where.append("(UPPER(symbol) LIKE ? OR trade_id LIKE ?)")
        params += [f"%{search.upper()}%", f"%{search}%"]
    if symbol:
        where.append("UPPER(symbol) = ?")
        params.append(symbol.upper())
    if strategy:
        where.append("strategy_name = ?")
        params.append(strategy)
    if status:
        where.append("COALESCE(status, 'CLOSED') = ?")
        params.append(status.upper())
    if direction:
        where.append("COALESCE(side, 'LONG') = ?")
        params.append(direction.upper())
    if run_id:
        where.append("run_id = ?")
        params.append(run_id)
    if date_from:
        where.append("date(exit_at) >= date(?)")
        params.append(date_from)
    if date_to:
        where.append("date(exit_at) <= date(?)")
        params.append(date_to)

    column = _validated_sort(conn, "trades", sort, TRADE_SORTS, "exit_at")
    return _paged(
        conn, "trades", where, params, f'"{column}" {_direction(order)}', page, page_size
    )


def get_trade(conn: sqlite3.Connection, trade_id: str) -> dict[str, Any] | None:
    """One trade plus its full lifecycle: the orders that opened/closed it and
    the signals behind those orders. Done in three indexed queries rather than
    per-row lookups, so the detail view is not an N+1."""

    row = conn.execute("SELECT * FROM trades WHERE trade_id = ?", (trade_id,)).fetchone()
    if row is None:
        return None
    trade = row_to_dict(row)

    orders = [
        row_to_dict(r)
        for r in conn.execute(
            "SELECT * FROM orders WHERE trade_id = ? OR order_id IN (?, ?) "
            "ORDER BY submitted_at",
            (trade_id, trade["entry_order_id"], trade["exit_order_id"]),
        )
    ]
    signal_ids = [o["signal_id"] for o in orders if o.get("signal_id")]
    signals: list[dict[str, Any]] = []
    if signal_ids:
        placeholders = ",".join("?" for _ in signal_ids)
        signals = [
            row_to_dict(r)
            for r in conn.execute(
                f"SELECT * FROM signals WHERE signal_id IN ({placeholders}) "
                "ORDER BY timestamp",
                signal_ids,
            )
        ]
    return {"trade": trade, "orders": orders, "signals": signals}


# -------------------------------------------------------------------- signals

SIGNAL_SORTS = ("timestamp", "symbol", "action", "outcome", "strategy_name", "recorded_at")


def query_signals(
    conn: sqlite3.Connection,
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    search: str | None = None,
    symbol: str | None = None,
    strategy: str | None = None,
    action: str | None = None,
    outcome: str | None = None,
    run_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort: str | None = None,
    order: str | None = None,
) -> Page:
    where: list[str] = []
    params: list[Any] = []

    if search:
        where.append("(UPPER(symbol) LIKE ? OR signal_id LIKE ? OR reason LIKE ?)")
        params += [f"%{search.upper()}%", f"%{search}%", f"%{search}%"]
    if symbol:
        where.append("UPPER(symbol) = ?")
        params.append(symbol.upper())
    if strategy:
        where.append("strategy_name = ?")
        params.append(strategy)
    if action:
        where.append("action = ?")
        params.append(action.upper())
    if outcome:
        where.append("COALESCE(outcome, '') = ?")
        params.append(outcome.upper())
    if run_id:
        where.append("run_id = ?")
        params.append(run_id)
    if date_from:
        where.append("date(timestamp) >= date(?)")
        params.append(date_from)
    if date_to:
        where.append("date(timestamp) <= date(?)")
        params.append(date_to)

    column = _validated_sort(conn, "signals", sort, SIGNAL_SORTS, "timestamp")
    return _paged(
        conn, "signals", where, params, f'"{column}" {_direction(order)}', page, page_size
    )


def get_signal(conn: sqlite3.Connection, run_id: str, signal_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM signals WHERE run_id = ? AND signal_id = ?", (run_id, signal_id)
    ).fetchone()
    if row is None:
        return None
    signal = row_to_dict(row)
    orders = [
        row_to_dict(r)
        for r in conn.execute(
            "SELECT * FROM orders WHERE signal_id = ? AND run_id = ? ORDER BY submitted_at",
            (signal_id, run_id),
        )
    ]
    trade = None
    if signal.get("trade_id"):
        trade_row = conn.execute(
            "SELECT * FROM trades WHERE trade_id = ?", (signal["trade_id"],)
        ).fetchone()
        trade = row_to_dict(trade_row) if trade_row else None
    return {"signal": signal, "orders": orders, "trade": trade}


def signal_action_counts(conn: sqlite3.Connection, run_id: str | None = None) -> dict[str, int]:
    where, params = ("WHERE run_id = ?", [run_id]) if run_id else ("", [])
    return {
        r["action"]: r["n"]
        for r in conn.execute(
            f"SELECT action, COUNT(*) AS n FROM signals {where} GROUP BY action", params
        )
    }


# --------------------------------------------------------------------- orders

ORDER_SORTS = ("submitted_at", "filled_at", "symbol", "status", "quantity", "recorded_at")


def query_orders(
    conn: sqlite3.Connection,
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    search: str | None = None,
    symbol: str | None = None,
    side: str | None = None,
    status: str | None = None,
    run_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort: str | None = None,
    order: str | None = None,
) -> Page:
    where: list[str] = []
    params: list[Any] = []

    if search:
        where.append("(UPPER(symbol) LIKE ? OR order_id LIKE ? OR signal_id LIKE ?)")
        params += [f"%{search.upper()}%", f"%{search}%", f"%{search}%"]
    if symbol:
        where.append("UPPER(symbol) = ?")
        params.append(symbol.upper())
    if side:
        where.append("side = ?")
        params.append(side.upper())
    if status:
        where.append("status = ?")
        params.append(status.upper())
    if run_id:
        where.append("run_id = ?")
        params.append(run_id)
    if date_from:
        where.append("date(submitted_at) >= date(?)")
        params.append(date_from)
    if date_to:
        where.append("date(submitted_at) <= date(?)")
        params.append(date_to)

    column = _validated_sort(conn, "orders", sort, ORDER_SORTS, "submitted_at")
    return _paged(
        conn, "orders", where, params, f'"{column}" {_direction(order)}', page, page_size
    )


# ------------------------------------------------------------------ positions


def query_positions(conn: sqlite3.Connection, run_id: str | None = None) -> list[dict[str, Any]]:
    where, params = ("WHERE run_id = ?", [run_id]) if run_id else ("", [])
    return [
        row_to_dict(r)
        for r in conn.execute(
            f"SELECT * FROM positions {where} ORDER BY symbol", params
        )
    ]


# ----------------------------------------------------------------------- runs

RUN_SORTS = ("started_at", "final_equity", "strategy_name", "start_date", "end_date")


def query_runs(
    conn: sqlite3.Connection,
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    strategy: str | None = None,
    sort: str | None = None,
    order: str | None = None,
) -> Page:
    where: list[str] = []
    params: list[Any] = []
    if strategy:
        where.append("strategy_name = ?")
        params.append(strategy)
    column = _validated_sort(conn, "runs", sort, RUN_SORTS, "started_at")
    return _paged(
        conn, "runs", where, params, f'"{column}" {_direction(order)}', page, page_size
    )


def latest_run(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
    return row_to_dict(row) if row else None


def get_run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return row_to_dict(row) if row else None


def equity_curve(conn: sqlite3.Connection, run_id: str, max_points: int = 1200) -> list[dict[str, Any]]:
    """The run's equity curve, thinned to at most `max_points` by taking every
    Nth row. Thinning is uniform sampling, not smoothing: no value shown is an
    average or an interpolation of recorded values.

    The final point is always included, so the last equity value is never
    dropped by the sampling stride.
    """

    total = conn.execute(
        "SELECT COUNT(*) FROM equity_snapshots WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    if total == 0:
        return []

    stride = max(1, -(-total // max_points))  # ceil division
    rows = conn.execute(
        "SELECT seq, timestamp, equity, cash, position_count FROM equity_snapshots "
        "WHERE run_id = ? AND (seq % ? = 0 OR seq = ?) ORDER BY seq",
        (run_id, stride, total - 1),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def run_trade_aggregates(conn: sqlite3.Connection, run_id: str | None = None) -> dict[str, Any]:
    """Trade-level aggregates computed in SQL.

    Formulas, stated so the dashboard numbers are checkable:
      gross_pnl  = SUM(gross_pnl)      -- before simulated costs
      costs      = SUM(costs)          -- entry + exit legs
      net_pnl    = SUM(net_pnl)        -- gross_pnl - costs, per trade
      win_rate   = COUNT(net_pnl > 0) / COUNT(*)
      avg_win    = AVG(net_pnl) over net_pnl > 0
      avg_loss   = AVG(net_pnl) over net_pnl <= 0   (a break-even trade counts
                   as a loss, matching app/backtest/metrics.py)
      expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    """

    where, params = ("WHERE run_id = ?", [run_id]) if run_id else ("", [])
    row = conn.execute(
        f"""SELECT
              COUNT(*) AS trade_count,
              COALESCE(SUM(gross_pnl), 0) AS gross_pnl,
              COALESCE(SUM(costs), 0) AS costs,
              COALESCE(SUM(net_pnl), 0) AS net_pnl,
              SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS win_count,
              SUM(CASE WHEN net_pnl <= 0 THEN 1 ELSE 0 END) AS loss_count,
              AVG(CASE WHEN net_pnl > 0 THEN net_pnl END) AS avg_win,
              AVG(CASE WHEN net_pnl <= 0 THEN net_pnl END) AS avg_loss,
              SUM(CASE WHEN ambiguous_exit = 1 THEN 1 ELSE 0 END) AS ambiguous_count
            FROM trades {where}""",
        params,
    ).fetchone()

    agg = row_to_dict(row)
    count = agg["trade_count"] or 0
    agg["win_rate"] = (agg["win_count"] / count) if count else None
    if count and agg["win_rate"] is not None:
        agg["expectancy"] = (
            agg["win_rate"] * (agg["avg_win"] or 0.0)
            + (1 - agg["win_rate"]) * (agg["avg_loss"] or 0.0)
        )
    else:
        agg["expectancy"] = None
    return agg


def max_drawdown(conn: sqlite3.Connection, run_id: str) -> float | None:
    """Max peak-to-trough drawdown of the recorded equity curve, as a positive
    fraction. None when fewer than two points exist, because a drawdown is not
    meaningful for a single observation.

    Reads the full curve (not the thinned one) so the trough is not sampled
    away. Mirrors app/backtest/metrics.compute_equity_curve_drawdown.
    """

    values = [
        r[0]
        for r in conn.execute(
            "SELECT equity FROM equity_snapshots WHERE run_id = ? ORDER BY seq", (run_id,)
        )
    ]
    if len(values) < 2:
        return None
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def distinct_values(conn: sqlite3.Connection, table: str, column: str) -> list[Any]:
    """Distinct non-null values for a filter dropdown. Both identifiers are
    validated against the allowlist and the real schema first."""

    real = {c["name"] for c in table_columns(conn, table)}
    if column not in real:
        raise InvalidQuery(f"column {column!r} does not exist on {table!r}")
    return [
        r[0]
        for r in conn.execute(
            f'SELECT DISTINCT "{column}" FROM "{table}" '
            f'WHERE "{column}" IS NOT NULL ORDER BY "{column}"'
        )
    ]


# ------------------------------------------------------------------ explorer


def browse_table(
    conn: sqlite3.Connection,
    table: str,
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    search: str | None = None,
    sort: str | None = None,
    order: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    symbol: str | None = None,
) -> dict[str, Any]:
    """Generic read-only browse over an allowlisted table.

    `search` matches across the table's TEXT columns only (numeric columns are
    excluded so a substring search cannot silently coerce types). Date filters
    apply only when the table actually has a recognised timestamp column.
    """

    columns = table_columns(conn, table)
    names = {c["name"] for c in columns}

    where: list[str] = []
    params: list[Any] = []

    if search:
        text_cols = [
            c["name"] for c in columns if (c["type"] or "").upper().startswith("TEXT")
        ]
        if text_cols:
            where.append(
                "(" + " OR ".join(f'"{c}" LIKE ?' for c in text_cols) + ")"
            )
            params += [f"%{search}%" for _ in text_cols]
        else:
            where.append("0 = 1")  # nothing searchable; return no rows, not all rows

    if symbol and "symbol" in names:
        where.append("UPPER(symbol) = ?")
        params.append(symbol.upper())

    date_column = next(
        (c for c in ("timestamp", "exit_at", "submitted_at", "ts", "started_at", "recorded_at")
         if c in names),
        None,
    )
    if date_from and date_column:
        where.append(f'date("{date_column}") >= date(?)')
        params.append(date_from)
    if date_to and date_column:
        where.append(f'date("{date_column}") <= date(?)')
        params.append(date_to)

    sort_column = sort or date_column or columns[0]["name"]
    if sort_column not in names:
        raise InvalidQuery(f"column {sort_column!r} does not exist on {table!r}")

    result = _paged(
        conn, f'"{table}"', where, params,
        f'"{sort_column}" {_direction(order)}', page, page_size,
    )
    return {
        "table": table,
        "columns": columns,
        "date_column": date_column,
        "sort": sort_column,
        "order": (order or "desc").lower(),
        **result.to_dict(),
    }


def latest_rows(conn: sqlite3.Connection, limit: int = 10) -> dict[str, list[dict[str, Any]]]:
    """Most recently written rows per table, for the explorer's "latest
    activity" panel."""

    out: dict[str, list[dict[str, Any]]] = {}
    for table in EXPLORABLE_TABLES:
        columns = {c["name"] for c in table_columns(conn, table)}
        order_col = next(
            (c for c in ("recorded_at", "ts", "started_at", "seq") if c in columns), None
        )
        if order_col is None:
            out[table] = []
            continue
        rows = conn.execute(
            f'SELECT * FROM "{table}" ORDER BY "{order_col}" DESC LIMIT ?', (limit,)
        ).fetchall()
        out[table] = [row_to_dict(r) for r in rows]
    return out


def recent_events(
    conn: sqlite3.Connection, limit: int = 100, severity: str | None = None
) -> list[dict[str, Any]]:
    where, params = "", []
    if severity:
        where = "WHERE severity = ?"
        params.append(severity.upper())
    return [
        row_to_dict(r)
        for r in conn.execute(
            f"SELECT * FROM app_events {where} ORDER BY ts DESC, event_id DESC LIMIT ?",
            [*params, limit],
        )
    ]
