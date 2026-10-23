from app.config import RiskLimits
from app.paper.portfolio import Portfolio
from app.paper.risk import RiskManager


def test_sizing_leaves_room_for_stop_slippage():
    plain = RiskManager(RiskLimits(gap_buffer_pct=0)).position_size(100_000, 100, 95)
    buffered = RiskManager(RiskLimits(gap_buffer_pct=0.02)).position_size(100_000, 100, 95)
    assert buffered < plain
    # Even if the stop fills 2% beyond its level, loss stays within the 1% budget.
    assert buffered * (5 + 2) <= 100_000 * 0.01


def test_single_trade_worst_case_loss_is_bounded():
    r = RiskManager(RiskLimits())
    qty = r.position_size(100_000, 100, 90)
    assert qty * 100 <= 100_000 * 0.2          # position cap
    assert qty * (10 + 1) <= 100_000 * 0.01    # risk budget incl. buffer


def test_drawdown_breaker_halts_new_entries():
    limits = RiskLimits(max_drawdown_pct=0.10, max_daily_loss_pct=1.0)
    r = RiskManager(limits)
    p = Portfolio(starting_cash=100_000)
    assert r.check_new_entry_allowed(p, "A", 1, 100, {})[0]
    p.cash = 85_000  # equity 15% below the peak seen above
    ok, reason = r.check_new_entry_allowed(p, "B", 1, 100, {})
    assert not ok and "drawdown" in reason


def test_gap_below_stop_fills_at_open_not_at_stop(tmp_path):
    """Regression: a stop was filled at its level even when the bar opened below it."""
    from tests.test_persistence import daily, run_and_persist, VIABLE_STOP
    bars = [
        daily("X", 1, 100, 101, 99, 100),
        daily("X", 2, 100, 101, 99, 100),
        daily("X", 3, 100, 101, 99, 100),
        daily("X", 4, 80, 82, 78, 80),      # gaps far below the 95 stop
        daily("X", 5, 80, 81, 79, 80),
    ]
    repo, result, _ = run_and_persist(tmp_path, bars, {1: "BUY"}, stop=VIABLE_STOP, target=500)
    stopped = [t for t in result.trades if "stop" in t.exit_reason.lower()]
    assert stopped and stopped[0].exit_price <= 80.0
    repo.close()
