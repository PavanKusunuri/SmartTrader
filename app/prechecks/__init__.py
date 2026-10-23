"""Strategy pre-checks: an evidence registry plus a gate a strategy must pass.

Research prototypes only. Passing every check is the MINIMUM bar for further
paper-trading study, not evidence of profitability.
"""

from app.prechecks.candidates import CANDIDATES, Candidate, EvidenceGrade
from app.prechecks.gate import CheckResult, PrecheckReport, run_prechecks

__all__ = [
    "CANDIDATES", "Candidate", "EvidenceGrade",
    "CheckResult", "PrecheckReport", "run_prechecks",
]
