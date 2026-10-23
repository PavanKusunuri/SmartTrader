from dataclasses import replace

from app.backtest.metrics import Metrics
from app.prechecks import CANDIDATES, run_prechecks

MOMENTUM = next(c for c in CANDIDATES if c.key == "swing_momentum_trend")
ORB = next(c for c in CANDIDATES if c.key == "intraday_orb_stocks_in_play")


def metrics(**kw) -> Metrics:
    base = dict(
        trade_count=40, win_count=18, loss_count=22, win_rate=0.45, avg_win=900.0,
        avg_loss=-500.0, expectancy=130.0, gross_pnl=8000.0, net_pnl=5200.0,
        total_costs=2800.0, starting_cash=100000.0, final_equity=105200.0,
        total_return_net_pct=0.052, total_return_gross_pct=0.08,
        max_drawdown_pct=0.06, benchmark_return_pct=0.02,
    )
    base.update(kw)
    return Metrics(**base)


def failed(report) -> set[str]:
    return {c.name for c in report.failures()}


def test_good_result_on_real_data_passes():
    assert run_prechecks(MOMENTUM, metrics(), metrics(), "NSE_BHAVCOPY_EOD").passed


def test_synthetic_data_always_fails():
    assert "real_data" in failed(run_prechecks(MOMENTUM, metrics(), metrics(), "SAMPLE_SYNTHETIC"))


def test_high_win_rate_does_not_rescue_negative_expectancy():
    bad = metrics(win_rate=0.9, expectancy=-50.0, net_pnl=-2000.0)
    assert "out_of_sample_net_expectancy_positive" in failed(
        run_prechecks(MOMENTUM, metrics(), bad, "NSE_BHAVCOPY_EOD"))


def test_too_few_trades_fails():
    few = metrics(trade_count=5)
    assert "out_of_sample_min_trades" in failed(
        run_prechecks(MOMENTUM, metrics(), few, "NSE_BHAVCOPY_EOD"))


def test_missing_out_of_sample_fails():
    assert "out_of_sample_present" in failed(run_prechecks(MOMENTUM, metrics(), None, "NSE_BHAVCOPY_EOD"))


def test_underperforming_buy_and_hold_fails():
    oos = metrics(benchmark_return_pct=0.10)
    assert "oos_beats_buy_and_hold" in failed(run_prechecks(MOMENTUM, metrics(), oos, "NSE_BHAVCOPY_EOD"))


def test_costs_dominating_gross_profit_fails():
    oos = metrics(gross_pnl=1000.0, total_costs=900.0, net_pnl=100.0)
    assert "oos_costs_not_dominant" in failed(run_prechecks(MOMENTUM, metrics(), oos, "NSE_BHAVCOPY_EOD"))


def test_intraday_net_negative_fails():
    oos = metrics(net_pnl=-10.0)
    assert "high_cost_sensitivity_acknowledged" in failed(run_prechecks(ORB, metrics(), oos, "NSE_BHAVCOPY_EOD"))


def test_anecdotal_evidence_fails_pullback():
    pull = next(c for c in CANDIDATES if c.key == "swing_pullback_in_uptrend")
    assert "evidence_not_anecdotal_only" in failed(run_prechecks(pull, metrics(), metrics(), "NSE_BHAVCOPY_EOD"))


def test_all_failures_reported_not_just_first():
    r = run_prechecks(replace(MOMENTUM), metrics(trade_count=1), metrics(trade_count=1), "SAMPLE_SYNTHETIC")
    assert len(r.failures()) >= 3
