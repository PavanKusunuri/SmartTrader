"""Cost-model tests.

The numbers asserted here are the ones the CONFIGURED assumptions imply, not
claims about what any broker actually charges. Each test sets the rates it
cares about explicitly so it tests the arithmetic rather than today's
defaults.
"""

import pytest

from app.backtest.costs import (
    breakeven_exit_price,
    compute_brokerage,
    compute_order_cost_breakdown,
    compute_order_costs,
    compute_round_trip_costs,
    compute_slippage,
)
from app.config import CostAssumptions, ProfileCharges
from app.models import ChargeProfile, SignalAction

ZERO_RATES = dict(exchange_txn_rate=0.0, sebi_turnover_rate=0.0, ipft_rate=0.0, gst_rate=0.0)


def only_profile(**profile_kwargs) -> CostAssumptions:
    """Cost assumptions with every shared rate zeroed, so a test sees only the
    profile-specific charge it is asserting on."""

    zeroed = dict(
        brokerage_pct=0.0, brokerage_cap=0.0, brokerage_min=0.0,
        stt_buy_rate=0.0, stt_sell_rate=0.0, stamp_duty_buy_rate=0.0,
        dp_charge_per_sell=0.0,
    )
    zeroed.update(profile_kwargs)
    charges = ProfileCharges(**zeroed)
    return CostAssumptions(delivery=charges, intraday=charges, **ZERO_RATES)


def test_buy_slippage_increases_price():
    assert compute_slippage(100.0, SignalAction.BUY, 0.001) == 100.1


def test_sell_slippage_decreases_price():
    assert compute_slippage(100.0, SignalAction.SELL, 0.001) == 99.9


def test_slippage_rejects_no_trade_side():
    with pytest.raises(ValueError):
        compute_slippage(100.0, SignalAction.NO_TRADE, 0.001)


def test_costs_are_never_negative():
    costs = CostAssumptions()
    assert compute_order_costs(100.0, 10, SignalAction.BUY, costs) >= 0
    assert compute_order_costs(100.0, 10, SignalAction.SELL, costs) >= 0


def test_higher_turnover_means_higher_cost():
    costs = CostAssumptions()
    small = compute_order_costs(100.0, 1, SignalAction.BUY, costs)
    large = compute_order_costs(100.0, 1000, SignalAction.BUY, costs)
    assert large > small


def test_costs_reject_unsupported_side():
    with pytest.raises(ValueError):
        compute_order_costs(100.0, 10, SignalAction.NO_TRADE, CostAssumptions())


# --- brokerage floor and cap -------------------------------------------------


def test_brokerage_uses_percentage_between_floor_and_cap():
    charges = ProfileCharges(brokerage_pct=0.001, brokerage_cap=20.0, brokerage_min=5.0)
    # 0.1% of 10,000 = 10, which is between the 5 floor and the 20 cap.
    assert compute_brokerage(10_000, charges) == pytest.approx(10.0)


def test_brokerage_floor_applies_to_small_orders():
    """The whole reason a 20-share Rs 19 trade cannot work."""

    charges = ProfileCharges(brokerage_pct=0.001, brokerage_cap=20.0, brokerage_min=5.0)
    # 0.1% of 380 is Re 0.38, but the per-order floor is Rs 5.
    assert compute_brokerage(380, charges) == pytest.approx(5.0)


def test_brokerage_cap_applies_to_large_orders():
    charges = ProfileCharges(brokerage_pct=0.001, brokerage_cap=20.0, brokerage_min=5.0)
    # 0.1% of 500,000 = 500, capped at 20.
    assert compute_brokerage(500_000, charges) == pytest.approx(20.0)


def test_zero_turnover_pays_no_brokerage():
    charges = ProfileCharges(brokerage_min=5.0)
    assert compute_brokerage(0, charges) == 0.0


def test_brokerage_cap_below_floor_is_rejected():
    with pytest.raises(ValueError):
        ProfileCharges(brokerage_cap=5.0, brokerage_min=20.0)


