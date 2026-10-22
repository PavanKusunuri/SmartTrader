import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from app.models import Order, OrderStatus, SignalAction, Trade
from app.storage.sqlite import SqliteRepository

IST = ZoneInfo("Asia/Kolkata")


def make_trade(net_pnl=100.0) -> Trade:
    return Trade(
        trade_id=str(uuid.uuid4()), symbol="TCS",
        entry_order_id="o1", exit_order_id="o2",
        entry_price=3500, exit_price=3600, quantity=10,
        entry_at=datetime(2024, 1, 1, tzinfo=IST), exit_at=datetime(2024, 1, 5, tzinfo=IST),
        gross_pnl=1000.0, costs=20.0, net_pnl=net_pnl, exit_reason="test",
    )


def test_trades_persist_across_reconnect(tmp_path):
    """Restarting the app must not erase simulated trades."""
    db_path = tmp_path / "ledger.db"

    repo = SqliteRepository(db_path)
    trade = make_trade()
    repo.save_trade(trade)
    assert repo.trade_count() == 1
    repo.close()

    # Simulate an app restart: brand-new connection to the same file.
    repo2 = SqliteRepository(db_path)
    assert repo2.trade_count() == 1
    rows = repo2.all_trades()
    assert rows[0]["trade_id"] == trade.trade_id
    assert rows[0]["net_pnl"] == trade.net_pnl
    repo2.close()


def test_saving_same_trade_twice_does_not_duplicate(tmp_path):
    repo = SqliteRepository(tmp_path / "ledger.db")
    trade = make_trade()
    repo.save_trade(trade)
    repo.save_trade(trade)  # idempotent re-processing must not double-count
    assert repo.trade_count() == 1
    repo.close()


def test_orders_persist(tmp_path):
    repo = SqliteRepository(tmp_path / "ledger.db")
    order = Order(
        order_id="o1", signal_id="s1", symbol="TCS", side=SignalAction.BUY,
        quantity=10, submitted_at=datetime(2024, 1, 1, tzinfo=IST),
        status=OrderStatus.FILLED, filled_price=3500.0, filled_quantity=10,
        filled_at=datetime(2024, 1, 1, tzinfo=IST),
    )
    repo.save_order(order)
    cur = repo._conn.execute("SELECT status, filled_price FROM orders WHERE order_id='o1'")
    row = cur.fetchone()
    assert row[0] == "FILLED"
    assert row[1] == 3500.0
    repo.close()


def test_rejected_order_records_reason(tmp_path):
    repo = SqliteRepository(tmp_path / "ledger.db")
    order = Order(
        order_id="o2", signal_id="s2", symbol="TCS", side=SignalAction.BUY,
        quantity=0, submitted_at=datetime(2024, 1, 1, tzinfo=IST),
        status=OrderStatus.REJECTED, rejection_reason="kill switch is enabled",
    )
    repo.save_order(order)
    cur = repo._conn.execute("SELECT rejection_reason FROM orders WHERE order_id='o2'")
    assert "kill switch" in cur.fetchone()[0]
    repo.close()


def test_run_summary_persists(tmp_path):
    repo = SqliteRepository(tmp_path / "ledger.db")
    repo.save_run_summary(
        run_id="r1", symbols=["TCS", "INFY"], strategy_name="swing_ma_crossover",
        starting_cash=100_000, final_equity=101_000, metrics_dict={"trade_count": 3},
    )
    cur = repo._conn.execute("SELECT symbols, final_equity FROM runs WHERE run_id='r1'")
    row = cur.fetchone()
    assert "TCS" in row[0]
    assert row[1] == 101_000
    repo.close()
