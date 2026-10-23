"""Pre-check gate: minimum evidence a strategy must show before being considered.

Judged on NET results, in-sample AND out-of-sample, on non-synthetic data.
Win rate is intentionally NOT a criterion. A pass means "worth continued paper
study", never "profitable".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.backtest.evaluation import MIN_TRADES_FOR_ANY_CONCLUSION
from app.backtest.metrics import Metrics
from app.prechecks.candidates import Candidate, EvidenceGrade

NON_REAL_DATA_KINDS = {"SAMPLE_SYNTHETIC", "SIMULATED"}
MAX_COST_SHARE_OF_GROSS = 0.5   # costs may not consume more than half of gross profit


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass
class PrecheckReport:
    strategy: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]


def _add(report: PrecheckReport, name: str, ok: bool, detail: str) -> None:
    report.checks.append(CheckResult(name, ok, detail))


def run_prechecks(
    candidate: Candidate,
    in_sample: Metrics,
    out_of_sample: Metrics | None,
    data_kind: str,
) -> PrecheckReport:
    """Runs every check and reports all failures, not just the first."""

    r = PrecheckReport(strategy=candidate.key)

    _add(r, "real_data", data_kind not in NON_REAL_DATA_KINDS,
         f"data_kind={data_kind}" + ("" if data_kind not in NON_REAL_DATA_KINDS
                                     else " — synthetic data cannot show an edge"))

    _add(r, "evidence_not_anecdotal_only", candidate.evidence != EvidenceGrade.ANECDOTAL,
         f"evidence grade: {candidate.evidence.value}"
         + (" — no published support; only your own out-of-sample proof counts"
            if candidate.evidence == EvidenceGrade.ANECDOTAL else ""))

    if out_of_sample is None:
        _add(r, "out_of_sample_present", False, "no out-of-sample result supplied")
        return r
    _add(r, "out_of_sample_present", True, "supplied")

    for label, m in (("in_sample", in_sample), ("out_of_sample", out_of_sample)):
        _add(r, f"{label}_min_trades", m.trade_count >= MIN_TRADES_FOR_ANY_CONCLUSION,
             f"{m.trade_count} trades (need {MIN_TRADES_FOR_ANY_CONCLUSION})")
        _add(r, f"{label}_net_expectancy_positive",
             m.expectancy is not None and m.expectancy > 0,
             f"net expectancy per trade = {m.expectancy}")

    if out_of_sample.benchmark_return_pct is not None:
        beat = out_of_sample.total_return_net_pct > out_of_sample.benchmark_return_pct
        _add(r, "oos_beats_buy_and_hold", beat,
             f"net {out_of_sample.total_return_net_pct:.2%} vs benchmark "
             f"{out_of_sample.benchmark_return_pct:.2%}")

    gross = out_of_sample.gross_pnl
    if gross > 0:
        share = out_of_sample.total_costs / gross
        _add(r, "oos_costs_not_dominant", share <= MAX_COST_SHARE_OF_GROSS,
             f"costs are {share:.0%} of gross profit (limit {MAX_COST_SHARE_OF_GROSS:.0%})")
    else:
        _add(r, "oos_costs_not_dominant", False, "no gross profit out of sample")

    if candidate.cost_sensitivity == "high":
        _add(r, "high_cost_sensitivity_acknowledged", out_of_sample.net_pnl > 0,
             "intraday-style strategies must be net-positive out of sample; "
             "a small cost-rate error can flip the sign")

    return r
