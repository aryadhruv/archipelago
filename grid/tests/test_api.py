from grid.store import run_id_for


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
    profile = board["models"][1]["tool_profile"]
    assert profile["filesystem_server"] == 1 and profile["toolbelt"] == 0
    cell = next(c for c in board["matrix"] if c["model_id"] == "a")
    assert cell == {"task_id": "t1", "model_id": "a", "mean_score": 1.0, "n": 1}

    csv = client.get("/api/export.csv").text.splitlines()
    assert csv[0].startswith("run_id,task_id,model_id")
    assert len(csv) == 5


def test_events_and_manual_retry(setup, monkeypatch):
    from grid.server import GridControl

    started = []
    monkeypatch.setattr(GridControl, "start", lambda self: started.append(True))
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
    # An idle grid starts so the retried run gets a worker.
    assert started == [True]


def test_world_files_and_smoke_status(setup):
    _, _, client = setup
    files = client.get("/api/worlds/w1/files").json()
    assert files == {"world_id": "w1", "files": [{"path": "/a.txt", "size_bytes": 1}]}
    assert client.get("/api/worlds/nope/files").status_code == 404

    smoke = client.get("/api/smoke").json()
    assert smoke["state"] == "idle" and smoke["ok"] is None
    assert smoke["task_id"] == "t1"


def test_final_answer_from_the_tool_message():
    from grid.views import final_answer

    trajectory = {
        "final_answer": None,
        "messages": [
            {"role": "assistant", "content": "thinking"},
            {
                "role": "tool",
                "name": "final_answer",
                "content": [{"type": "text", "text": "It's at /a.png"}],
            },
        ],
    }
    assert final_answer(trajectory) == "It's at /a.png"
    assert final_answer({"final_answer": "Direct."}) == "Direct."
    assert final_answer(None) is None
