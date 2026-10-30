import json
import tarfile
import zipfile

import pytest

from grid import human as human_module
from grid.human import HumanServer, bind_address
from grid.inputs import (
    HUMAN_SERVER,
    InputError,
    agent_config_for,
    load_grid,
    run_mcp_config,
    verifiers_for,
)
from grid.scheduler import grid_order
from grid.worlds import (
    human_folder,
    overlay_tarballs,
    tar_gz_to_zip,
    world_tarballs,
)


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


def _with_human(grid_dir, human, personas=None):
    config = json.loads((grid_dir / "grid.json").read_text())
    _write(grid_dir / "grid.json", {**config, "human": human})
    if personas is not None:
        _write(grid_dir / "world_descriptions.json", personas)
    return load_grid(grid_dir / "grid.json")


def test_no_human_leaves_mcp_config_alone(grid_dir):
    grid = load_grid(grid_dir / "grid.json")
    assert grid.human is None
    assert HUMAN_SERVER not in grid.mcp_config["mcpServers"]
    assert run_mcp_config(grid) is grid.mcp_config
    assert "ask_human" not in grid.system_prompt


def test_human_needs_a_model(grid_dir):
    with pytest.raises(InputError, match="human"):
        _with_human(grid_dir, {"max_questions": 3})


@pytest.mark.parametrize(
    "human, match",
    [
        ({"model": 3}, "model string"),
        ({"model": "  "}, "model string"),
        ({"model": "m", "extra_args": "fast"}, "must be an object"),
        ({"model": "m", "extra_args": {"model": "x", "tools": []}}, "model, tools"),
        ({"model": "m", "max_questions": "abc"}, "whole number"),
        ({"model": "m", "max_questions": "3"}, "whole number"),
        ({"model": "m", "max_questions": True}, "whole number"),
        ({"model": "m", "max_questions": 2.5}, "whole number"),
        ({"model": "m", "max_questions": -1}, "whole number"),
    ],
)
def test_human_rejects_bad_settings(grid_dir, human, match):
    with pytest.raises(InputError, match=match):
        _with_human(grid_dir, human)


def test_human_accepts_a_cap_and_extra_args(grid_dir):
    grid = _with_human(
        grid_dir,
        {"model": "m", "extra_args": {"num_retries": 8}, "max_questions": 0},
    )
    assert grid.human.extra_args == {"num_retries": 8}
    assert grid.human.max_questions == 0


def test_human_run_config_points_at_the_host(grid_dir):
    grid = _with_human(
        grid_dir,
        {"model": "openrouter/x/h"},
        [{"world_id": "w1", "human": {"persona": "Terse manager."}}],
    )
    assert grid.human.max_questions is None
    assert grid.personas == {"w1": "Terse manager."}
    assert "human_server_ask_human" in grid.system_prompt
    # Listed with the grid's servers until each run's URL replaces it.
    assert grid.mcp_config["mcpServers"][HUMAN_SERVER]["transport"] == "http"

    url = "http://host.docker.internal:4321/tok/mcp"
    config = run_mcp_config(grid, url)
    assert config["mcpServers"][HUMAN_SERVER] == {"transport": "http", "url": url}
    # The human runs on the host: nothing in the sandbox's config carries env.
    assert "env" not in config["mcpServers"][HUMAN_SERVER]
    others = {k: v for k, v in config["mcpServers"].items() if k != HUMAN_SERVER}
    assert others == {
        k: v for k, v in grid.mcp_config["mcpServers"].items() if k != HUMAN_SERVER
    }


def test_human_server_env(grid_dir, tmp_path):
    grid = _with_human(
        grid_dir,
        {
            "model": "openrouter/x/h",
            "extra_args": {"temperature": 0},
            "max_questions": 2,
        },
        [{"world_id": "w1", "human": {"persona": "Terse manager."}}],
    )
    folder = tmp_path / "folder"
    server = HumanServer(
        grid, grid.tasks[0], folder, "http://localhost:8200", tmp_path / "h.log"
    )
    env = server._env("127.0.0.1", 4321, "/tok/mcp")
    assert env["HUMAN_MODEL"] == "openrouter/x/h"
    assert json.loads(env["HUMAN_EXTRA_ARGS"]) == {"temperature": 0}
    assert env["HUMAN_PERSONA"] == "Terse manager."
    assert env["HUMAN_TASK_PROMPT"] == "Find it."
    assert env["HUMAN_MAX_QUESTIONS"] == "2"
    assert env["HUMAN_DATA_ROOT"] == str(folder / "human")
    assert env["HUMAN_AGENT_FILES"] == str(folder / "agent_files.txt")
    assert env["HUMAN_ENV_URL"] == "http://localhost:8200"
    assert env["HUMAN_ENV_FILE"].endswith("agents/.env")
    assert (env["MCP_HOST"], env["MCP_PORT"], env["MCP_PATH"]) == (
        "127.0.0.1",
        "4321",
        "/tok/mcp",
    )


def test_human_default_persona_is_empty(grid_dir, tmp_path):
    grid = _with_human(grid_dir, {"model": "openrouter/x/h"})
    server = HumanServer(grid, grid.tasks[0], tmp_path, "http://e", tmp_path / "h.log")
    env = server._env("127.0.0.1", 1, "/p")
    # Empty: the server falls back to its default persona.
    assert env["HUMAN_PERSONA"] == ""
    assert env["HUMAN_MAX_QUESTIONS"] == ""


def test_human_folder_stays_out_of_the_tarballs(tmp_path):
    world = tmp_path / "w.zip"
    with zipfile.ZipFile(world, "w") as zf:
        zf.writestr("filesystem/docs/a.txt", "hello")
        zf.writestr(".apps_data/mail/inbox.mbox", "From x")
        zf.writestr(".apps_data/human/knowledge.md", "world notes")
        zf.writestr(".apps_data/human/files/list.csv", "a,b")
    overlay = tmp_path / "task_files" / "t1"
    (overlay / ".apps_data" / "human").mkdir(parents=True)
    (overlay / ".apps_data" / "human" / "knowledge.md").write_text("task notes")
    (overlay / "filesystem").mkdir()
    (overlay / "filesystem" / "b.txt").write_text("b")

    cache = tmp_path / "cache"
    with tarfile.open(world_tarballs(world, cache)[".apps_data"]) as tar:
        assert tar.getnames() == ["mail/inbox.mbox"]
    assert ".apps_data" not in overlay_tarballs(overlay, cache)

    folder = human_folder(world, overlay, cache)
    # The overlay's knowledge wins; the world's files stay.
    assert (folder / "human" / "knowledge.md").read_text() == "task notes"
    assert (folder / "human" / "files" / "list.csv").read_text() == "a,b"
    assert (folder / "agent_files.txt").read_text() == "b.txt\ndocs/a.txt\n"
    assert human_folder(world, overlay, cache) == folder
    # Without an overlay, the world's alone.
    plain = human_folder(world, tmp_path / "missing", cache)
    assert (plain / "human" / "knowledge.md").read_text() == "world notes"


def test_bind_address_falls_back_to_loopback(monkeypatch):
    def no_docker():
        raise human_module.DockerException("no docker")

    bind_address.cache_clear()
    monkeypatch.setattr(human_module, "client", no_docker)
    try:
        assert bind_address() == "127.0.0.1"
    finally:
        bind_address.cache_clear()
