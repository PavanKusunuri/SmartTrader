"""Cost-viability gate tests.

The headline case is the one that motivated the gate: buy 20 shares at
Rs 19.00, sell at Rs 19.05, gross profit Re 1, charges far higher. The gate
must refuse it, and must say why in rupees.
"""

import pytest

from app.config import AppConfig, CostAssumptions, ProfileCharges, RiskLimits, ViabilityPolicy
from app.models import ChargeProfile, OrderStatus, Signal, SignalAction
from app.paper.broker import PaperBroker
from app.paper.viability import assess_trade, minimum_viable_quantity

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def costs() -> CostAssumptions:
    return CostAssumptions()


def policy(**kw) -> ViabilityPolicy:
    return ViabilityPolicy(**kw)


# --- the motivating example --------------------------------------------------


def test_twenty_shares_at_nineteen_rupees_for_five_paise_is_refused():
    result = assess_trade(
        entry_price=19.00, quantity=20, costs=costs(), policy=policy(),
        stop_loss=18.50, target=19.05,
    )
    assert result.viable is False
    assert result.gross_profit_at_target == pytest.approx(1.0)
    # Charges exceed the entire gross profit many times over.
    assert result.round_trip_costs_at_target > 20.0
    assert result.net_profit_at_target < 0
    assert "not cost-viable" in result.reason


def test_rejection_reason_quotes_the_numbers_that_failed():
    result = assess_trade(
        entry_price=19.00, quantity=20, costs=costs(), policy=policy(),
        stop_loss=18.50, target=19.05,
    )
    assert "Rs" in result.reason
    assert result.failed_checks  # at least one named check failed
    assert any("order value" in c or "net profit at target" in c for c in result.failed_checks)


def test_breakeven_price_is_reported_so_the_user_can_see_the_real_target():
    result = assess_trade(
        entry_price=19.00, quantity=20, costs=costs(), policy=policy(),
        stop_loss=18.50, target=19.05,
    )
    assert result.breakeven_price is not None
    # The Rs 19.05 exit is nowhere near breakeven.
    assert result.breakeven_price > 19.05
    assert result.breakeven_move_pct > 0.01


# --- a trade that does clear its costs ---------------------------------------


def test_adequately_sized_trade_with_a_real_move_is_viable():
    # Rs 50,000 order, 5% target: charges are a small fraction of the move.
    result = assess_trade(
        entry_price=500.0, quantity=100, costs=costs(), policy=policy(),
        stop_loss=475.0, target=525.0,
    )
    assert result.viable is True
    assert result.net_profit_at_target > 0
    assert result.reward_to_cost_ratio > 2.0
    assert "cost-viable" in result.reason
    assert result.failed_checks == ()


def test_viable_trade_reports_cost_pct_and_net_figures():
    result = assess_trade(
        entry_price=500.0, quantity=100, costs=costs(), policy=policy(),
        stop_loss=475.0, target=525.0,
    )
    assert 0 < result.cost_pct_of_turnover < 0.01
    assert result.net_loss_at_stop > 2500.0  # the Rs 2,500 stop loss plus charges
    assert result.net_reward_to_risk is not None


# --- individual checks -------------------------------------------------------


def test_order_value_floor_blocks_small_orders():
    result = assess_trade(
        entry_price=100.0, quantity=10, costs=costs(),  # Rs 1,000 order
        policy=policy(min_order_value=2500.0),
        stop_loss=95.0, target=120.0,
    )
    assert result.viable is False
    assert any("order value" in c for c in result.failed_checks)


def test_reward_to_cost_ratio_blocks_a_move_too_small_to_pay_for_itself():
    # Big enough order, but the target is only a whisker above entry, so the
    # trade clears net-positive yet nowhere near the required cost multiple.
    result = assess_trade(
        entry_price=500.0, quantity=100, costs=costs(),
        policy=policy(min_reward_to_cost_ratio=2.0, min_net_reward_to_risk=0.0),
        stop_loss=495.0, target=501.5,
    )
    assert result.viable is False
    assert any("reward-to-cost" in c for c in result.failed_checks)


def test_net_reward_to_risk_blocks_a_poorly_shaped_trade():
    # Target barely above entry, stop far below: net reward << net risk.
    result = assess_trade(
        entry_price=500.0, quantity=100, costs=costs(),
        policy=policy(min_reward_to_cost_ratio=0.0, min_net_reward_to_risk=1.0),
        stop_loss=400.0, target=510.0,
    )
    assert result.viable is False
    assert any("net reward-to-risk" in c for c in result.failed_checks)


