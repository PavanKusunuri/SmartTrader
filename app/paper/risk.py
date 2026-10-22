"""Position sizing and hard risk limits.

Every simulated entry must pass through check_new_entry_allowed() before
the broker fills it. All limits here are user-configurable (app.config.RiskLimits);
exceeding any of them means NO_TRADE, never a reduced-size trade that
silently ignores the limit.
"""

from __future__ import annotations

from app.config import RiskLimits
from app.paper.portfolio import Portfolio


class RiskManager:
    def __init__(self, limits: RiskLimits):
        self.limits = limits

    def position_size(self, cash: float, entry_price: float, stop_loss: float) -> int:
        """Shares sized so that a stop-loss hit loses roughly
        risk_per_trade_pct of cash, capped by max_position_pct of cash.
        Returns 0 if inputs are invalid or sizing rounds down to nothing."""

        if entry_price <= 0 or stop_loss <= 0 or stop_loss >= entry_price or cash <= 0:
            return 0

        risk_budget = cash * self.limits.risk_per_trade_pct
        stop_distance = entry_price - stop_loss
        qty_by_risk = int(risk_budget // stop_distance)

        max_position_value = cash * self.limits.max_position_pct
        qty_by_max_position = int(max_position_value // entry_price)

        return max(0, min(qty_by_risk, qty_by_max_position))

    def check_new_entry_allowed(
        self,
        portfolio: Portfolio,
        symbol: str,
        quantity: int,
        entry_price: float,
        current_prices: dict[str, float],
    ) -> tuple[bool, str]:
        if self.limits.kill_switch_enabled:
            return False, "kill switch is enabled: no new simulated entries"

        if quantity <= 0:
            return False, "computed position size is zero"

        if symbol in portfolio.positions:
            return False, f"already holding a position in {symbol}"

        if len(portfolio.positions) >= self.limits.max_open_positions:
            return False, f"max open positions reached ({self.limits.max_open_positions})"

        equity = portfolio.equity(current_prices)
        if equity <= 0:
            return False, "portfolio equity is zero or negative"

        new_exposure_value = portfolio.existing_exposure_value() + quantity * entry_price
        if new_exposure_value > self.limits.max_total_exposure_pct * equity:
            return False, (
                f"max total exposure exceeded: {new_exposure_value:.2f} > "
                f"{self.limits.max_total_exposure_pct:.0%} of equity {equity:.2f}"
            )

        daily_pnl = portfolio.daily_pnl(current_prices)
        max_loss = -self.limits.max_daily_loss_pct * portfolio.day_start_equity
        if daily_pnl <= max_loss:
            return False, (
                f"max daily loss reached: daily P&L {daily_pnl:.2f} <= limit {max_loss:.2f}"
            )

        return True, ""
