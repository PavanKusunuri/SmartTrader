"""Tests for the local dashboard API.

The safety-critical property here is that the dashboard is READ-ONLY: it must
be impossible to place an order, write to the ledger, or run caller-supplied
SQL through it. Those tests come first; cosmetics come later.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.backtest.engine import run_backtest
from app.config import AppConfig
from app.data.csv_provider import CsvProvider
from app.storage.persist import persist_run
from app.storage.sqlite import SqliteRepository
from app.strategies.swing import SwingMAStrategy

from pathlib import Path

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"


@pytest.fixture(scope="module")
def populated_db(tmp_path_factory) -> str:
    """A ledger with one real backtest run in it, built on the CURRENT schema."""
    db_path = tmp_path_factory.mktemp("ledger") / "dash.db"
    config = AppConfig()
    result = run_backtest(
        provider=CsvProvider(SAMPLES_DIR),
        symbols=["SAMPLE_TCS", "SAMPLE_INFY"],
        strategy=SwingMAStrategy(config.swing),
        config=config,
    )
    repo = SqliteRepository(db_path)
    persist_run(
        repo=repo, run_id="test-run-1", result=result, config=config,
        strategy_name="swing_ma_crossover",
        strategy_params=config.swing.model_dump(),
    )
    repo.close()
    return str(db_path)


@pytest.fixture
def client(populated_db) -> TestClient:
    return TestClient(create_app(db_path=populated_db, samples_dir=str(SAMPLES_DIR)))


# --- Safety: read-only, no order placement ----------------------------------


def test_every_route_is_read_only():
    """No route may accept a method that could mutate state."""
    app = create_app(db_path="unused.db", samples_dir=str(SAMPLES_DIR))
    mutating = {"POST", "PUT", "PATCH", "DELETE"}
    offenders = []
    for route in app.routes:
        methods = getattr(route, "methods", set()) or set()
        if methods & mutating:
            offenders.append((getattr(route, "path", "?"), sorted(methods & mutating)))
    assert offenders == [], f"dashboard exposes mutating routes: {offenders}"


def test_no_route_mentions_order_placement():
    app = create_app(db_path="unused.db", samples_dir=str(SAMPLES_DIR))
    paths = [getattr(r, "path", "") for r in app.routes]
    forbidden = ("place", "submit", "execute", "buy", "sell", "cancel", "modify")
    assert [p for p in paths if any(word in p.lower() for word in forbidden)] == []


def test_status_declares_paper_mode_and_no_live_orders(client):
    body = client.get("/api/status").json()
    assert body["live_orders_possible"] is False
    assert "PAPER TRADING" in body["mode"]


def test_mutating_methods_are_rejected_on_real_endpoints(client):
    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)("/api/trades")
        assert response.status_code in (404, 405), (
            f"{method.upper()} /api/trades returned {response.status_code}"
        )


def test_dashboard_opens_database_read_only(populated_db):
    """Writes through the dashboard's own connection must fail."""
    from app.storage.sqlite import open_readonly

    conn = open_readonly(populated_db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM trades")
    conn.close()


# --- Core endpoints ---------------------------------------------------------


def test_core_endpoints_return_200(client):
    for path in [
        "/api/health", "/api/status", "/api/overview", "/api/trades",
        "/api/signals", "/api/orders", "/api/positions", "/api/risk",
        "/api/runs", "/api/strategies", "/api/events", "/api/db/tables",
    ]:
        response = client.get(path)
        assert response.status_code == 200, f"{path} -> {response.status_code} {response.text[:200]}"


def test_index_page_shows_paper_trading_banner(client):
    html = client.get("/").text
    assert "PAPER TRADING" in html
    assert "NO REAL ORDERS" in html


def test_trades_endpoint_returns_the_persisted_trades(client):
    body = client.get("/api/trades").json()
    assert "rows" in body
    assert len(body["rows"]) >= 1
    row = body["rows"][0]
    for field in ("trade_id", "symbol", "entry_price", "exit_price", "net_pnl"):
        assert field in row


def test_net_and_gross_are_both_exposed(client):
    """CLAUDE.md requires net and gross reported separately, not just net."""
    overview = client.get("/api/overview").json()
    blob = str(overview).lower()
    assert "gross" in blob and "net" in blob


def test_risk_endpoint_exposes_limits_and_kill_switch(client):
    body = client.get("/api/risk").json()
    limits = body["configured_limits"]
    assert "max_daily_loss_pct" in limits
    assert "kill_switch_enabled" in limits


def test_health_reports_unimplemented_components_honestly(client):
    """Features that don't exist must say so, never report HEALTHY."""
    components = {c["name"]: c["status"] for c in client.get("/api/health").json()["components"]}
    assert components["Alert delivery (Telegram)"] == "NOT_IMPLEMENTED"
    assert components["Live/delayed market data feed"] == "NOT_IMPLEMENTED"


def test_scanner_health_is_derived_from_actual_scan_history(client, tmp_path):
    """The scanner exists, so its health must reflect whether it has run
    against THIS ledger — not a hardcoded status."""
    # The populated ledger has backtest runs but no scanner events.
    components = {c["name"]: c for c in client.get("/api/health").json()["components"]}
    assert components["Scanner"]["status"] == "UNKNOWN"
    assert "has not run" in components["Scanner"]["detail"]

    # After a scanner event is recorded, it must report HEALTHY.
    db_path = tmp_path / "scanned.db"
    repo = SqliteRepository(db_path)
    repo.log_event(
        severity="INFO", component="scanner",
        message="scan completed: 2 symbols", detail={"traded": 0},
    )
    repo.close()

    scanned_client = TestClient(create_app(db_path=str(db_path), samples_dir=str(SAMPLES_DIR)))
    scanner = {
        c["name"]: c for c in scanned_client.get("/api/health").json()["components"]
    }["Scanner"]
    assert scanner["status"] == "HEALTHY"
    assert "scan completed" in scanner["detail"]


def test_csv_exports_are_downloadable(client):
    for path in ["/api/export/trades.csv", "/api/export/orders.csv", "/api/export/signals.csv"]:
        response = client.get(path)
        assert response.status_code == 200, path
        assert "text/csv" in response.headers["content-type"]
        assert response.text.count("\n") >= 1  # at least a header row


# --- Hostile input ----------------------------------------------------------


def test_unknown_table_is_rejected_not_executed(client):
    response = client.get("/api/db/tables/sqlite_master")
    assert response.status_code == 400


def test_sql_injection_in_table_name_is_rejected(client):
    for payload in ["trades; DROP TABLE trades", "trades'--", "../../etc/passwd"]:
        response = client.get(f"/api/db/tables/{payload}")
        assert response.status_code in (400, 404), f"{payload} -> {response.status_code}"


def test_sql_injection_in_sort_column_is_rejected(client):
    response = client.get("/api/trades", params={"sort": "net_pnl; DROP TABLE trades"})
    assert response.status_code == 400
    # Prove the table is still there afterwards.
    assert client.get("/api/trades").status_code == 200


def test_trades_table_still_intact_after_hostile_requests(client):
    before = len(client.get("/api/trades").json()["rows"])
    client.get("/api/db/tables/trades;DROP TABLE trades")
    client.get("/api/trades", params={"sort": "1=1"})
    after = len(client.get("/api/trades").json()["rows"])
    assert before == after


def test_absurd_paging_is_clamped_not_crashed(client):
    for params in [{"page": 0}, {"page": -5}, {"page_size": 10**9}, {"page_size": -1}]:
        response = client.get("/api/trades", params=params)
        assert response.status_code in (200, 400), f"{params} -> {response.status_code}"


# --- Degraded states --------------------------------------------------------


def test_missing_database_is_reported_not_crashed(tmp_path):
    """A missing ledger must give a usable dashboard that says what is wrong."""
    missing = tmp_path / "does_not_exist.db"
    client = TestClient(create_app(db_path=str(missing), samples_dir=str(SAMPLES_DIR)))

    # The page itself must still render so the user can read the problem.
    assert client.get("/").status_code == 200
    # Status does not depend on the ledger and must stay available.
    assert client.get("/api/status").status_code == 200

    health = client.get("/api/health")
    assert health.status_code in (200, 503)
    assert "error" in health.text.lower() or "degraded" in health.text.lower()


def test_empty_but_valid_database_reports_no_data(tmp_path):
    db_path = tmp_path / "empty.db"
    SqliteRepository(db_path).close()  # creates the schema, writes no rows
    client = TestClient(create_app(db_path=str(db_path), samples_dir=str(SAMPLES_DIR)))

    overview = client.get("/api/overview")
    assert overview.status_code == 200
    assert overview.json()["has_data"] is False
    assert client.get("/api/trades").json()["rows"] == []


def test_outdated_schema_database_does_not_crash_the_dashboard(tmp_path):
    """A ledger from an older build is missing tables the dashboard reads.
    That must surface as a clear degraded state, not an opaque crash."""
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE trades (trade_id TEXT PRIMARY KEY, symbol TEXT)")
    conn.commit()
    conn.close()

    client = TestClient(create_app(db_path=str(db_path), samples_dir=str(SAMPLES_DIR)))
    assert client.get("/").status_code == 200
    assert client.get("/api/status").status_code == 200
    # Whatever the data endpoints do, they must not return a 500.
    for path in ["/api/health", "/api/overview", "/api/trades"]:
        assert client.get(path).status_code != 500, path
