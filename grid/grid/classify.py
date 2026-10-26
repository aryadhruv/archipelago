"""Tell the model's failures (scored) from infrastructure errors (rerun).

The rules (also in grid/README.md):
- Agent ``completed`` or ``failed``: grade it. A failure is the model's fault
  (steps or output budget, context overflow, bad request) and counts.
- Agent ``error`` with a wall_clock ``budget_exhausted`` fault: a timeout. It
  counts as a fail with score 0 and isn't graded.
- Any other agent ``error``, ``cancelled``, or no trajectory at all: infra.
  Rerun the agent.
- Grading that isn't ``completed`` with a final score: rerun grading only.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Verdict(StrEnum):
    GRADE = "grade"
    FAIL_TIMEOUT = "fail_timeout"
    RERUN_AGENT = "rerun_agent"
    SCORED = "scored"
    RERUN_GRADING = "rerun_grading"


@dataclass(frozen=True)
class Outcome:
    verdict: Verdict
    reason: str
    score: float | None = None


def is_wall_clock_fault(fault: Any) -> bool:
    return (
        isinstance(fault, dict)
        and fault.get("kind") == "budget_exhausted"
        and fault.get("budget") == "wall_clock"
    )


def classify_agent(trajectory: dict[str, Any] | None) -> Outcome:
    if trajectory is None:
        return Outcome(Verdict.RERUN_AGENT, "agent wrote no trajectory")
    status = trajectory.get("status")
    if status in ("completed", "failed"):
        return Outcome(Verdict.GRADE, f"agent {status}")
    if status == "error" and is_wall_clock_fault(trajectory.get("fault")):
        return Outcome(Verdict.FAIL_TIMEOUT, "agent hit its wall clock", score=0.0)
    error = trajectory.get("error")
    detail = f": {str(error)[:200]}" if error else ""
    return Outcome(Verdict.RERUN_AGENT, f"agent {status}{detail}")


def classify_grading(grades: dict[str, Any] | None) -> Outcome:
    if grades is None:
        return Outcome(Verdict.RERUN_GRADING, "grading wrote no grades")
    status = grades.get("grading_run_status")
    score = (grades.get("scoring_results") or {}).get("final_score")
    if status == "completed" and score is not None:
        return Outcome(Verdict.SCORED, "graded", score=float(score))
    errored = [
        v.get("verifier_id")
        for v in grades.get("verifier_results") or []
        if v.get("status") == "error"
    ]
    detail = f" (verifier errors: {', '.join(map(str, errored))})" if errored else ""
    return Outcome(Verdict.RERUN_GRADING, f"grading {status}{detail}")