def test_cost_pct_of_turnover_limit_blocks_expensive_round_trips():
    expensive = CostAssumptions(
        delivery=ProfileCharges(brokerage_pct=0.02, brokerage_cap=0.0, brokerage_min=0.0)
    )
    result = assess_trade(
        entry_price=500.0, quantity=100, costs=expensive,
        policy=policy(require_net_profit_at_target=False, min_reward_to_cost_ratio=0.0,
                      min_net_reward_to_risk=0.0, max_cost_pct_of_turnover=0.01),
        stop_loss=475.0, target=525.0,
    )
    assert result.viable is False
    assert any("charges are" in c for c in result.failed_checks)


def test_zero_quantity_is_not_tradable():
    result = assess_trade(
        entry_price=500.0, quantity=0, costs=costs(), policy=policy(),
        stop_loss=475.0, target=525.0,
    )
    assert result.viable is False
    assert any("position size is zero" in c for c in result.failed_checks)


# --- missing inputs are skipped, not silently passed or failed --------------


def test_missing_target_skips_reward_checks_and_says_so():
    result = assess_trade(
        entry_price=500.0, quantity=100, costs=costs(), policy=policy(),
        stop_loss=475.0, target=None,
    )
    assert result.viable is True  # order value is fine; nothing else is knowable
    assert any("no target price" in s for s in result.skipped_checks)
    assert result.net_profit_at_target is None
    assert result.reward_to_cost_ratio is None


def test_missing_stop_skips_reward_to_risk_check():
    result = assess_trade(
        entry_price=500.0, quantity=100, costs=costs(), policy=policy(),
        stop_loss=None, target=525.0,
    )
    assert any("no stop-loss" in s for s in result.skipped_checks)
    assert result.net_loss_at_stop is None


def test_breakeven_is_still_computed_without_a_target():
    result = assess_trade(
        entry_price=500.0, quantity=100, costs=costs(), policy=policy(), target=None,
    )
    assert result.breakeven_price is not None and result.breakeven_price > 500.0


# --- the gate can be turned off, but the numbers remain visible -------------


def test_disabled_policy_computes_economics_but_does_not_block():
    result = assess_trade(
        entry_price=19.00, quantity=20, costs=costs(), policy=policy(enabled=False),
        stop_loss=18.50, target=19.05,
    )
    assert result.viable is True
    assert "disabled" in result.reason
    # The economics are still there to look at.
    assert result.net_profit_at_target < 0
    assert result.round_trip_costs_at_target > 20.0


# --- profiles are priced differently ----------------------------------------


def test_intraday_round_trip_is_cheaper_than_delivery_for_the_same_trade():
    delivery = assess_trade(
        entry_price=500.0, quantity=100, costs=costs(), policy=policy(),
        stop_loss=475.0, target=525.0, profile=ChargeProfile.DELIVERY,
    )
    intraday = assess_trade(
        entry_price=500.0, quantity=100, costs=costs(), policy=policy(),
        stop_loss=475.0, target=525.0, profile=ChargeProfile.INTRADAY,
    )
    # Intraday pays no DP charge and sell-side-only STT at a lower rate.
    assert intraday.round_trip_costs_at_target < delivery.round_trip_costs_at_target


# --- minimum viable quantity ------------------------------------------------


def test_minimum_viable_quantity_is_reported_for_a_salvageable_setup():
    # A 2% target on a Rs 19 stock: too small at 20 shares, fine at scale.
    min_qty = minimum_viable_quantity(
        entry_price=19.0, costs=costs(), policy=policy(),
        stop_loss=18.0, target=19.38,
    )
    assert min_qty is not None
    assert min_qty > 20
    # The reported minimum must actually pass, and one share fewer must not.
    assert assess_trade(19.0, min_qty, costs(), policy(), stop_loss=18.0, target=19.38).viable
    assert not assess_trade(
        19.0, min_qty - 1, costs(), policy(), stop_loss=18.0, target=19.38
    ).viable


def test_minimum_viable_quantity_is_none_when_no_size_can_work():
    # A 5-paise target can never pay for percentage-based charges, at any size.
    assert minimum_viable_quantity(
        entry_price=19.0, costs=costs(), policy=policy(),
        stop_loss=18.5, target=19.05,
    ) is None


# --- the broker enforces the gate -------------------------------------------