# --- which charge applies to which leg ---------------------------------------


def test_delivery_charges_stt_on_both_legs():
    costs = only_profile(stt_buy_rate=0.001, stt_sell_rate=0.001)
    buy = compute_order_cost_breakdown(100.0, 10, SignalAction.BUY, costs)
    sell = compute_order_cost_breakdown(100.0, 10, SignalAction.SELL, costs)
    assert buy.stt == pytest.approx(1.0)
    assert sell.stt == pytest.approx(1.0)


def test_intraday_charges_stt_on_sell_leg_only():
    costs = CostAssumptions(**ZERO_RATES)
    buy = compute_order_cost_breakdown(
        100.0, 10, SignalAction.BUY, costs, ChargeProfile.INTRADAY
    )
    sell = compute_order_cost_breakdown(
        100.0, 10, SignalAction.SELL, costs, ChargeProfile.INTRADAY
    )
    assert buy.stt == 0.0
    assert sell.stt > 0


def test_stamp_duty_applies_to_buy_leg_only():
    costs = only_profile(stamp_duty_buy_rate=0.00015)
    buy = compute_order_cost_breakdown(100.0, 10, SignalAction.BUY, costs)
    sell = compute_order_cost_breakdown(100.0, 10, SignalAction.SELL, costs)
    assert buy.stamp_duty > 0
    assert sell.stamp_duty == 0.0


def test_dp_charge_is_flat_on_delivery_sell_and_absent_on_buy():
    costs = only_profile(dp_charge_per_sell=18.25)
    small_sell = compute_order_cost_breakdown(19.0, 20, SignalAction.SELL, costs)
    large_sell = compute_order_cost_breakdown(1900.0, 200, SignalAction.SELL, costs)
    buy = compute_order_cost_breakdown(19.0, 20, SignalAction.BUY, costs)
    # Flat: the same rupees on a Rs 380 exit as on a Rs 380,000 one.
    assert small_sell.dp_charge == pytest.approx(18.25)
    assert large_sell.dp_charge == pytest.approx(18.25)
    assert buy.dp_charge == 0.0


def test_intraday_has_no_dp_charge_by_default():
    sell = compute_order_cost_breakdown(
        100.0, 100, SignalAction.SELL, CostAssumptions(), ChargeProfile.INTRADAY
    )
    assert sell.dp_charge == 0.0


def test_gst_is_charged_on_fees_not_on_turnover():
    costs = CostAssumptions(
        delivery=ProfileCharges(
            brokerage_pct=0.0, brokerage_cap=20.0, brokerage_min=20.0,
            stt_buy_rate=0.0, stt_sell_rate=0.0, stamp_duty_buy_rate=0.0,
            dp_charge_per_sell=0.0,
        ),
        exchange_txn_rate=0.0, sebi_turnover_rate=0.0, ipft_rate=0.0, gst_rate=0.18,
    )
    leg = compute_order_cost_breakdown(100.0, 100, SignalAction.BUY, costs)
    # 18% of the Rs 20 brokerage, not 18% of the Rs 10,000 turnover.
    assert leg.gst == pytest.approx(3.6)


def test_breakdown_components_sum_to_total():
    leg = compute_order_cost_breakdown(1234.56, 37, SignalAction.SELL, CostAssumptions())
    parts = (
        leg.brokerage + leg.stt + leg.exchange_txn + leg.sebi_turnover
        + leg.ipft + leg.stamp_duty + leg.dp_charge + leg.gst
    )
    assert leg.total == pytest.approx(parts, abs=0.005)


def test_unknown_profile_is_rejected():
    with pytest.raises(ValueError):
        CostAssumptions().for_profile("MARGIN")


# --- the user's example: small trades are dominated by flat charges ----------


