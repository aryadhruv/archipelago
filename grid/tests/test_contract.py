"""The API serves at least the fields the UI's mock fixtures have.

The fixtures in grid/static/mock are the UI's view of the API. Every key in a
fixture must be in the real response, recursively; the API may add more.
"""

import json
from pathlib import Path

from grid.store import run_id_for

MOCK = Path(__file__).parents[1] / "grid" / "static" / "mock"


def _missing(expected, actual, path="") -> list[str]:
    """Keys in expected that actual lacks, and values of a different JSON type."""
    if expected is not None and actual is not None:
        number = (int, float)
        same = (
            isinstance(expected, number) and isinstance(actual, number)
            if isinstance(expected, number) and not isinstance(expected, bool)
            else type(expected) is type(actual)
        )
        if not same:
            return [
                f"{path}: {type(actual).__name__}, expected {type(expected).__name__}"
            ]
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path or '.'}: expected an object"]
        missing = []
        for key, value in expected.items():
            if key not in actual:
                missing.append(f"{path}.{key}")
            elif value is not None and actual[key] is not None:
                missing += _missing(value, actual[key], f"{path}.{key}")
        return missing
    if isinstance(expected, list) and expected and isinstance(actual, list) and actual:
        return _missing(expected[0], actual[0], f"{path}[0]")
    return []


def _fixture(name: str):
    return json.loads((MOCK / name).read_text())


def test_fixture_dir_exists():
    assert (MOCK / "grid.json").exists()


def test_grid_summary_runs_leaderboard(setup):
    _, _, client = setup
    for name, url in [
        ("grid.json", "/api/grid"),
        ("summary.json", "/api/summary"),
        ("runs.json", "/api/runs"),
        ("leaderboard.json", "/api/leaderboard"),
    ]:
        assert _missing(_fixture(name), client.get(url).json()) == [], name


def test_run_detail_and_events(setup):
    grid, store, client = setup
    run_id = run_id_for("t1", "a", 1)
    detail = _fixture("run_r-0001.json")
    assert _missing(detail, client.get(f"/api/runs/{run_id}").json()) == []

    store.add_event("step", run_id, model_id="a", task_id="t1", tokens=5, text="x")
    events = client.get(f"/api/runs/{run_id}/events").json()
    assert _missing(_fixture("events_r-0001.json")[0], events[-1]) == []


def test_world_files(setup):
    _, _, client = setup
    fixture = next(MOCK.glob("world_*.json"))
    assert (
        _missing(_fixture(fixture.name), client.get("/api/worlds/w1/files").json())
        == []
    )


def test_summary_state_and_phase_enums(setup):
    _, _, client = setup
    assert client.get("/api/summary").json()["state"] in {
        "running",
        "paused",
        "idle",
        "done",
    }
    phases = {r["phase"] for r in client.get("/api/runs").json()}
    assert phases <= {"queued", "setup", "agent", "grading", "done"}
