"""The scanner's virtual portfolio must survive a process restart.

Each "restart" below builds a brand-new Scanner/PaperBroker/Portfolio from the
ledger file alone, exactly as a second `python -m app.cli scan` would.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.config import AppConfig
from app.models import ChargeProfile, OrderStatus, Signal, SignalAction
from app.paper.broker import PaperBroker
from app.paper.portfolio import Portfolio
from app.scanner.scanner import ScanOutcome, Scanner
from app.scanner.universe import Watchlist
from app.storage.scanner_state import ScannerStateStore
from tests.test_scanner import AlwaysBuyStrategy, ListProvider, TRADING_DAY, bar_at

IST = ZoneInfo("Asia/Kolkata")


def boot(db_path, bars, strategy=None, config=None):
    """Simulates process start: load whatever the ledger holds."""
    config = config or AppConfig()
    store = ScannerStateStore(db_path)
    state = store.load_portfolio_state()
    broker = PaperBroker(config, portfolio=Portfolio.from_state(state)) if state else PaperBroker(config)
    scanner = Scanner(
        provider=ListProvider(bars), strategy=strategy or AlwaysBuyStrategy(),
        broker=broker, watchlist=Watchlist.from_list(list(bars)),
    )
    scanner.restore_processed(store.load_processed())
    return store, broker, scanner


def shutdown(store, broker, scanner):
    """Simulates the end of a scan: save, then the process exits."""
    store.save_portfolio_state(broker.portfolio.export_state())
    store.save_processed(scanner.processed_bars)
    store.close()


def test_first_run_has_no_saved_state(tmp_path):
    store = ScannerStateStore(tmp_path / "l.db")
    assert store.load_portfolio_state() is None
    assert store.load_processed() == set()
    store.close()


def test_cash_and_open_position_survive_restart(tmp_path):
    db = tmp_path / "l.db"
    bars = {"TCS": [bar_at("TCS", TRADING_DAY - timedelta(days=1), close=100)]}

    store, broker, scanner = boot(db, bars)
    report = scanner.scan_once(TRADING_DAY)
    assert report.count(ScanOutcome.TRADED) == 1
    cash_after_buy = broker.portfolio.cash
    qty = broker.portfolio.positions["TCS"].quantity
    shutdown(store, broker, scanner)

    store2, broker2, _ = boot(db, bars)
    assert broker2.portfolio.cash == pytest.approx(cash_after_buy)
    assert broker2.portfolio.cash < broker2.portfolio.starting_cash  # money really left
    assert "TCS" in broker2.portfolio.positions
    assert broker2.portfolio.positions["TCS"].quantity == qty
    store2.close()


def test_without_persistence_each_run_would_start_flat(tmp_path):
    """The documented limitation this feature removes, kept as a control."""
    fresh = PaperBroker(AppConfig())
    assert fresh.portfolio.positions == {}
    assert fresh.portfolio.cash == fresh.portfolio.starting_cash


def test_same_bar_is_not_traded_again_after_restart(tmp_path):
    """Cross-process idempotency: the old per-process-only gap."""
    db = tmp_path / "l.db"
    bars = {"TCS": [bar_at("TCS", TRADING_DAY - timedelta(days=1))]}

    store, broker, scanner = boot(db, bars)
    assert scanner.scan_once(TRADING_DAY).count(ScanOutcome.TRADED) == 1
    shutdown(store, broker, scanner)

    store2, broker2, scanner2 = boot(db, bars)
    report = scanner2.scan_once(TRADING_DAY + timedelta(minutes=5))
    assert report.count(ScanOutcome.SKIPPED_DUPLICATE) == 1
    assert report.count(ScanOutcome.TRADED) == 0
    assert len([o for o in broker2.orders if o.status == OrderStatus.FILLED]) == 0
    store2.close()


def test_charge_profile_is_restored_so_exit_is_costed_correctly(tmp_path):
    """A restored INTRADAY position that came back as DELIVERY would have its
    first exit charged under the wrong schedule."""
    db = tmp_path / "l.db"
    broker = PaperBroker(AppConfig())
    broker.portfolio.apply_fill_buy(
        "TCS", 10, 100.0, 5.0, TRADING_DAY, 95.0, 110.0, "o1",
        charge_profile=ChargeProfile.INTRADAY,
    )
    store = ScannerStateStore(db)
    store.save_portfolio_state(broker.portfolio.export_state())
    store.close()

    store2 = ScannerStateStore(db)
    restored = Portfolio.from_state(store2.load_portfolio_state())
    store2.close()

    assert restored.positions["TCS"].charge_profile == ChargeProfile.INTRADAY
    trade = restored.apply_fill_sell("TCS", 101.0, 5.0, TRADING_DAY, "x", "o2")
    assert trade.charge_profile == ChargeProfile.INTRADAY


def test_position_fields_round_trip_exactly(tmp_path):
    p = Portfolio(100_000)
    p.apply_fill_buy("TCS", 7, 123.45, 6.78, TRADING_DAY, 110.0, 150.0, "ord-1")
    store = ScannerStateStore(tmp_path / "l.db")
    store.save_portfolio_state(p.export_state())
    back = store.load_portfolio_state()["positions"][0]
    store.close()
    orig = p.positions["TCS"]
    assert (back.symbol, back.quantity, back.avg_entry_price) == ("TCS", 7, 123.45)
    assert (back.stop_loss, back.target, back.entry_costs) == (110.0, 150.0, 6.78)
    assert back.entry_order_id == "ord-1"
    assert back.opened_at == orig.opened_at


def test_realized_pnl_reconciles_after_restore(tmp_path):
    """Trades are not reloaded, so reconcile() must account for carried-forward P&L."""
    p = Portfolio(100_000)
    p.apply_fill_buy("TCS", 10, 100.0, 5.0, TRADING_DAY, 90.0, 120.0, "o1")
    p.apply_fill_sell("TCS", 110.0, 5.0, TRADING_DAY, "x", "o2")
    assert p.realized_pnl != 0
    assert p.reconcile({}) == []

    restored = Portfolio.from_state(p.export_state())
    assert restored.trades == []
    assert restored.realized_pnl == pytest.approx(p.realized_pnl)
    assert restored.reconcile({}) == []

    # A new trade after restore still reconciles.
    restored.apply_fill_buy("INFY", 5, 50.0, 2.0, TRADING_DAY, 45.0, 60.0, "o3")
    restored.apply_fill_sell("INFY", 48.0, 2.0, TRADING_DAY, "x", "o4")
    assert restored.reconcile({}) == []


def test_daily_loss_baseline_survives_restart(tmp_path):
    """Otherwise every new process would reset the max-daily-loss baseline."""
    p = Portfolio(100_000)
    p.mark_new_day(TRADING_DAY.date(), {})
    p.cash -= 2_000  # a loss earlier today
    back = Portfolio.from_state(p.export_state())
    back.mark_new_day(TRADING_DAY.date(), {})  # same day: must NOT re-baseline
    assert back.day_start_equity == pytest.approx(100_000)
    assert back.daily_pnl({}) == pytest.approx(-2_000)

    back.mark_new_day(TRADING_DAY.date() + timedelta(days=1), {})  # next day: re-baselines
    assert back.day_start_equity == pytest.approx(98_000)


def test_save_replaces_positions_rather_than_accumulating(tmp_path):
    store = ScannerStateStore(tmp_path / "l.db")
    p = Portfolio(100_000)
    p.apply_fill_buy("TCS", 5, 100.0, 1.0, TRADING_DAY, 90.0, 120.0, "o1")
    store.save_portfolio_state(p.export_state())
    p.apply_fill_sell("TCS", 101.0, 1.0, TRADING_DAY, "x", "o2")
    store.save_portfolio_state(p.export_state())  # position now closed
    assert store.load_portfolio_state()["positions"] == []
    store.close()


def test_save_is_atomic_when_a_write_fails(tmp_path):
    """A crash mid-save must not pair new cash with old positions."""
    store = ScannerStateStore(tmp_path / "l.db")
    p = Portfolio(100_000)
    p.apply_fill_buy("TCS", 5, 100.0, 1.0, TRADING_DAY, 90.0, 120.0, "o1")
    store.save_portfolio_state(p.export_state())
    good = store.load_portfolio_state()

    bad_state = p.export_state()
    bad_state["cash"] = 1.0
    bad_state["positions"] = [object()]  # explodes mid-transaction
    with pytest.raises(Exception):
        store.save_portfolio_state(bad_state)

    after = store.load_portfolio_state()
    assert after["cash"] == pytest.approx(good["cash"])
    assert [x.symbol for x in after["positions"]] == ["TCS"]
    store.close()


def test_clear_deletes_everything_and_reports_counts(tmp_path):
    store = ScannerStateStore(tmp_path / "l.db")
    p = Portfolio(100_000)
    p.apply_fill_buy("TCS", 5, 100.0, 1.0, TRADING_DAY, 90.0, 120.0, "o1")
    store.save_portfolio_state(p.export_state())
    store.save_processed({("TCS", TRADING_DAY)})

    deleted = store.clear()
    assert deleted["scanner_portfolio"] == 1
    assert deleted["scanner_positions"] == 1
    assert deleted["scanner_processed"] == 1
    assert store.load_portfolio_state() is None
    assert store.load_processed() == set()
    store.close()


def test_state_survives_alongside_the_main_ledger_schema(tmp_path):
    """Sharing one db file with SqliteRepository must not clash."""
    from app.storage.sqlite import SqliteRepository

    db = tmp_path / "shared.db"
    repo = SqliteRepository(db)
    store = ScannerStateStore(db)
    p = Portfolio(100_000)
    store.save_portfolio_state(p.export_state())
    assert repo.trade_count() == 0
    assert store.load_portfolio_state()["cash"] == 100_000
    store.close()
    repo.close()


# --- end to end through the real CLI, in separate processes ------------------


def _run_cli(*args):
    import subprocess

    root = Path(__file__).resolve().parent.parent
    return subprocess.run(
        [sys.executable, "-m", "app.cli", *args],
        cwd=root, capture_output=True, text=True, timeout=120,
    )


def test_cli_resumes_across_real_processes(tmp_path):
    db = str(tmp_path / "cli.db")
    base = ["scan", "--symbols", "SAMPLE_TCS,SAMPLE_INFY", "--db", db]

    first = _run_cli(*base, "--now", "2024-09-10T11:00:00+05:30")
    assert first.returncode == 0, first.stdout + first.stderr
    assert "starting fresh" in first.stdout

    second = _run_cli(*base, "--now", "2024-09-10T11:05:00+05:30")
    assert second.returncode == 0, second.stdout + second.stderr
    assert "RESUMED virtual portfolio" in second.stdout
    # Same newest bar both times: the second process must not re-evaluate it.
    assert "SKIPPED_DUPLICATE" in second.stdout


def test_cli_reset_erases_scanner_state_too(tmp_path):
    db = str(tmp_path / "cli.db")
    _run_cli("scan", "--symbols", "SAMPLE_TCS", "--db", db, "--now", "2024-09-10T11:00:00+05:30")
    store = ScannerStateStore(db)
    assert store.row_counts()["scanner_portfolio"] == 1
    store.close()

    reset = _run_cli("reset", "--db", db, "--yes")
    assert reset.returncode == 0, reset.stdout + reset.stderr

    store = ScannerStateStore(db)
    assert store.row_counts() == {
        "scanner_portfolio": 0, "scanner_positions": 0, "scanner_processed": 0,
    }
    store.close()

    third = _run_cli("scan", "--symbols", "SAMPLE_TCS", "--db", db, "--now", "2024-09-10T11:00:00+05:30")
    assert "starting fresh" in third.stdout
