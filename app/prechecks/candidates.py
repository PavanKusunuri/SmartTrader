"""Registry of researched strategy families and how strong the evidence is.

Ranked by quality of EVIDENCE, deliberately not by win rate. Win rate says
nothing about profit: a 45% win-rate trend strategy with large winners can beat
a 80% win-rate strategy whose rare loss wipes out many wins. See
docs/STRATEGY_RESEARCH.md for sources and caveats (web research, 2026-10-23).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EvidenceGrade(str, Enum):
    ACADEMIC = "academic"        # peer-reviewed / long-sample studies, incl. Indian data
    MIXED = "mixed"              # one positive study, independent replication disputes it
    ANECDOTAL = "anecdotal"      # blogs, vendor material, closed-source scripts


@dataclass(frozen=True)
class Candidate:
    key: str
    horizon: str                 # "swing" or "intraday"
    summary: str
    evidence: EvidenceGrade
    cost_sensitivity: str        # "low" | "high"
    implemented_as: str | None   # strategy in app/strategies, if any
    caveats: tuple[str, ...] = field(default_factory=tuple)


CANDIDATES: tuple[Candidate, ...] = (
    Candidate(
        key="swing_momentum_trend",
        horizon="swing",
        summary="Buy relative-strength leaders above a long trend filter; exit on "
                "trend break or trailing stop. Includes time-series (absolute) momentum.",
        evidence=EvidenceGrade.ACADEMIC,
        cost_sensitivity="low",
        implemented_as="swing (MA trend filter, partial)",
        caveats=(
            "Indian studies (NSE/BSE) report momentum profits, but periods and "
            "horizons disagree; one NSE study finds long-run reversal.",
            "Momentum crashes occur in crisis/recovery phases; a regime filter is needed.",
            "Bhavcopy prices are unadjusted for splits/bonuses.",
        ),
    ),
    Candidate(
        key="swing_pullback_in_uptrend",
        horizon="swing",
        summary="Buy a short-term dip inside an established uptrend with a defined stop.",
        evidence=EvidenceGrade.ANECDOTAL,
        cost_sensitivity="low",
        implemented_as=None,
        caveats=("Typical reported win rate ~35-50% with larger winners; sources are "
                 "blogs/vendors, no out-of-sample proof found.",),
    ),
    Candidate(
        key="swing_volatility_breakout",
        horizon="swing",
        summary="Buy a breakout from a tight base on rising volume.",
        evidence=EvidenceGrade.ANECDOTAL,
        cost_sensitivity="low",
        implemented_as=None,
        caveats=("Edge reported to collapse in ranging markets; claimed win rates "
                 "(60-80%) are unverified and likely survivorship-biased.",),
    ),
    Candidate(
        key="intraday_orb_stocks_in_play",
        horizon="intraday",
        summary="5-minute opening-range breakout, restricted to unusually active "
                "('in play') stocks.",
        evidence=EvidenceGrade.MIXED,
        cost_sensitivity="high",
        implemented_as=None,
        caveats=(
            "Zarattini/Barbon/Aziz (US stocks 2016-2023) report profit after costs.",
            "An independent replication on five index CFDs found gross reproduced "
            "but net ~zero. Not tested on NSE.",
            "Needs intraday data the app does not yet have from a real source.",
        ),
    ),
    Candidate(
        key="intraday_ma_momentum",
        horizon="intraday",
        summary="Existing prototype: MA trend + momentum + volume.",
        evidence=EvidenceGrade.ANECDOTAL,
        cost_sensitivity="high",
        implemented_as="intraday_ma_momentum",
        caveats=("Only run on synthetic data; lost money net of assumed costs.",),
    ),
)
