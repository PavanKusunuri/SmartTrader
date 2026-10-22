"""Virtual broker: the ONLY place a Signal turns into an Order.

PAPER TRADING — NO REAL ORDERS. This module never calls a live broker API
and has no network access. Every fill here is a simulation based on the
current bar's price plus configured slippage/costs.
"""

from __future__ import annotations

import uuid

from app.backtest.costs import compute_order_costs, compute_slippage
from app.config import AppConfig
from app.models import Order, OrderStatus, Signal, SignalAction
from app.paper.portfolio import Portfolio
from app.paper.risk import RiskManager


class PaperBroker:
    """PAPER TRADING — NO REAL ORDERS. Simulated fills only."""

    def __init__(self, config: AppConfig, portfolio: Portfolio | None = None):
        self.config = config
        self.portfolio = portfolio or Portfolio(config.starting_cash)
        self.risk = RiskManager(config.risk)
        self.orders: list[Order] = []
        self._processed_signal_ids: set[str] = set()

    def process_signal(
        self,
        signal: Signal,
        fill_price_basis: float,
        current_prices: dict[str, float],
    ) -> Order | None:
        """Turns a Signal into a simulated Order, or returns None for
        NO_TRADE signals (no order is created at all — there is nothing to
        reject or fill).

        `fill_price_basis` is the price the fill is simulated from (this
        milestone uses the signal bar's close; see app/backtest/engine.py
        for why that choice is explicit and documented rather than implicit).
        """

        if signal.action == SignalAction.NO_TRADE:
            return None

        if signal.signal_id in self._processed_signal_ids:
            return self._make_order(
                signal, quantity=0, status=OrderStatus.REJECTED,
                rejection_reason="duplicate signal_id already processed",
            )
        self._processed_signal_ids.add(signal.signal_id)

        if signal.action == SignalAction.BUY:
            return self._process_buy(signal, fill_price_basis, current_prices)
        if signal.action == SignalAction.SELL:
            return self._process_sell(signal, fill_price_basis)

        raise ValueError(f"unhandled signal action: {signal.action}")

    def _process_buy(
        self, signal: Signal, fill_price_basis: float, current_prices: dict[str, float]
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
        costs = compute_order_costs(fill_price, quantity, SignalAction.BUY, self.config.costs)
        order = self._make_order(signal, quantity=quantity, status=OrderStatus.SUBMITTED)

        try:
            self.portfolio.apply_fill_buy(
                symbol=signal.symbol,
                quantity=quantity,
                fill_price=fill_price,
                costs=costs,
                timestamp=signal.timestamp,
                stop_loss=signal.stop_loss,
                target=signal.target,
                order_id=order.order_id,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as a rejected order, not a crash
            order.status = OrderStatus.REJECTED
            order.rejection_reason = str(exc)
            return order

        order.status = OrderStatus.FILLED
        order.filled_price = fill_price
        order.filled_quantity = quantity
        order.filled_at = signal.timestamp
        return order

    def _process_sell(self, signal: Signal, fill_price_basis: float) -> Order:
        if signal.symbol not in self.portfolio.positions:
            return self._make_order(
                signal, quantity=0, status=OrderStatus.REJECTED,
                rejection_reason=f"no open position in {signal.symbol} to sell",
            )

        position = self.portfolio.positions[signal.symbol]
        fill_price = compute_slippage(fill_price_basis, SignalAction.SELL, self.config.costs.slippage_rate)
        costs = compute_order_costs(fill_price, position.quantity, SignalAction.SELL, self.config.costs)
        order = self._make_order(signal, quantity=position.quantity, status=OrderStatus.SUBMITTED)

        self.portfolio.apply_fill_sell(
            symbol=signal.symbol,
            fill_price=fill_price,
            costs=costs,
            timestamp=signal.timestamp,
            exit_reason=signal.reason,
            exit_order_id=order.order_id,
        )

        order.status = OrderStatus.FILLED
        order.filled_price = fill_price
        order.filled_quantity = position.quantity
        order.filled_at = signal.timestamp
        return order

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
        )
        self.orders.append(order)
        return order
