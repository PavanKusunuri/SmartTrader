"""Tests for the ledger schema migration and run persistence.

These cover the guarantees the dashboard depends on and that a user's recorded
history depends on:
  - opening an older ledger with a newer build ADDS columns and loses no rows
  - the reset path cannot fire without an exact confirmation token
  - a read-only connection physically cannot write
  - every persisted order traces back to a persisted, explained signal
  - a NO_TRADE decision is recorded as such rather than dropped
  - an open position with no available price stores NULL, not the entry price
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.backtest.engine import run_backtest
from app.config import AppConfig, RiskLimits, SwingStrategyConfig
from app.data.base import DataProvider
from app.models import (
    Bar,
    DataQuality,
    OrderStatus,
    Position,
    Signal,
    SignalAction,
)
from app.storage.persist import persist_run
from app.storage.sqlite import (
    RESET_CONFIRMATION_TOKEN,
    SqliteRepository,
    open_readonly,
)
from app.strategies.base import Strategy

IST = ZoneInfo("Asia/Kolkata")


class ListProvider(DataProvider):
    def __init__(self, bars_by_symbol):
        self.bars_by_symbol = bars_by_symbol

    def get_bars(self, symbol, interval="1d", start=None, end=None):
        return list(self.bars_by_symbol.get(symbol, []))


class ScriptedStrategy(Strategy):
    name = "scripted"

    def __init__(self, actions, stop=None, target=None):
        self.actions = actions
        self.stop = stop
        self.target = target

    def evaluate(self, bars, position=None):
        idx = len(bars) - 1
        latest = bars[-1]
        action = SignalAction(self.actions.get(idx, "NO_TRADE"))
        return Signal(
            signal_id=f"scripted:{latest.symbol}:{latest.timestamp.isoformat()}",
            symbol=latest.symbol,
            timestamp=latest.timestamp,
            strategy_name=self.name,
            action=action,
            reason=f"scripted {action.value} at index {idx}",
            inputs={"idx": idx, "close": latest.close},
            stop_loss=self.stop if action == SignalAction.BUY else None,
            target=self.target if action == SignalAction.BUY else None,
        )


def daily(symbol, day, o, h, l, c, quality=DataQuality.OK) -> Bar:
    dt = datetime(2024, 1, day, tzinfo=IST)
    return Bar(
        symbol=symbol, timestamp=dt, open=o, high=h, low=l, close=c, volume=1000,
        source="test-double", retrieved_at=dt, interval="1d", quality=quality,
    )


def config():
    return AppConfig(
        starting_cash=100_000,
        risk=RiskLimits(max_position_pct=1.0),
        swing=SwingStrategyConfig(fast_ma_period=2, slow_ma_period=4),
    )


# A stop 5 below a Rs 100 entry sizes the position at ~200 shares on the
# default 1%-of-cash risk budget, i.e. about Rs 20,000 of turnover. That clears
# the cost-viability gate in app/paper/viability.py, which refuses entries whose
# round-trip charges would exceed a configured fraction of order value. A wider
# stop sizes the order smaller and the entry is (correctly) rejected as
# uneconomic, so tests that need a real fill must use a viable setup.
VIABLE_STOP = 95.0


def run_and_persist(tmp_path, bars, actions, stop=None, target=None, name="ledger.db"):
    cfg = config()
    strategy = ScriptedStrategy(actions, stop=stop, target=target)
    result = run_backtest(
        provider=ListProvider({"X": bars}), symbols=["X"], strategy=strategy, config=cfg
    )
    repo = SqliteRepository(tmp_path / name)
    counts = persist_run(
        repo=repo, run_id="run-1", result=result, config=cfg,
        strategy_name=strategy.name, strategy_params={"fast": 2},
    )
    return repo, result, counts


# ------------------------------------------------------------------ migration


OLD_SCHEMA = """
CREATE TABLE trades (
    trade_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, entry_order_id TEXT NOT NULL,
    exit_order_id TEXT NOT NULL, entry_price REAL NOT NULL, exit_price REAL NOT NULL,
    quantity INTEGER NOT NULL, entry_at TEXT NOT NULL, exit_at TEXT NOT NULL,
    gross_pnl REAL NOT NULL, costs REAL NOT NULL, net_pnl REAL NOT NULL,
    exit_reason TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY, signal_id TEXT NOT NULL, symbol TEXT NOT NULL,
    side TEXT NOT NULL, quantity INTEGER NOT NULL, submitted_at TEXT NOT NULL,
    status TEXT NOT NULL, filled_price REAL, filled_quantity INTEGER, filled_at TEXT,
    rejection_reason TEXT, recorded_at TEXT NOT NULL
);
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, symbols TEXT NOT NULL,
    strategy_name TEXT NOT NULL, starting_cash REAL NOT NULL, final_equity REAL,
    metrics_json TEXT
);
"""


def make_legacy_db(path):
    """A ledger written by the pre-dashboard build: original three tables only."""
    import sqlite3

    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.execute(
        "INSERT INTO trades VALUES ('t-old','TCS','o1','o2',100,110,10,"
        "'2024-01-01T00:00:00+05:30','2024-01-05T00:00:00+05:30',100,5,95,'legacy','2024-01-05T10:00:00')"
    )
    conn.execute(
        "INSERT INTO runs VALUES ('r-old','2024-01-05T10:00:00','[\"TCS\"]','swing',100000,100095,'{}')"
    )
    conn.commit()
    conn.close()


def test_migration_preserves_legacy_rows_and_adds_columns(tmp_path):
    path = tmp_path / "legacy.db"
    make_legacy_db(path)

    repo = SqliteRepository(path)
    assert repo.trade_count() == 1, "migration must not drop existing trades"

    columns = {row[1] for row in repo._conn.execute('PRAGMA table_info("trades")')}
    assert {"run_id", "strategy_name", "side", "status", "ambiguous_exit"} <= columns

    # The legacy row keeps its data and gets NULL for the new columns, which the
    # API/UI must render as "not recorded" rather than inventing a value.
    row = repo._conn.execute(
        "SELECT net_pnl, run_id, strategy_name FROM trades WHERE trade_id='t-old'"
    ).fetchone()
    assert row[0] == 95
    assert row[1] is None
    assert row[2] is None
    repo.close()


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "legacy.db"
    make_legacy_db(path)
    for _ in range(3):
        repo = SqliteRepository(path)
        assert repo.trade_count() == 1
        repo.close()


def test_new_tables_are_created_on_a_legacy_db(tmp_path):
    path = tmp_path / "legacy.db"
    make_legacy_db(path)
    repo = SqliteRepository(path)
    counts = repo.table_counts()
    for table in ("signals", "positions", "equity_snapshots", "app_events"):
        assert table in counts
        assert counts[table] == 0
    repo.close()


# ------------------------------------------------------------------ read-only


def test_readonly_connection_cannot_write(tmp_path):
    import sqlite3

    path = tmp_path / "ledger.db"
    SqliteRepository(path).close()

    conn = open_readonly(path)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM trades")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DROP TABLE trades")
    conn.close()


def test_readonly_connection_is_usable_from_another_thread(tmp_path):
    """Regression: the dashboard served intermittent 503s because FastAPI runs
    a sync generator dependency and the endpoint consuming it on DIFFERENT
    threadpool threads. sqlite3's default same-thread guard then rejected the
    connection with ProgrammingError, which surfaced as 'The ledger could not
    be read' on whichever pages happened to land on another thread.

    Each request still gets its own connection and uses it sequentially, so
    crossing threads this way is safe — but it must be permitted.
    """
    import threading

    path = tmp_path / "ledger.db"
    SqliteRepository(path).close()

    conn = open_readonly(path)  # created on this thread
    result = {}

    def read_on_another_thread():
        try:
            result["rows"] = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        except Exception as exc:  # noqa: BLE001 - the failure mode under test
            result["error"] = f"{type(exc).__name__}: {exc}"

    thread = threading.Thread(target=read_on_another_thread)
    thread.start()
    thread.join()
    conn.close()

    assert "error" not in result, result.get("error")
    assert result["rows"] == 0


def test_readonly_open_refuses_to_create_a_missing_file(tmp_path):
    import sqlite3

    missing = tmp_path / "nope.db"
    with pytest.raises(sqlite3.OperationalError):
        open_readonly(missing)
    assert not missing.exists(), "mode=ro must not create a database file"


# ---------------------------------------------------------------------- reset


def test_reset_requires_the_exact_token_and_deletes_nothing_otherwise(tmp_path):
    bars = [daily("X", d, 100, 101, 99, 100) for d in range(1, 6)]
    repo, _, _ = run_and_persist(tmp_path, bars, {1: "BUY"}, stop=90, target=200)

    before = repo.table_counts()
    assert before["signals"] > 0

    for wrong in ("", "delete my paper trading history", "yes", "DROP"):
        with pytest.raises(ValueError):
            repo.reset_ledger(wrong)

    assert repo.table_counts() == before, "a failed confirmation must delete nothing"
    repo.close()


def test_reset_clears_history_but_keeps_the_audit_trail(tmp_path):
    bars = [daily("X", d, 100, 101, 99, 100) for d in range(1, 6)]
    repo, _, _ = run_and_persist(tmp_path, bars, {1: "BUY"}, stop=90, target=200)

    repo.reset_ledger(RESET_CONFIRMATION_TOKEN)
    counts = repo.table_counts()

    assert counts["trades"] == 0
    assert counts["orders"] == 0
    assert counts["signals"] == 0
    assert counts["equity_snapshots"] == 0
    # The reset itself must stay on record.
    assert counts["app_events"] > 0
    events = [
        row[0] for row in repo._conn.execute(
            "SELECT message FROM app_events ORDER BY event_id"
        )
    ]
    assert any("ledger reset" in m for m in events)
    repo.close()


# ---------------------------------------------------------------- persist_run


def test_persist_run_records_orders_signals_and_equity(tmp_path):
    bars = [
        daily("X", 1, 100, 101, 99, 100),
        daily("X", 2, 100, 101, 99, 100),   # BUY signalled
        daily("X", 3, 100, 101, 99, 100),   # fills at open
        daily("X", 4, 100, 101, 99, 100),
        daily("X", 5, 100, 101, 99, 100),   # SELL signalled
        daily("X", 6, 100, 101, 99, 100),   # exit fills
    ]
    repo, result, counts = run_and_persist(tmp_path, bars, {1: "BUY", 4: "SELL"}, stop=90, target=200)

    assert counts["orders"] == len(result.orders) > 0
    assert counts["signals"] == len(result.signals) > 0
    assert counts["equity_snapshots"] == len(result.equity_points) > 0

    stored = repo.table_counts()
    assert stored["orders"] == counts["orders"]
    assert stored["signals"] == counts["signals"]
    repo.close()


def test_every_persisted_order_has_a_matching_persisted_signal(tmp_path):
    """An order with no signal row would be an unexplained simulated trade."""
    bars = [
        daily("X", 1, 100, 101, 99, 100),
        daily("X", 2, 100, 101, 99, 100),
        daily("X", 3, 100, 101, 99, 100),
        daily("X", 4, 99, 100, 80, 85),     # breaches the stop -> engine exit
        daily("X", 5, 100, 101, 99, 100),
    ]
    repo, _, _ = run_and_persist(tmp_path, bars, {1: "BUY"}, stop=VIABLE_STOP, target=500)

    orphans = repo._conn.execute(
        "SELECT COUNT(*) FROM orders o WHERE NOT EXISTS "
        "(SELECT 1 FROM signals s WHERE s.signal_id = o.signal_id AND s.run_id = o.run_id)"
    ).fetchone()[0]
    assert orphans == 0

    # The engine-initiated stop exit must itself be recorded and explained.
    engine_signals = repo._conn.execute(
        "SELECT reason FROM signals WHERE strategy_name LIKE 'engine:%'"
    ).fetchall()
    assert engine_signals, "engine-initiated exits must be recorded as signals"
    assert any("stop" in r[0].lower() for r in engine_signals)
    repo.close()


def test_no_trade_decisions_are_recorded_with_outcome(tmp_path):
    bars = [daily("X", d, 100, 101, 99, 100) for d in range(1, 6)]
    repo, result, _ = run_and_persist(tmp_path, bars, {})  # never trades

    outcomes = dict(
        repo._conn.execute("SELECT outcome, COUNT(*) FROM signals GROUP BY outcome")
    )
    assert outcomes.get("NO_TRADE") == len(result.signals)
    assert repo.table_counts()["orders"] == 0
    repo.close()


def test_run_summary_records_the_assumptions_behind_the_result(tmp_path):
    bars = [daily("X", d, 100, 101, 99, 100) for d in range(1, 6)]
    repo, _, _ = run_and_persist(tmp_path, bars, {1: "BUY"}, stop=90, target=200)

    row = repo._conn.execute(
        "SELECT interval, start_date, end_date, params_json, costs_json, risk_json, "
        "data_source_json, signal_count FROM runs WHERE run_id='run-1'"
    ).fetchone()
    interval, start, end, params, costs, risk, sources, signal_count = row

    assert interval == "1d"
    assert start == "2024-01-01"
    assert end == "2024-01-05"
    assert "fast" in params
    assert "slippage_rate" in costs
    assert "max_open_positions" in risk
    assert "test-double" in sources
    assert signal_count > 0
    repo.close()


def test_trades_are_attributed_to_their_run(tmp_path):
    bars = [
        daily("X", 1, 100, 101, 99, 100),
        daily("X", 2, 100, 101, 99, 100),
        daily("X", 3, 100, 101, 99, 100),
        daily("X", 4, 100, 101, 99, 100),
        daily("X", 5, 100, 101, 99, 100),
        daily("X", 6, 100, 101, 99, 100),
    ]
    repo, result, _ = run_and_persist(
        tmp_path, bars, {1: "BUY", 4: "SELL"}, stop=VIABLE_STOP, target=200
    )
    assert result.trades, "scenario must produce a closed trade for this test to mean anything"

    rows = repo._conn.execute("SELECT run_id, strategy_name, side, status FROM trades").fetchall()
    assert rows
    for run_id, strategy_name, side, status in rows:
        assert run_id == "run-1"
        assert strategy_name == "scripted"
        assert side == "LONG"
        assert status == "CLOSED"
    repo.close()


def test_open_position_without_a_price_stores_null_not_the_entry_price(tmp_path):
    """Falling back to the entry price would report unrealised P&L of exactly
    zero, which reads as 'flat' when the truth is 'unknown'."""

    repo = SqliteRepository(tmp_path / "ledger.db")
    position = Position(
        symbol="X", quantity=10, avg_entry_price=100.0, stop_loss=90.0, target=120.0,
        opened_at=datetime(2024, 1, 1, tzinfo=IST), entry_order_id="o1", entry_costs=1.0,
    )
    repo.save_open_position(position, run_id="run-1", last_price=None)

    row = repo._conn.execute(
        "SELECT last_price, avg_entry_price FROM positions WHERE symbol='X'"
    ).fetchone()
    assert row[0] is None
    assert row[1] == 100.0
    repo.close()


def test_saving_the_same_run_twice_is_idempotent(tmp_path):
    """Re-persisting a run must not double its rows: signals are keyed by
    (run_id, signal_id) and orders/trades by their ids."""

    bars = [
        daily("X", 1, 100, 101, 99, 100),
        daily("X", 2, 100, 101, 99, 100),
        daily("X", 3, 100, 101, 99, 100),
        daily("X", 4, 100, 101, 99, 100),
    ]
    cfg = config()
    strategy = ScriptedStrategy({1: "BUY"}, stop=90, target=200)
    result = run_backtest(
        provider=ListProvider({"X": bars}), symbols=["X"], strategy=strategy, config=cfg
    )
    repo = SqliteRepository(tmp_path / "ledger.db")
    first = persist_run(repo=repo, run_id="run-1", result=result, config=cfg,
                        strategy_name=strategy.name)
    before = repo.table_counts()
    persist_run(repo=repo, run_id="run-1", result=result, config=cfg,
                strategy_name=strategy.name)
    after = repo.table_counts()

    assert after["signals"] == before["signals"] == first["signals"]
    assert after["orders"] == before["orders"]
    assert after["trades"] == before["trades"]
    assert after["equity_snapshots"] == before["equity_snapshots"]
    repo.close()


def test_reconciliation_problems_are_logged_as_an_error_event(tmp_path):
    bars = [daily("X", d, 100, 101, 99, 100) for d in range(1, 5)]
    cfg = config()
    strategy = ScriptedStrategy({}, stop=90)
    result = run_backtest(
        provider=ListProvider({"X": bars}), symbols=["X"], strategy=strategy, config=cfg
    )
    # Force a reconciliation problem so the logging path is exercised.
    result.reconciliation_problems = ["forced problem for test"]

    repo = SqliteRepository(tmp_path / "ledger.db")
    persist_run(repo=repo, run_id="run-1", result=result, config=cfg,
                strategy_name=strategy.name)

    severities = [
        r[0] for r in repo._conn.execute(
            "SELECT severity FROM app_events WHERE component='portfolio'"
        )
    ]
    assert "ERROR" in severities
    repo.close()
