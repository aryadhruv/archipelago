from grid.classify import Verdict, classify_agent, classify_grading


def test_completed_and_failed_are_graded():
    assert classify_agent({"status": "completed"}).verdict == Verdict.GRADE
    assert classify_agent({"status": "failed"}).verdict == Verdict.GRADE


def test_wall_clock_error_is_a_scored_timeout():
    outcome = classify_agent(
        {
            "status": "error",
            "fault": {"kind": "budget_exhausted", "budget": "wall_clock", "limit": 60},
        }
    )
    assert outcome.verdict == Verdict.FAIL_TIMEOUT
    assert outcome.score == 0.0


def test_other_errors_and_cancelled_rerun_the_agent():
    assert classify_agent({"status": "error"}).verdict == Verdict.RERUN_AGENT
    assert classify_agent({"status": "cancelled"}).verdict == Verdict.RERUN_AGENT
    assert classify_agent(None).verdict == Verdict.RERUN_AGENT
    other_budget = {
        "status": "error",
        "fault": {"kind": "budget_exhausted", "budget": "steps"},
    }
    assert classify_agent(other_budget).verdict == Verdict.RERUN_AGENT


def test_grading_needs_completed_and_a_score():
    ok = {"grading_run_status": "completed", "scoring_results": {"final_score": 0.5}}
    assert classify_grading(ok).verdict == Verdict.SCORED
    assert classify_grading(ok).score == 0.5

    zero = {"grading_run_status": "completed", "scoring_results": {"final_score": 0}}
    assert classify_grading(zero).score == 0.0

    errored = {
        "grading_run_status": "error",
        "scoring_results": {"final_score": None},
        "verifier_results": [{"verifier_id": "v1", "status": "error"}],
    }
    outcome = classify_grading(errored)
    assert outcome.verdict == Verdict.RERUN_GRADING
    assert "v1" in outcome.reason
    assert classify_grading(None).verdict == Verdict.RERUN_GRADING
