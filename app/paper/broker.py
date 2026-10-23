"""Virtual broker: the ONLY place a Signal turns into an Order.

PAPER TRADING — NO REAL ORDERS. This module never calls a live broker API
and has no network access. Every fill here is a simulation based on the
current bar's price plus configured slippage/costs.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.backtest.costs import compute_order_costs, compute_slippage
from app.config import AppConfig
from app.models import ChargeProfile, Order, OrderStatus, Signal, SignalAction
from app.paper.portfolio import Portfolio
from app.paper.risk import RiskManager
from app.paper.viability import TradeEconomics, assess_trade, minimum_viable_quantity


class PaperBroker:
    """PAPER TRADING — NO REAL ORDERS. Simulated fills only."""

    def __init__(self, config: AppConfig, portfolio: Portfolio | None = None):
        self.config = config
        self.portfolio = portfolio or Portfolio(config.starting_cash)
        self.risk = RiskManager(config.risk)
        self.orders: list[Order] = []
        self._processed_signal_ids: set[str] = set()
        # Economics computed for every BUY the broker priced, keyed by order
        # id, including the ones it rejected. Kept so the dashboard and the
        # ledger can show *why* a setup was refused, not just that it was.
        self.trade_economics: dict[str, TradeEconomics] = {}

    def process_signal(
        self,
        signal: Signal,
        fill_price_basis: float,
        current_prices: dict[str, float],
        fill_timestamp: datetime | None = None,
        ambiguous_exit: bool = False,
    ) -> Order | None:
        """Turns a Signal into a simulated Order, or returns None for
        NO_TRADE signals (no order is created at all — there is nothing to
        reject or fill).

        `fill_price_basis` is the price the fill is simulated from, decided
        by the caller. The backtest engine passes the NEXT bar's open, so a
        decision made on bar N never fills at bar N's own close.
        `fill_timestamp` is when the fill happened (defaults to the signal's
        own timestamp when the caller does not separate the two).

        Risk limits are re-checked here, at fill time, not at signal time —
        so a queued order that has become unaffordable or would breach a
        limit is rejected rather than filled on stale reasoning.
        """

        if signal.action == SignalAction.NO_TRADE:
            return None

        fill_timestamp = fill_timestamp or signal.timestamp

        if signal.signal_id in self._processed_signal_ids:
            return self._make_order(
                signal, quantity=0, status=OrderStatus.REJECTED,
                rejection_reason="duplicate signal_id already processed",
            )
        self._processed_signal_ids.add(signal.signal_id)

        if signal.action == SignalAction.BUY:
            return self._process_buy(signal, fill_price_basis, current_prices, fill_timestamp)
        if signal.action == SignalAction.SELL:
            return self._process_sell(signal, fill_price_basis, fill_timestamp, ambiguous_exit)

        raise ValueError(f"unhandled signal action: {signal.action}")

    def _process_buy(
        self,
        signal: Signal,
        fill_price_basis: float,
        current_prices: dict[str, float],
        fill_timestamp: datetime,
    ) -> Order:
        if signal.stop_loss is None:
            return self._make_order(
                signal, quantity=0, status=OrderStatus.REJECTED,
                rejection_reason="BUY signal missing required stop_loss",
            )

        quantity = self.risk.position_size(
            cash=self.portfolio.cash, entry_price=fill_price_basis, stop_loss=signal.stop_loss
        )
        allowed, reason = self.risk.check_new_entry_allowed(
            self.portfolio, signal.symbol, quantity, fill_price_basis, current_prices
        )
        if not allowed:
            return self._make_order(
                signal, quantity=quantity, status=OrderStatus.REJECTED, rejection_reason=reason
            )

        fill_price = compute_slippage(fill_price_basis, SignalAction.BUY, self.config.costs.slippage_rate)

        # Cost viability is judged on the price the trade would actually fill
        # at (slippage included), not the signal's clean close.
        economics = assess_trade(
            entry_price=fill_price,
            quantity=quantity,
            costs=self.config.costs,
            policy=self.config.viability,
            stop_loss=signal.stop_loss,
            target=signal.target,
            profile=signal.charge_profile,
        )
        if not economics.viable:
            order = self._make_order(
                signal,
                quantity=quantity,
                status=OrderStatus.REJECTED,
                rejection_reason=self._viability_rejection_reason(signal, fill_price, economics),
            )
            self.trade_economics[order.order_id] = economics
            return order

        costs = compute_order_costs(
            fill_price, quantity, SignalAction.BUY, self.config.costs, signal.charge_profile
        )
        order = self._make_order(signal, quantity=quantity, status=OrderStatus.SUBMITTED)
        self.trade_economics[order.order_id] = economics

        try:
            self.portfolio.apply_fill_buy(
                symbol=signal.symbol,
                quantity=quantity,
                fill_price=fill_price,
                costs=costs,
                timestamp=fill_timestamp,
                stop_loss=signal.stop_loss,
                target=signal.target,
                order_id=order.order_id,
                charge_profile=signal.charge_profile,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as a rejected order, not a crash
            order.status = OrderStatus.REJECTED
            order.rejection_reason = str(exc)
            return order

        order.status = OrderStatus.FILLED
        order.filled_price = fill_price
        order.filled_quantity = quantity
        order.filled_at = fill_timestamp
        return order

    def _process_sell(
        self,
        signal: Signal,
        fill_price_basis: float,
        fill_timestamp: datetime,
        ambiguous_exit: bool = False,
    ) -> Order:
        if signal.symbol not in self.portfolio.positions:
            return self._make_order(
                signal, quantity=0, status=OrderStatus.REJECTED,
                rejection_reason=f"no open position in {signal.symbol} to sell",
            )

        position = self.portfolio.positions[signal.symbol]
        fill_price = compute_slippage(fill_price_basis, SignalAction.SELL, self.config.costs.slippage_rate)
        # The exit leg is costed under the profile the position was OPENED
        # with, so a delivery entry is not cheaply exited at intraday rates.
        costs = compute_order_costs(
            fill_price,
            position.quantity,
            SignalAction.SELL,
            self.config.costs,
            position.charge_profile,
        )
        order = self._make_order(signal, quantity=position.quantity, status=OrderStatus.SUBMITTED)

        self.portfolio.apply_fill_sell(
            symbol=signal.symbol,
            fill_price=fill_price,
            costs=costs,
            timestamp=fill_timestamp,
            exit_reason=signal.reason,
            exit_order_id=order.order_id,
            ambiguous_exit=ambiguous_exit,
        )

        order.status = OrderStatus.FILLED
        order.filled_price = fill_price
        order.filled_quantity = position.quantity
        order.filled_at = fill_timestamp
        return order

    def _viability_rejection_reason(
        self, signal: Signal, fill_price: float, economics: TradeEconomics
    ) -> str:
        """Builds the rejection text, adding the smallest size that WOULD
        clear the charges when one exists — "this needs at least N shares" is
        actionable, "not viable" is not."""

        reason = economics.reason
        min_qty = minimum_viable_quantity(
            entry_price=fill_price,
            costs=self.config.costs,
            policy=self.config.viability,
            stop_loss=signal.stop_loss,
            target=signal.target,
            profile=signal.charge_profile,
        )
        if min_qty is None:
            return (
                f"{reason}. No position size clears these charges at this "
                "entry/stop/target, so the setup itself is uneconomic"
            )
        if min_qty > economics.quantity:
            return (
                f"{reason}. Would need at least {min_qty} shares "
                f"(Rs {min_qty * fill_price:,.2f}) for this setup to clear its own charges, "
                f"but risk sizing allows only {economics.quantity}"
            )
        return reason

    def record_expired_order(self, signal: Signal, reason: str) -> Order:
        """Records a signal that could never be filled (e.g. no next bar to
        fill against, or the fill bar's data was invalid) as a cancelled
        order, so it shows up in the audit trail instead of vanishing."""

        return self._make_order(
            signal, quantity=0, status=OrderStatus.CANCELLED, rejection_reason=reason
        )

    def _make_order(
        self,
        signal: Signal,
        quantity: int,
        status: OrderStatus,
        rejection_reason: str | None = None,
    ) -> Order:
        order = Order(
            order_id=str(uuid.uuid4()),
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            side=signal.action,
            quantity=quantity,
            submitted_at=signal.timestamp,
            status=status,
            rejection_reason=rejection_reason,
            charge_profile=signal.charge_profile,
        )
        self.orders.append(order)
        return order
