"""Validated application configuration.

**Every charge rate in this module is an UNVERIFIED, user-configurable
ASSUMPTION, not a verified-current official rate.** CLAUDE.md section 8
requires verification against official sources before any figure is
presented as current, and none of these have been verified. An attempt to
read Groww's official rate card on 2026-10-22 failed: two charge URLs
returned HTTP 404 and the brokerage calculator renders its numbers
client-side, so only one figure could actually be read from it (a DP-charge
footnote). The defaults below are drawn from third-party aggregator
summaries plus the known structure of Indian cash-equity charges. They exist
so the app has a *realistic* cost model instead of a zero-cost fantasy, and
so small-ticket trades are correctly shown as uneconomic. Replace them with
the figures on your own contract note before drawing any conclusion from a
backtest.

See docs/COSTS_AND_VIABILITY.md for each figure's provenance and how to
override it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models import ChargeProfile


class ProfileCharges(BaseModel):
    """Charges that differ between delivery and intraday settlement.

    Brokerage is modelled as the Indian discount-broker norm: a percentage of
    turnover, capped at a flat per-order ceiling and floored at a per-order
    minimum. With pct=0.1%, cap=Rs 20 and floor=Rs 5, a Rs 380 order pays the
    Rs 5 floor, not Rs 0.38 — which is exactly why tiny orders cannot work.
    UNVERIFIED defaults.
    """

    brokerage_pct: float = Field(default=0.001, ge=0, le=0.05)
    brokerage_cap: float = Field(default=20.0, ge=0)
    brokerage_min: float = Field(default=5.0, ge=0)
    stt_buy_rate: float = Field(default=0.001, ge=0, le=0.05)
    stt_sell_rate: float = Field(default=0.001, ge=0, le=0.05)
    stamp_duty_buy_rate: float = Field(default=0.00015, ge=0, le=0.05)
    # Depository-participant charge on the sell leg: a FLAT rupee amount per
    # scrip per day, not a percentage. This is the charge that most often
    # turns a small "profitable" delivery trade into a net loss.
    dp_charge_per_sell: float = Field(default=18.25, ge=0)
    dp_charge_gst_applicable: bool = Field(default=True)

    @model_validator(mode="after")
    def cap_must_not_be_below_min(self) -> "ProfileCharges":
        if self.brokerage_cap > 0 and self.brokerage_cap < self.brokerage_min:
            raise ValueError("brokerage_cap must be >= brokerage_min")
        return self


def _delivery_defaults() -> ProfileCharges:
    """Delivery (CNC): STT on BOTH legs, DP charge on the sell leg."""

    return ProfileCharges()


def _intraday_defaults() -> ProfileCharges:
    """Intraday (MIS): STT on the sell leg only, no DP charge, lower stamp
    duty. UNVERIFIED defaults."""

    return ProfileCharges(
        stt_buy_rate=0.0,
        stt_sell_rate=0.00025,
        stamp_duty_buy_rate=0.00003,
        dp_charge_per_sell=0.0,
    )


class CostAssumptions(BaseModel):
    """Full per-leg cost model. All rates are UNVERIFIED defaults — see the
    module docstring. Rates shared by both profiles live here; rates that
    differ live on `delivery` / `intraday`."""

    delivery: ProfileCharges = Field(default_factory=_delivery_defaults)
    intraday: ProfileCharges = Field(default_factory=_intraday_defaults)

    # Exchange transaction charge, NSE cash segment, per rupee of turnover.
    exchange_txn_rate: float = Field(default=0.0000297, ge=0, le=0.05)
    # SEBI turnover fee, Rs 10 per crore of turnover.
    sebi_turnover_rate: float = Field(default=0.000001, ge=0, le=0.05)
    # Investor Protection Fund Trust levy, Rs 10 per crore (NSE cash).
    ipft_rate: float = Field(default=0.000001, ge=0, le=0.05)
    # GST on brokerage + exchange txn + SEBI fee + IPFT (+ DP where flagged).
    gst_rate: float = Field(default=0.18, ge=0, le=1)
    slippage_rate: float = Field(default=0.0005, ge=0, le=0.05)

    def for_profile(self, profile: ChargeProfile) -> ProfileCharges:
        """Returns the charge schedule for one settlement profile."""

        if profile == ChargeProfile.DELIVERY:
            return self.delivery
        if profile == ChargeProfile.INTRADAY:
            return self.intraday
        raise ValueError(f"unknown charge profile: {profile}")

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_flat_schema(cls, data):
        """Accepts the pre-viability cost schema still stored in `runs.costs_json`.

        The old model had a single flat `brokerage_per_order` plus top-level
        `stt_sell_rate`/`stamp_duty_buy_rate`. Mapping those onto the new
        nested shape keeps historical runs costed the way they were actually
        run, instead of silently re-pricing them with today's defaults.
        """

        if not isinstance(data, dict):
            return data
        legacy_keys = {"brokerage_per_order", "stt_sell_rate", "stamp_duty_buy_rate"}
        if not (legacy_keys & data.keys()):
            return data
        if "delivery" in data or "intraday" in data:
            return data  # already new-shape; nothing to migrate

        data = dict(data)
        flat = float(data.pop("brokerage_per_order", 0.0) or 0.0)
        stt_sell = float(data.pop("stt_sell_rate", 0.0) or 0.0)
        stamp = float(data.pop("stamp_duty_buy_rate", 0.0) or 0.0)
        legacy_profile = {
            # A flat fee is expressed as pct=0 with floor=cap=the flat fee.
            "brokerage_pct": 0.0,
            "brokerage_cap": flat,
            "brokerage_min": flat,
            "stt_buy_rate": 0.0,
            "stt_sell_rate": stt_sell,
            "stamp_duty_buy_rate": stamp,
            # The old model had no DP charge, SEBI fee or IPFT at all.
            "dp_charge_per_sell": 0.0,
        }
        data["delivery"] = dict(legacy_profile)
        data["intraday"] = dict(legacy_profile)
        data.setdefault("sebi_turnover_rate", 0.0)
        data.setdefault("ipft_rate", 0.0)
        return data


class ViabilityPolicy(BaseModel):
    """Minimum economics a simulated entry must clear before the broker fills it.

    This is the answer to "20 shares at Rs 19, sold at Rs 19.05": gross profit
    Re 1, round-trip charges tens of rupees. Each check below is a separate,
    configurable reason to return NO_TRADE rather than book a trade that only
    looks like a winner because the cost model was ignored.

    Defaults are deliberately strict for a Rs 100,000 virtual account. They
    are a starting point, not a researched optimum.
    """

    enabled: bool = Field(default=True)
    # Floor on order value: below this, flat per-order charges dominate.
    min_order_value: float = Field(default=2500.0, ge=0)
    # Net profit at target must be strictly positive after all charges.
    require_net_profit_at_target: bool = Field(default=True)
    # Gross profit at target must be at least this multiple of round-trip
    # charges, so the trade is not a coin-flip on the broker's fee schedule.
    min_reward_to_cost_ratio: float = Field(default=2.0, ge=0)
    # Round-trip charges as a fraction of entry turnover.
    max_cost_pct_of_turnover: float = Field(default=0.01, ge=0, le=1)
    # Net profit at target vs net loss at stop, after charges on both legs.
    # OFF by default (0 = not checked), unlike the checks above. Charges cut
    # the reward and add to the risk, so a gross 1:1 setup can never show a
    # net ratio of 1.0 — requiring one would silently outlaw every symmetric
    # strategy, including high-win-rate ones that are perfectly sound. That
    # is a view about trade shape and win rate, not cost arithmetic, and this
    # project has not researched it. Set it to 1.0 if you want that view
    # enforced.
    min_net_reward_to_risk: float = Field(default=0.0, ge=0)


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
    docs/STRATEGIES.md and CLAUDE.md section 7.
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
    viability: ViabilityPolicy = Field(default_factory=ViabilityPolicy)
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