def buy_signal(price: float, stop: float, target: float, profile=ChargeProfile.DELIVERY):
    return Signal(
        signal_id="s1", symbol="PENNY", timestamp=datetime(2024, 1, 10, tzinfo=IST),
        strategy_name="test", action=SignalAction.BUY,
        reason="test entry", inputs={}, stop_loss=stop, target=target,
        charge_profile=profile,
    )


def test_broker_rejects_an_uneconomic_entry_without_filling_it():
    config = AppConfig(starting_cash=100_000, risk=RiskLimits(max_position_pct=1.0))
    broker = PaperBroker(config)
    order = broker.process_signal(
        buy_signal(19.00, 18.00, 19.05), fill_price_basis=19.00,
        current_prices={"PENNY": 19.00},
    )
    assert order.status == OrderStatus.REJECTED
    assert "not cost-viable" in order.rejection_reason
    # Nothing was bought and no cash moved.
    assert broker.portfolio.positions == {}
    assert broker.portfolio.cash == 100_000


def test_broker_records_the_economics_of_a_rejected_entry():
    config = AppConfig(starting_cash=100_000, risk=RiskLimits(max_position_pct=1.0))
    broker = PaperBroker(config)
    order = broker.process_signal(
        buy_signal(19.00, 18.00, 19.05), fill_price_basis=19.00,
        current_prices={"PENNY": 19.00},
    )
    economics = broker.trade_economics[order.order_id]
    assert economics.viable is False
    assert economics.net_profit_at_target < 0
    # Available as JSON for the dashboard/ledger.
    assert economics.as_dict()["breakeven_price"] is not None


def test_broker_fills_an_economic_entry_and_records_its_profile():
    config = AppConfig(starting_cash=1_000_000, risk=RiskLimits(max_position_pct=1.0))
    broker = PaperBroker(config)
    signal = Signal(
        signal_id="s2", symbol="BIGCO", timestamp=datetime(2024, 1, 10, tzinfo=IST),
        strategy_name="test", action=SignalAction.BUY, reason="test entry", inputs={},
        stop_loss=475.0, target=525.0, charge_profile=ChargeProfile.INTRADAY,
    )
    order = broker.process_signal(
        signal, fill_price_basis=500.0, current_prices={"BIGCO": 500.0}
    )
    assert order.status == OrderStatus.FILLED
    assert order.charge_profile == ChargeProfile.INTRADAY
    assert broker.portfolio.positions["BIGCO"].charge_profile == ChargeProfile.INTRADAY


def test_disabled_gate_lets_the_broker_fill_a_tiny_trade():
    config = AppConfig(
        starting_cash=100_000, risk=RiskLimits(max_position_pct=1.0),
        viability=ViabilityPolicy(enabled=False),
    )
    broker = PaperBroker(config)
    order = broker.process_signal(
        buy_signal(19.00, 18.00, 19.05), fill_price_basis=19.00,
        current_prices={"PENNY": 19.00},
    )
    assert order.status == OrderStatus.FILLED


def test_exit_leg_is_costed_under_the_profile_the_position_was_opened_with():
    config = AppConfig(
        starting_cash=1_000_000, risk=RiskLimits(max_position_pct=1.0),
        viability=ViabilityPolicy(enabled=False),
    )

    def round_trip(profile) -> float:
        broker = PaperBroker(config)
        entry = Signal(
            signal_id="e", symbol="CO", timestamp=datetime(2024, 1, 10, tzinfo=IST),
            strategy_name="t", action=SignalAction.BUY, reason="in", inputs={},
            stop_loss=475.0, target=525.0, charge_profile=profile,
        )
        broker.process_signal(entry, 500.0, {"CO": 500.0})
        exit_signal = Signal(
            signal_id="x", symbol="CO", timestamp=datetime(2024, 1, 11, tzinfo=IST),
            strategy_name="t", action=SignalAction.SELL, reason="out", inputs={},
        )
        broker.process_signal(exit_signal, 525.0, {"CO": 525.0})
        return broker.portfolio.trades[-1].costs

    # The SELL signal carries the default DELIVERY profile in both runs, so a
    # difference here proves the POSITION's profile drove the exit costing.
    assert round_trip(ChargeProfile.INTRADAY) < round_trip(ChargeProfile.DELIVERY)


