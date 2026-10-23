"""Tests for the dashboard's query layer and derived values.

Covers the behaviour the UI relies on and the behaviour that keeps it honest:
filtering/sorting/pagination done in SQL, identifier allowlisting, aggregate
formulas, and the two places where a value is derived or deliberately absent
(order slippage/costs, and an open position with no price).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.api import queries as q
from app.api.derive import (
    cost_assumptions_from_run,
    derive_order_economics,
    position_marks,
)
from app.backtest.costs import compute_order_costs, compute_slippage
from app.config import CostAssumptions
from app.models import Order, OrderStatus, Signal, SignalAction, Trade
from app.storage.sqlite import SqliteRepository, open_readonly

IST = ZoneInfo("Asia/Kolkata")


def make_trade(trade_id, symbol, net_pnl, exit_day, quantity=10):
    return Trade(
        trade_id=trade_id, symbol=symbol, entry_order_id=f"e-{trade_id}",
        exit_order_id=f"x-{trade_id}", entry_price=100.0, exit_price=100.0 + net_pnl / quantity,
        quantity=quantity,
        entry_at=datetime(2024, 1, 1, tzinfo=IST),
        exit_at=datetime(2024, 1, exit_day, tzinfo=IST),
        gross_pnl=net_pnl + 5.0, costs=5.0, net_pnl=net_pnl, exit_reason="test",
    )


@pytest.fixture
def conn(tmp_path):
    """A ledger with a predictable mix of winners, losers and symbols."""
    repo = SqliteRepository(tmp_path / "ledger.db")
    repo.save_run_summary(
        run_id="run-a", symbols=["TCS", "INFY"], strategy_name="swing_ma_crossover",
        starting_cash=100_000, final_equity=100_300, metrics_dict={"trade_count": 4},
        interval="1d", start_date="2024-01-01", end_date="2024-01-20",
        costs=CostAssumptions().model_dump(), risk={"max_open_positions": 5},
        params={"fast_ma_period": 20}, data_sources={"TCS": "csv:TCS.csv"},
    )
    for trade_id, symbol, net, day in [
        ("t1", "TCS", 200.0, 5), ("t2", "TCS", -100.0, 10),
        ("t3", "INFY", 300.0, 15), ("t4", "INFY", -100.0, 20),
    ]:
        repo.save_trade(make_trade(trade_id, symbol, net, day), run_id="run-a",
                        strategy_name="swing_ma_crossover")

    for i, (action, outcome) in enumerate(
        [("BUY", "FILLED"), ("NO_TRADE", "NO_TRADE"), ("NO_TRADE", "NO_TRADE"), ("SELL", "FILLED")]
    ):
        repo.save_signal(
            Signal(
                signal_id=f"s{i}", symbol="TCS" if i % 2 == 0 else "INFY",
                timestamp=datetime(2024, 1, i + 1, tzinfo=IST),
                strategy_name="swing_ma_crossover", action=SignalAction(action),
                reason=f"reason {i}", inputs={"fast_ma": 10.5 + i},
            ),
            run_id="run-a", outcome=outcome,
        )

    repo.save_equity_curve("run-a", [
        (datetime(2024, 1, d, tzinfo=IST), equity, equity, 0)
        for d, equity in enumerate([100_000, 100_500, 99_000, 100_300], start=1)
    ])
    repo.close()
    connection = open_readonly(tmp_path / "ledger.db")
    yield connection
    connection.close()


# ------------------------------------------------------------------ paginate


def test_pagination_returns_a_slice_not_the_whole_table(conn):
    page1 = q.query_trades(conn, page=1, page_size=2, sort="exit_at", order="asc")
    page2 = q.query_trades(conn, page=2, page_size=2, sort="exit_at", order="asc")

    assert page1.total == 4 and page1.total_pages == 2
    assert len(page1.rows) == 2 and len(page2.rows) == 2
    assert {r["trade_id"] for r in page1.rows} == {"t1", "t2"}
    assert {r["trade_id"] for r in page2.rows} == {"t3", "t4"}


def test_page_size_is_clamped_to_the_maximum(conn):
    page = q.query_trades(conn, page=1, page_size=10_000)
    assert page.page_size == q.MAX_PAGE_SIZE


@pytest.mark.parametrize("page,size", [(0, 10), (-1, 10), (1, 0), (1, -5)])
def test_invalid_paging_is_rejected(conn, page, size):
    with pytest.raises(q.InvalidQuery):
        q.query_trades(conn, page=page, page_size=size)


# --------------------------------------------------------------------- sort


def test_sorting_ascending_and_descending(conn):
    asc = q.query_trades(conn, sort="net_pnl", order="asc").rows
    desc = q.query_trades(conn, sort="net_pnl", order="desc").rows
    assert [r["net_pnl"] for r in asc] == sorted(r["net_pnl"] for r in asc)
    assert [r["net_pnl"] for r in desc] == sorted((r["net_pnl"] for r in desc), reverse=True)


def test_sort_column_must_be_allowlisted(conn):
    # A real column that is not in the endpoint's allowlist is still refused.
    with pytest.raises(q.InvalidQuery):
        q.query_trades(conn, sort="trade_id")


def test_sort_column_injection_is_refused(conn):
    with pytest.raises(q.InvalidQuery):
        q.query_trades(conn, sort="net_pnl; DROP TABLE trades")


def test_order_direction_must_be_asc_or_desc(conn):
    with pytest.raises(q.InvalidQuery):
        q.query_trades(conn, order="; DROP TABLE trades")


# ------------------------------------------------------------------- filter


def test_symbol_filter_is_case_insensitive(conn):
    for value in ("TCS", "tcs", "Tcs"):
        rows = q.query_trades(conn, symbol=value).rows
        assert {r["symbol"] for r in rows} == {"TCS"}
        assert len(rows) == 2


def test_date_range_filter_is_inclusive(conn):
    rows = q.query_trades(conn, date_from="2024-01-10", date_to="2024-01-15").rows
    assert {r["trade_id"] for r in rows} == {"t2", "t3"}


def test_midnight_ist_timestamp_is_not_shifted_into_the_previous_day(conn):
    """Regression: date filters used SQLite's date(), which converts an ISO
    string carrying a +05:30 offset to UTC. date('2024-01-10T00:00:00+05:30')
    is 2024-01-09, so a trade stamped midnight IST fell out of a range that
    started on its own date. Every bar in this app is IST-stamped and daily
    bars sit at midnight, so this hid rows from the user."""

    exact_day = q.query_trades(conn, date_from="2024-01-10", date_to="2024-01-10").rows
    assert {r["trade_id"] for r in exact_day} == {"t2"}

    # And the day before must NOT pick it up.
    day_before = q.query_trades(conn, date_from="2024-01-09", date_to="2024-01-09").rows
    assert day_before == []


def test_signal_date_filter_handles_midnight_ist(conn):
    rows = q.query_signals(conn, date_from="2024-01-01", date_to="2024-01-01").rows
    assert len(rows) == 1
    assert rows[0]["signal_id"] == "s0"


def test_search_matches_symbol_or_trade_id(conn):
    assert {r["trade_id"] for r in q.query_trades(conn, search="INFY").rows} == {"t3", "t4"}
    assert {r["trade_id"] for r in q.query_trades(conn, search="t1").rows} == {"t1"}


def test_search_does_not_execute_sql(conn):
    rows = q.query_trades(conn, search="' OR 1=1 --").rows
    assert rows == [], "a hostile search string must match nothing, not everything"
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 4


def test_signal_filters_and_action_counts(conn):
    assert q.signal_action_counts(conn, "run-a") == {"BUY": 1, "NO_TRADE": 2, "SELL": 1}
    no_trades = q.query_signals(conn, action="NO_TRADE").rows
    assert len(no_trades) == 2
    assert all(r["action"] == "NO_TRADE" for r in no_trades)


# --------------------------------------------------------------- aggregates


def test_trade_aggregates_match_hand_computed_values(conn):
    agg = q.run_trade_aggregates(conn, "run-a")

    assert agg["trade_count"] == 4
    assert agg["net_pnl"] == pytest.approx(300.0)       # 200 - 100 + 300 - 100
    assert agg["gross_pnl"] == pytest.approx(320.0)     # each gross = net + 5
    assert agg["costs"] == pytest.approx(20.0)          # 4 legs x 5
    assert agg["win_count"] == 2 and agg["loss_count"] == 2
    assert agg["win_rate"] == pytest.approx(0.5)
    assert agg["avg_win"] == pytest.approx(250.0)       # (200 + 300) / 2
    assert agg["avg_loss"] == pytest.approx(-100.0)
    # expectancy = 0.5 * 250 + 0.5 * -100 = 75
    assert agg["expectancy"] == pytest.approx(75.0)


def test_gross_and_net_differ_by_exactly_the_costs(conn):
    agg = q.run_trade_aggregates(conn, "run-a")
    assert agg["gross_pnl"] - agg["costs"] == pytest.approx(agg["net_pnl"])


def test_aggregates_on_an_empty_run_do_not_divide_by_zero(conn):
    agg = q.run_trade_aggregates(conn, "no-such-run")
    assert agg["trade_count"] == 0
    assert agg["win_rate"] is None
    assert agg["expectancy"] is None
    assert agg["net_pnl"] == 0


def test_max_drawdown_matches_the_recorded_curve(conn):
    # Curve: 100000 -> 100500 -> 99000 -> 100300.
    # Peak 100500, trough 99000 => 1500 / 100500.
    assert q.max_drawdown(conn, "run-a") == pytest.approx(1500 / 100_500)


def test_max_drawdown_is_none_for_a_single_point(tmp_path):
    repo = SqliteRepository(tmp_path / "one.db")
    repo.save_equity_curve("solo", [(datetime(2024, 1, 1, tzinfo=IST), 100.0, 100.0, 0)])
    repo.close()
    conn = open_readonly(tmp_path / "one.db")
    assert q.max_drawdown(conn, "solo") is None
    conn.close()


def test_equity_curve_thinning_keeps_the_final_point(tmp_path):
    repo = SqliteRepository(tmp_path / "big.db")
    repo.save_equity_curve("run-big", [
        (datetime(2024, 1, 1, tzinfo=IST), float(i), float(i), 0) for i in range(5000)
    ])
    repo.close()
    conn = open_readonly(tmp_path / "big.db")
    curve = q.equity_curve(conn, "run-big", max_points=100)
    assert len(curve) <= 110
    assert curve[-1]["seq"] == 4999, "the last recorded equity must never be sampled away"
    conn.close()


# ----------------------------------------------------------------- explorer


def test_only_allowlisted_tables_are_browsable(conn):
    for table in ("sqlite_master", "sqlite_sequence", "trades; DROP TABLE trades", "nope"):
        with pytest.raises(q.InvalidQuery):
            q.browse_table(conn, table)


def test_browse_table_reports_column_types_and_nulls(conn):
    result = q.browse_table(conn, "trades", page_size=5)
    names = {c["name"]: c for c in result["columns"]}
    assert names["trade_id"]["primary_key"] is True
    assert names["symbol"]["notnull"] is True
    # New columns exist and are NULL for rows saved without them.
    assert "ambiguous_exit" in names


def test_browse_table_handles_an_empty_table(conn):
    result = q.browse_table(conn, "positions")
    assert result["total"] == 0
    assert result["rows"] == []


def test_browse_search_on_a_table_with_no_text_columns_returns_nothing(conn):
    # equity_snapshots is all numeric apart from its timestamp; a search must
    # not silently fall through and return every row.
    result = q.browse_table(conn, "equity_snapshots", search="zzz")
    assert result["total"] == 0


def test_distinct_values_rejects_an_unknown_column(conn):
    with pytest.raises(q.InvalidQuery):
        q.distinct_values(conn, "trades", "nope; DROP TABLE trades")


# ------------------------------------------------------------------- derive


def test_derived_slippage_inverts_the_broker_arithmetic_exactly():
    """The derived pre-slippage price must reproduce the price the broker
    actually started from, or the Orders page is quietly wrong."""

    costs = CostAssumptions()
    basis = 250.0
    for side in (SignalAction.BUY, SignalAction.SELL):
        filled = compute_slippage(basis, side, costs.slippage_rate)
        order = {
            "side": side.value, "filled_price": filled, "filled_quantity": 10,
        }
        economics = derive_order_economics(order, costs)
        assert economics["derived"] is True
        assert economics["fill_price_basis"] == pytest.approx(basis, rel=1e-9)
        assert economics["costs"] == compute_order_costs(filled, 10, side, costs)
        # Slippage always works against the trader, so the total is positive.
        assert economics["slippage_total"] > 0


def test_unfilled_order_has_no_slippage_or_costs():
    order = {"side": "BUY", "filled_price": None, "filled_quantity": 0,
             "status": "REJECTED"}
    economics = derive_order_economics(order, CostAssumptions())
    assert economics["costs"] is None
    assert economics["slippage_total"] is None
    assert "never filled" in economics["unavailable_reason"]


def test_order_economics_unavailable_without_recorded_assumptions():
    order = {"side": "BUY", "filled_price": 100.0, "filled_quantity": 5}
    economics = derive_order_economics(order, None)
    assert economics["costs"] is None
    assert "did not record" in economics["unavailable_reason"]


def test_cost_assumptions_are_not_substituted_with_current_defaults():
    """A run that recorded no assumptions must report none, not today's."""
    assert cost_assumptions_from_run(None) is None
    assert cost_assumptions_from_run({"costs_json": None}) is None
    assert cost_assumptions_from_run({"costs_json": "{}"}) is None
    assert cost_assumptions_from_run({"costs_json": "not json"}) is None
    recorded = cost_assumptions_from_run(
        {"costs_json": '{"slippage_rate": 0.01}'}
    )
    assert recorded is not None and recorded.slippage_rate == 0.01


def test_position_without_a_price_reports_unknown_not_zero():
    marks = position_marks(
        {"quantity": 10, "avg_entry_price": 100.0, "last_price": None}, 100_000
    )
    assert marks["price_available"] is False
    assert marks["unrealized_pnl"] is None, "unknown must not be reported as flat"
    assert marks["market_value"] is None
    assert marks["exposure_pct"] is None
    assert marks["cost_basis"] == 1000.0
    assert "no price" in marks["price_note"]


def test_position_marks_compute_pnl_and_exposure():
    marks = position_marks(
        {"quantity": 10, "avg_entry_price": 100.0, "last_price": 120.0}, 10_000
    )
    assert marks["market_value"] == 1200.0
    assert marks["unrealized_pnl"] == 200.0
    assert marks["unrealized_pnl_pct"] == pytest.approx(0.2)
    assert marks["exposure_pct"] == pytest.approx(0.12)


def test_position_marks_tolerate_zero_cost_basis():
    marks = position_marks(
        {"quantity": 0, "avg_entry_price": 0.0, "last_price": 10.0}, 0
    )
    assert marks["unrealized_pnl_pct"] is None
    assert marks["exposure_pct"] is None
