import asyncio
import os
import time

from grid.store import run_id_for
from grid.worker import Worker, _run_logged


def test_timeout_kills_the_child_too(tmp_path):
    pid_file = tmp_path / "child.pid"
    # Like `uv run python ...`: a parent whose child does the work.
    cmd = ["sh", "-c", f"sleep 60 & echo $! > {pid_file}; wait"]
    code = asyncio.run(
        _run_logged(cmd, tmp_path, tmp_path / "out.log", dict(os.environ), 0.5)
    )
    assert code is None
    child = int(pid_file.read_text())
    time.sleep(0.2)
    try:
        os.kill(child, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    assert not alive


def test_worker_errors_count_against_max_attempts(setup):
    grid, store, _ = setup
    worker = Worker(grid, store, "image", lambda _: None)
    run_id = run_id_for("t1", "a", 1)
    for attempt in range(1, grid.max_attempts + 1):
        store.update_run(run_id, agent_attempts=attempt)
        worker.worker_error(run_id, RuntimeError("boom"))
    run = store.run(run_id)
    assert run.state == "gave_up"
    assert "worker error" in run.reason