# --- strategies declare the profile their trades settle under ---------------
#
# Flagged by a peer session as the thing most likely to bite: if both
# strategies fell back to the DELIVERY default, every intraday entry would be
# charged STT on both legs plus a flat DP charge it would never actually pay,
# making the intraday prototype look worse than it is. These assert the
# emitted signal, not just the class attribute, so a stamping regression is
# caught too.


def _one_bar(symbol="X", close=100.0):
    from app.models import Bar
    ts = datetime(2024, 1, 10, 10, 0, tzinfo=IST)
    return Bar(
        symbol=symbol, timestamp=ts, open=close, high=close, low=close, close=close,
        volume=1000, source="test-double", retrieved_at=ts, interval="5m",
    )


def test_intraday_strategy_declares_and_emits_the_intraday_profile():
    from app.strategies.intraday import IntradayMomentumStrategy

    strategy = IntradayMomentumStrategy()
    assert strategy.charge_profile == ChargeProfile.INTRADAY
    # Every signal it emits carries it, including a NO_TRADE one.
    signal = strategy.evaluate([_one_bar()])
    assert signal.charge_profile == ChargeProfile.INTRADAY


def test_swing_strategy_declares_and_emits_the_delivery_profile():
    from app.strategies.swing import SwingMAStrategy

    strategy = SwingMAStrategy()
    assert strategy.charge_profile == ChargeProfile.DELIVERY
    signal = strategy.evaluate([_one_bar(close=100.0)])
    assert signal.charge_profile == ChargeProfile.DELIVERY


# --- engine-initiated exits inherit the POSITION's profile ------------------


def test_engine_synthetic_stop_exit_is_costed_under_the_position_profile():
    """The engine builds its own SELL signals for stop/target/EOD exits, so
    they inherit no profile from a strategy. The exit must still be costed
    the way the entry was, which it is because the broker reads the
    position's profile rather than the signal's."""

    from app.backtest.engine import run_backtest
    from app.data.base import DataProvider
    from app.models import Bar
    from app.strategies.base import Strategy

    class TwoBarProvider(DataProvider):
        def __init__(self, bars):
            self.bars = bars

        def get_bars(self, symbol, interval="1d", start=None, end=None):
            return list(self.bars)

    def daily(day, o, h, l, c):
        ts = datetime(2024, 1, day, tzinfo=IST)
        return Bar(
            symbol="CO", timestamp=ts, open=o, high=h, low=l, close=c, volume=1000,
            source="test-double", retrieved_at=ts, interval="1d",
        )

    class BuyThenHold(Strategy):
        name = "buy_then_hold"

        def __init__(self, profile):
            self.charge_profile = profile

        @property
        def required_history(self) -> int:
            return 1

        def evaluate(self, bars, position=None):
            bar = bars[-1]
            buying = position is None and len(bars) == 1
            return Signal(
                signal_id=f"bth:{bar.symbol}:{bar.timestamp.isoformat()}",
                symbol=bar.symbol, timestamp=bar.timestamp, strategy_name=self.name,
                action=SignalAction.BUY if buying else SignalAction.NO_TRADE,
                reason="test entry" if buying else "holding", inputs={},
                stop_loss=475.0 if buying else None,
                target=600.0 if buying else None,
                charge_profile=self.charge_profile,
            )

    bars = [
        daily(1, 500, 505, 495, 500),   # BUY signalled
        daily(2, 500, 505, 495, 500),   # fills at this open
        daily(3, 500, 505, 470, 480),   # low 470 breaches the 475 stop
    ]

    def run(profile):
        result = run_backtest(
            provider=TwoBarProvider(bars),
            symbols=["CO"],
            strategy=BuyThenHold(profile),
            config=AppConfig(
                starting_cash=1_000_000,
                risk=RiskLimits(max_position_pct=1.0),
                viability=ViabilityPolicy(enabled=False),
            ),
        )
        assert result.metrics.trade_count == 1, "the stop exit must have produced a trade"
        trade = result.trades[0]
        assert "stop" in trade.exit_reason.lower()
        return trade

    intraday_trade = run(ChargeProfile.INTRADAY)
    delivery_trade = run(ChargeProfile.DELIVERY)

    # Both legs of the intraday round trip are cheaper: no DP charge on the
    # exit, lower STT. If the exit were costed as delivery regardless, these
    # would be much closer together.
    assert intraday_trade.costs < delivery_trade.costs
    assert intraday_trade.charge_profile == ChargeProfile.INTRADAY
    assert delivery_trade.charge_profile == ChargeProfile.DELIVERY
