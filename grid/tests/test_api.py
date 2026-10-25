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
        {"grid_id": "g", "repeats": 2, "judge": {"model": "j/j"}},
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
            "usage": {"call_log": [{"prompt_tokens": 10, "completion_tokens": 2}]},
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


def test_grid(setup):
    _, _, client = setup
    body = client.get("/api/grid").json()
    assert body["totals"] == {"tasks": 1, "models": 2, "repeats": 2, "runs": 4}
    assert body["worlds"][0] == {
        "world_id": "w1",
        "name": "World",
        "size_bytes": body["worlds"][0]["size_bytes"],
        "file_count": 1,
        "apps": ["mail"],
    }
    assert body["tasks"][0]["criteria_count"] == 1
    assert [m["provider"] for m in body["models"]] == ["vendor-a", "vendor-b"]


def test_runs_and_summary(setup):
    _, _, client = setup
    runs = client.get("/api/runs").json()
    assert len(runs) == 4
    assert client.get("/api/runs", params={"model": "b"}).json()[0]["phase"] == "agent"

    summary = client.get("/api/summary").json()
    assert summary["counts"] == {
        "queued": 2,
        "running": 1,
        "retrying": 0,
        "scored": 1,
        "gave_up": 0,
    }
    assert summary["tokens"] == {"prompt": 10, "completion": 2}
    assert summary["state"] == "idle"


def test_run_detail(setup):
    _, _, client = setup
    body = client.get(f"/api/runs/{run_id_for('t1', 'a', 1)}").json()
    assert body["final_answer"] == "Done."
    assert body["usage"]["call_log"][0]["prompt_tokens"] == 10
    criterion = body["grades"]["criteria"][0]
    assert criterion == {
        "verifier_id": "v1",
        "criteria": "Did it",
        "score": 1.0,
        "passed": True,
        "rationale": "It did.",
        "status": "ok",
    }
    assert client.get("/api/runs/nope").status_code == 404


def test_files_stay_inside_the_attempt(setup):
    _, _, client = setup
    run_id = run_id_for("t1", "a", 1)
    assert client.get(f"/api/runs/{run_id}/trajectory").json()["status"] == "completed"
    assert (
        client.get(f"/api/runs/{run_id}/files/../../../../grid.db").status_code == 404
    )


def test_leaderboard_and_export(setup):
    _, _, client = setup
    board = client.get("/api/leaderboard").json()
    first = board["models"][0]
    assert first["model_id"] == "a"
    assert first["errors"] == 0 and first["gave_up"] == 0
    assert board["pass_score"] == 1.0 and "gave up" in board["rules"]
    assert (
        first["mean_score"] == 1.0 and first["pass_rate"] == 1.0 and first["runs"] == 1
    )
    assert board["models"][1]["tool_profile"] == {"filesystem_server": 1}
    cell = next(c for c in board["matrix"] if c["model_id"] == "a")
    assert cell == {"task_id": "t1", "model_id": "a", "mean_score": 1.0, "n": 1}

    csv = client.get("/api/export.csv").text.splitlines()
    assert csv[0].startswith("run_id,task_id,model_id")
    assert len(csv) == 5


def test_events_and_manual_retry(setup):
    _, store, client = setup
    run_id = run_id_for("t1", "b", 1)
    events = client.get(f"/api/runs/{run_id}/events").json()
    assert (
        events[0]["kind"] == "tool_call" and events[0]["server"] == "filesystem_server"
    )
    assert client.post(f"/api/runs/{run_id}/retry").status_code == 409

    done = run_id_for("t1", "a", 1)
    assert client.post(f"/api/runs/{done}/retry").json()["state"] == "queued"
    run = store.run(done)
    assert run.agent_floor == 1 and run.resume_from == "agent"


def test_world_files_and_smoke_status(setup):
    _, _, client = setup
    files = client.get("/api/worlds/w1/files").json()
    assert files == {"world_id": "w1", "files": [{"path": "/a.txt", "size_bytes": 1}]}
    assert client.get("/api/worlds/nope/files").status_code == 404

    smoke = client.get("/api/smoke").json()
    assert smoke["state"] == "idle" and smoke["ok"] is None
    assert smoke["task_id"] == "t1"
