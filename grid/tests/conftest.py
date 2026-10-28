import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from grid.inputs import load_grid
from grid.scheduler import grid_order
from grid.server import GridControl, create_app
from grid.store import Store, run_id_for


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def setup(tmp_path):
    _write(
        tmp_path / "tasks_and_rubrics.json",
        [
            {
                "task_id": "t1",
                "world_id": "w1",
                "prompt": "Do it.",
                "rubric": [{"verifier_id": "v1", "criteria": "Did it"}],
            }
        ],
    )
    _write(
        tmp_path / "world_descriptions.json",
        [{"world_id": "w1", "world_name": "World"}],
    )
    _write(
        tmp_path / "models.json",
        [
            {
                "id": "a",
                "model": "openrouter/vendor-a/a",
                "context_window_tokens": 1000,
            },
            {
                "id": "b",
                "model": "openrouter/vendor-b/b",
                "context_window_tokens": 1000,
            },
        ],
    )
    _write(
        tmp_path / "grid.json",
        # With a simulated human, so the API serves what the demo fixtures show.
        {
            "grid_id": "g",
            "repeats": 2,
            "judge": {"model": "j/j"},
            "human": {"model": "h/h"},
        },
    )
    world = tmp_path / "world_files_zipped" / "w1.zip"
    world.parent.mkdir()
    with zipfile.ZipFile(world, "w") as zf:
        zf.writestr("filesystem/a.txt", "a")
        zf.writestr(".apps_data/mail/inbox.mbox", "m")

    grid = load_grid(tmp_path / "grid.json")
    store = Store(grid.grid_dir / "grid.db")
    store.add_runs(grid_order(grid))

    # One finished run with artifacts, one in flight.
    done = run_id_for("t1", "a", 1)
    attempt = grid.grid_dir / "t1" / "a" / "r1" / "a1"
    attempt.mkdir(parents=True)
    _write(
        attempt / "trajectory.json",
        {
            "status": "completed",
            "final_answer": "Done.",
            "usage": {
                "call_log": [
                    {"prompt_tokens": 10, "completion_tokens": 2, "reasoning_tokens": 0}
                ]
            },
        },
    )
    _write(
        attempt / "grades.json",
        {
            "grading_run_status": "completed",
            "scoring_results": {"final_score": 1.0},
            "verifier_results": [
                {
                    "verifier_id": "v1",
                    "score": 1.0,
                    "status": "ok",
                    "verifier_result_values": {"grade_rationale": "It did."},
                }
            ],
        },
    )
    store.update_run(
        done, state="scored", score=1.0, attempt_dir=str(attempt), agent_attempts=1,
        grading_attempts=1, tokens=12, prompt_tokens=10, completion_tokens=2,
        duration_s=5.0, started_at=100.0, finished_at=110.0, agent_status="completed",
        classification="scored",
    )  # fmt: skip
    store.update_run(
        run_id_for("t1", "b", 1), state="agent_running", port=8100, agent_attempts=1
    )
    store.add_event(
        "tool_call", run_id_for("t1", "b", 1), model_id="b", task_id="t1",
        server="filesystem_server", tool="read_file", ref="x",
    )  # fmt: skip
    control = GridControl(grid, store, log=lambda _: None)
    return grid, store, TestClient(create_app(grid, store, control))
