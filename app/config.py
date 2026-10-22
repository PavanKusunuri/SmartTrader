"""Validated application configuration.

Cost values below (brokerage, STT, stamp duty, GST, slippage) are
user-configurable ASSUMPTIONS, not verified-current official rates. CLAUDE.md
requires treating them as explicit assumptions unless verified against an
official source, and none have been verified as of Milestone 1. Override them
in your own config if you want different numbers; the defaults exist only so
the backtest has *some* cost model instead of assuming zero cost.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class CostAssumptions(BaseModel):
    """Per-trade cost model. All rates are UNVERIFIED defaults — see module docstring."""

    brokerage_per_order: float = Field(default=0.0, ge=0)
    stt_sell_rate: float = Field(default=0.00025, ge=0, le=0.05)
    exchange_txn_rate: float = Field(default=0.0000345, ge=0, le=0.05)
    gst_rate: float = Field(default=0.18, ge=0, le=1)
    stamp_duty_buy_rate: float = Field(default=0.00015, ge=0, le=0.05)
    slippage_rate: float = Field(default=0.0005, ge=0, le=0.05)


class RiskLimits(BaseModel):
    """Hard risk controls enforced by app/paper/risk.py before any simulated entry."""

    risk_per_trade_pct: float = Field(default=0.01, gt=0, le=1)
    max_position_pct: float = Field(default=0.2, gt=0, le=1)
    max_total_exposure_pct: float = Field(default=0.6, gt=0, le=1)
    max_daily_loss_pct: float = Field(default=0.03, gt=0, le=1)
    max_open_positions: int = Field(default=5, ge=1)
    kill_switch_enabled: bool = Field(default=False)


class SwingStrategyConfig(BaseModel):
    """Parameters for the Milestone-1 swing MA-trend-filter prototype.

    These defaults are EXPERIMENTAL, not a claim of profitability — see
    docs/STRATEGIES.md (to be written) and CLAUDE.md section 7.
    """

    fast_ma_period: int = Field(default=20, ge=2)
    slow_ma_period: int = Field(default=50, ge=2)
    min_holding_days: int = Field(default=3, ge=1)
    max_holding_days: int = Field(default=20, ge=1)
    stop_loss_pct: float = Field(default=0.05, gt=0, le=1)
    target_pct: float = Field(default=0.10, gt=0, le=1)

    @field_validator("slow_ma_period")
    @classmethod
    def slow_must_exceed_fast(cls, v: int, info) -> int:
        fast = info.data.get("fast_ma_period")
        if fast is not None and v <= fast:
            raise ValueError("slow_ma_period must be greater than fast_ma_period")
        return v


class IntradayStrategyConfig(BaseModel):
    """Parameters for the Milestone-2 intraday prototype.

    EXPERIMENTAL defaults, not a profitability claim. Intraday trading has
    materially higher cost and slippage sensitivity than swing trading; see
    docs/RISK_AND_LIMITATIONS.md.
    """

    fast_ma_period: int = Field(default=9, ge=2)
    slow_ma_period: int = Field(default=21, ge=2)
    volume_ma_period: int = Field(default=20, ge=2)
    # Entry requires current volume above this multiple of recent average
    # volume, as a crude "is anyone actually trading this move" filter.
    volume_surge_multiple: float = Field(default=1.5, gt=0)
    stop_loss_pct: float = Field(default=0.01, gt=0, le=1)
    target_pct: float = Field(default=0.02, gt=0, le=1)

    @field_validator("slow_ma_period")
    @classmethod
    def slow_must_exceed_fast(cls, v: int, info) -> int:
        fast = info.data.get("fast_ma_period")
        if fast is not None and v <= fast:
            raise ValueError("slow_ma_period must be greater than fast_ma_period")
        return v


class AppConfig(BaseModel):
    """Top-level configuration. Construct via AppConfig() for defaults, or
    AppConfig.model_validate(dict_from_file) for loaded config."""

    starting_cash: float = Field(default=100_000.0, gt=0)
    timezone: str = Field(default="Asia/Kolkata")
    db_path: str = Field(default="data/paper_trading.db")
    costs: CostAssumptions = Field(default_factory=CostAssumptions)
    risk: RiskLimits = Field(default_factory=RiskLimits)
    swing: SwingStrategyConfig = Field(default_factory=SwingStrategyConfig)
    intraday: IntradayStrategyConfig = Field(default_factory=IntradayStrategyConfig)

    @field_validator("timezone")
    @classmethod
    def timezone_must_be_kolkata(cls, v: str) -> str:
        if v != "Asia/Kolkata":
            raise ValueError(
                "Only Asia/Kolkata is supported in v1; this app trades Indian exchanges only."
            )
        return v


def load_config() -> AppConfig:
    """Load configuration with defaults. Raises pydantic.ValidationError on
    invalid values so the app fails fast at startup rather than later."""

    return AppConfig()