def test_small_order_costs_a_large_fraction_of_turnover():
    """20 shares at Rs 19 = Rs 380. Charges must dwarf a 5-paise move."""

    costs = CostAssumptions()
    round_trip = compute_round_trip_costs(19.00, 19.05, 20, costs)
    gross_profit = (19.05 - 19.00) * 20  # Re 1.00
    assert gross_profit == pytest.approx(1.0)
    assert round_trip > gross_profit * 20  # charges are 20x+ the gross profit


def test_cost_pct_of_turnover_falls_as_order_value_rises():
    costs = CostAssumptions()
    tiny = compute_order_cost_breakdown(19.0, 20, SignalAction.SELL, costs)
    big = compute_order_cost_breakdown(1900.0, 200, SignalAction.SELL, costs)
    assert tiny.pct_of_turnover > big.pct_of_turnover


# --- breakeven ---------------------------------------------------------------


def test_breakeven_price_covers_round_trip_costs():
    costs = CostAssumptions()
    be = breakeven_exit_price(19.0, 20, costs)
    assert be is not None
    net = (be - 19.0) * 20 - compute_round_trip_costs(19.0, be, 20, costs)
    assert net >= 0  # exiting exactly at breakeven never loses money
    # Breakeven is rounded up to the next whole paise, so the residual is
    # bounded by one paise per share plus the cost model's own rounding.
    assert net < 0.01 * 20 + 0.02


def test_breakeven_move_is_huge_for_tiny_orders_and_small_for_large_ones():
    costs = CostAssumptions()
    tiny = breakeven_exit_price(19.0, 20, costs)
    large = breakeven_exit_price(19.0, 20_000, costs)
    tiny_move = (tiny - 19.0) / 19.0
    large_move = (large - 19.0) / 19.0
    assert tiny_move > 0.05   # a Rs 380 trade needs a 5%+ move just to break even
    assert large_move < 0.01  # a Rs 380,000 trade needs well under 1%


def test_breakeven_is_undefined_for_zero_quantity():
    assert breakeven_exit_price(19.0, 0, CostAssumptions()) is None


def test_zero_cost_assumptions_break_even_at_entry():
    """A sanity check that the solver is not adding cost of its own."""

    free = CostAssumptions(
        delivery=ProfileCharges(
            brokerage_pct=0.0, brokerage_cap=0.0, brokerage_min=0.0,
            stt_buy_rate=0.0, stt_sell_rate=0.0, stamp_duty_buy_rate=0.0,
            dp_charge_per_sell=0.0,
        ),
        **ZERO_RATES,
    )
    assert breakeven_exit_price(19.0, 20, free) == pytest.approx(19.0)


# --- legacy stored-schema migration -----------------------------------------


def test_legacy_flat_cost_schema_is_migrated_not_repriced():
    """A run recorded under the old flat schema keeps its own numbers."""

    legacy = {
        "brokerage_per_order": 20.0,
        "stt_sell_rate": 0.00025,
        "exchange_txn_rate": 0.0000345,
        "gst_rate": 0.18,
        "stamp_duty_buy_rate": 0.00015,
        "slippage_rate": 0.0005,
    }
    costs = CostAssumptions.model_validate(legacy)
    # Flat Rs 20 per order, regardless of size, as the old model charged.
    assert compute_brokerage(1_000, costs.delivery) == pytest.approx(20.0)
    assert compute_brokerage(1_000_000, costs.delivery) == pytest.approx(20.0)
    # The old model had no DP charge, SEBI fee or IPFT; they must not appear.
    assert costs.delivery.dp_charge_per_sell == 0.0
    assert costs.sebi_turnover_rate == 0.0
    assert costs.ipft_rate == 0.0
    assert costs.gst_rate == 0.18


def test_new_schema_is_not_mistaken_for_legacy():
    data = CostAssumptions().model_dump()
    reloaded = CostAssumptions.model_validate(data)
    assert reloaded.delivery.dp_charge_per_sell == CostAssumptions().delivery.dp_charge_per_sell
