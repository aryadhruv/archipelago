import json
import tarfile
import zipfile

import pytest

from grid.inputs import InputError, agent_config_for, load_grid, verifiers_for
from grid.scheduler import grid_order
from grid.worlds import overlay_tarballs, tar_gz_to_zip, world_tarballs


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def grid_dir(tmp_path):
    _write(
        tmp_path / "tasks_and_rubrics.json",
        [
            {
                "task_id": "t1",
                "task_name": "Find a file",
                "world_id": "w1",
                "prompt": "Find it.",
                "rubric": [
                    {"verifier_id": "v1", "criteria": "Found it"},
                    {"criteria": "Said where"},
                ],
            },
            {
                "task_id": "t2",
                "world_id": "w1",
                "prompt": "Other.",
                "rubric": [{"criteria": "x"}],
            },
        ],
    )
    _write(
        tmp_path / "models.json",
        [
            {"id": "a", "model": "openrouter/x/a", "context_window_tokens": 1000},
            {
                "id": "b",
                "model": "openrouter/x/b",
                "context_window_tokens": 2000,
                "max_concurrency": 1,
            },
        ],
    )
    _write(
        tmp_path / "grid.json",
        {"grid_id": "g", "repeats": 2, "judge": {"model": "openrouter/x/j"}},
    )
    world = tmp_path / "world_files_zipped" / "w1.zip"
    world.parent.mkdir()
    with zipfile.ZipFile(world, "w") as zf:
        zf.writestr("filesystem/", "")
        zf.writestr("filesystem/docs/a.txt", "hello")
        zf.writestr(".apps_data/mail/inbox.mbox", "From x")
        zf.writestr("README", "ignored")
    return tmp_path


def test_load_grid(grid_dir):
    grid = load_grid(grid_dir / "grid.json")
    assert [t.task_id for t in grid.tasks] == ["t1", "t2"]
    assert grid.world_ids == ["w1"]
    assert grid.grading_settings["llm_judge_model"] == "openrouter/x/j"
    assert grid.output_dir == grid_dir / "runs"
    assert "toolbelt" in grid.system_prompt
    assert "filesystem_server" in grid.mcp_config["mcpServers"]


def test_order_spreads_models(grid_dir):
    order = grid_order(load_grid(grid_dir / "grid.json"))
    assert order[:4] == [("t1", "a", 1), ("t1", "b", 1), ("t2", "a", 1), ("t2", "b", 1)]
    assert len(order) == 8


def test_context_window_goes_into_agent_config(grid_dir):
    grid = load_grid(grid_dir / "grid.json")
    config = agent_config_for(grid, grid.model("b"))
    assert config["agent_config_values"]["context_window_tokens"] == 2000
    assert "context_window_tokens" not in grid.agent_config["agent_config_values"]


def test_verifiers_from_rubric(grid_dir):
    task = load_grid(grid_dir / "grid.json").task("t1")
    verifiers = verifiers_for(task)
    assert [v["verifier_id"] for v in verifiers] == ["v1", "t1_criterion_1"]
    assert verifiers[0]["verifier_values"]["is_primary_objective"] is True
    assert verifiers[1]["verifier_values"]["is_primary_objective"] is False


def test_rejects_unknown_tasks_and_bad_ids(grid_dir):
    _write(grid_dir / "grid.json", {"tasks": ["nope"], "judge": {"model": "j"}})
    with pytest.raises(InputError, match="Unknown task"):
        load_grid(grid_dir / "grid.json")
    _write(grid_dir / "grid.json", {"judge": {"model": "j"}})
    _write(
        grid_dir / "models.json",
        [{"id": "a/b", "model": "m", "context_window_tokens": 1}],
    )
    with pytest.raises(InputError, match="Model id"):
        load_grid(grid_dir / "grid.json")


def test_world_tarballs_split_and_cache(grid_dir, tmp_path):
    cache = tmp_path / "cache"
    tars = world_tarballs(grid_dir / "world_files_zipped" / "w1.zip", cache)
    assert set(tars) == {"filesystem", ".apps_data"}
    with tarfile.open(tars["filesystem"]) as tar:
        assert "docs/a.txt" in tar.getnames()
        assert tar.extractfile("docs/a.txt").read() == b"hello"
    with tarfile.open(tars[".apps_data"]) as tar:
        assert tar.getnames() == ["mail/inbox.mbox"]
    assert world_tarballs(grid_dir / "world_files_zipped" / "w1.zip", cache) == tars


def test_overlay_and_snapshot_zip(tmp_path):
    overlay = tmp_path / "task_files" / "t1"
    (overlay / "filesystem" / "in").mkdir(parents=True)
    (overlay / "filesystem" / "in" / "b.txt").write_text("b")
    tars = overlay_tarballs(overlay, tmp_path / "cache")
    assert set(tars) == {"filesystem"}

    snapshot = tmp_path / "snap.tar.gz"
    with tarfile.open(snapshot, "w:gz") as tar:
        tar.add(overlay / "filesystem" / "in" / "b.txt", arcname="filesystem/in/b.txt")
    with zipfile.ZipFile(tar_gz_to_zip(snapshot, tmp_path / "snap.zip")) as zf:
        assert zf.read("filesystem/in/b.txt") == b"b"
