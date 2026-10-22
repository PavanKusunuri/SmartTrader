"""Virtual cash/positions/P&L ledger.

Nothing in this module touches a real brokerage account. All fills are
simulated by app/paper/broker.py and recorded here for bookkeeping only.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from app.models import Position, Trade


class InsufficientCashError(Exception):
    pass


class Portfolio:
    def __init__(self, starting_cash: float):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self.realized_pnl: float = 0.0
        self._current_day: date | None = None
        self._day_start_equity: float = starting_cash

    def mark_new_day(self, day: date, current_prices: dict[str, float]) -> None:
        """Call once per calendar day before evaluating any new signals, so
        max-daily-loss is measured against that day's opening equity."""
        if self._current_day != day:
            self._current_day = day
            self._day_start_equity = self.equity(current_prices)

    def equity(self, current_prices: dict[str, float]) -> float:
        value = self.cash
        for symbol, pos in self.positions.items():
            price = current_prices.get(symbol, pos.avg_entry_price)
            value += pos.quantity * price
        return value

    def daily_pnl(self, current_prices: dict[str, float]) -> float:
        return self.equity(current_prices) - self._day_start_equity

    @property
    def day_start_equity(self) -> float:
        return self._day_start_equity

    def existing_exposure_value(self) -> float:
        return sum(pos.quantity * pos.avg_entry_price for pos in self.positions.values())

    def apply_fill_buy(
        self,
        symbol: str,
        quantity: int,
        fill_price: float,
        costs: float,
        timestamp: datetime,
        stop_loss: float | None,
        target: float | None,
        order_id: str,
    ) -> None:
        if symbol in self.positions:
            raise ValueError(f"already holding a position in {symbol}; duplicate entry")
        total_cost = quantity * fill_price + costs
        if total_cost > self.cash + 1e-6:
            raise InsufficientCashError(
                f"buy {quantity} {symbol} @ {fill_price} + costs {costs} exceeds cash {self.cash}"
            )
        self.cash -= total_cost
        self.positions[symbol] = Position(
            symbol=symbol,
            quantity=quantity,
            avg_entry_price=fill_price,
            stop_loss=stop_loss,
            target=target,
            opened_at=timestamp,
            entry_order_id=order_id,
            entry_costs=costs,
        )

    def apply_fill_sell(
        self,
        symbol: str,
        fill_price: float,
        costs: float,
        timestamp: datetime,
        exit_reason: str,
        exit_order_id: str,
        ambiguous_exit: bool = False,
    ) -> Trade:
        if symbol not in self.positions:
            raise ValueError(f"no open position in {symbol} to sell")
        pos = self.positions.pop(symbol)
        proceeds = pos.quantity * fill_price - costs
        self.cash += proceeds

        gross_pnl = (fill_price - pos.avg_entry_price) * pos.quantity
        total_costs = pos.entry_costs + costs
        net_pnl = gross_pnl - total_costs
        self.realized_pnl += net_pnl

        trade = Trade(
            trade_id=str(uuid.uuid4()),
            symbol=symbol,
            entry_order_id=pos.entry_order_id,
            exit_order_id=exit_order_id,
            entry_price=pos.avg_entry_price,
            exit_price=fill_price,
            quantity=pos.quantity,
            entry_at=pos.opened_at,
            exit_at=timestamp,
            gross_pnl=round(gross_pnl, 2),
            costs=round(total_costs, 2),
            net_pnl=round(net_pnl, 2),
            exit_reason=exit_reason,
            ambiguous_exit=ambiguous_exit,
        )
        self.trades.append(trade)
        return trade

    def reconcile(self, current_prices: dict[str, float]) -> list[str]:
        """Returns a list of problems found (empty = consistent). This is a
        cheap internal consistency check, not a guarantee of correctness."""

        problems: list[str] = []
        if self.cash < -1e-6:
            problems.append(f"negative cash: {self.cash}")
        for symbol, pos in self.positions.items():
            if pos.quantity <= 0:
                problems.append(f"{symbol}: non-positive quantity {pos.quantity}")
        expected_realized = sum(t.net_pnl for t in self.trades)
        if abs(expected_realized - self.realized_pnl) > 0.01:
            problems.append(
                f"realized_pnl mismatch: ledger={self.realized_pnl} vs trades sum={expected_realized}"
            )
        return problems
