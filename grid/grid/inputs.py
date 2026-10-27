"""Load a grid: grid.json, models.json and an inputs folder in the HF apex-agents layout.

The inputs folder holds the same files as the ``mercor/apex-agents`` dataset:

    tasks_and_rubrics.json            [{task_id, task_name, world_id, prompt, rubric, ...}]
    world_descriptions.json           [{world_id, ...}] (optional)
    world_files_zipped/<world_id>.zip filesystem/ and optional .apps_data/
    task_files/<task_id>/             optional per-task overlay, same two subsystems

Relative paths in grid.json resolve against the folder grid.json is in.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULTS_DIR = Path(__file__).parent / "defaults"
REPO_DIR = Path(__file__).resolve().parents[2]

# Model ids name run directories and run ids, so keep them path-safe.
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class InputError(ValueError):
    """The grid's configuration or inputs are malformed."""


@dataclass(frozen=True)
class Model:
    id: str
    model: str
    context_window_tokens: int
    label: str = ""
    extra_args: dict[str, Any] | None = None
    max_concurrency: int | None = None


@dataclass(frozen=True)
class Task:
    task_id: str
    task_name: str
    world_id: str
    prompt: str
    rubric: list[dict[str, Any]]
    has_task_files: bool


@dataclass
class Grid:
    grid_id: str
    config_path: Path
    inputs_dir: Path
    output_dir: Path
    tasks: list[Task]
    models: list[Model]
    repeats: int
    max_parallel: int
    max_attempts: int
    port_base: int
    agent_config: dict[str, Any]
    system_prompt: str
    mcp_config: dict[str, Any]
    grading_settings: dict[str, Any]
    eval_configs: list[dict[str, Any]]
    scoring_config: dict[str, Any]
    env_image: str | None = None
    # Seconds past the harness's own wall clock before the grid kills the agent.
    agent_grace_sec: int = 600
    # Seconds before the grid kills a grading attempt and reruns grading.
    grading_timeout_sec: int = 3600
    # The score a run needs to count as a pass on the leaderboard.
    pass_score: float = 1.0
    world_ids: list[str] = field(default_factory=list)

    @property
    def grid_dir(self) -> Path:
        return self.output_dir / self.grid_id

    def world_zip(self, world_id: str) -> Path:
        return self.inputs_dir / "world_files_zipped" / f"{world_id}.zip"

    def task_files_dir(self, task_id: str) -> Path:
        return self.inputs_dir / "task_files" / task_id

    def model(self, model_id: str) -> Model:
        return next(m for m in self.models if m.id == model_id)

    def task(self, task_id: str) -> Task:
        return next(t for t in self.tasks if t.task_id == task_id)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError as e:
        raise InputError(f"Missing file: {path}") from e
    except json.JSONDecodeError as e:
        raise InputError(f"Invalid JSON in {path}: {e}") from e


def _resolve(base: Path, value: str | None, default: Path) -> Path:
    if not value:
        return default
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def _load_models(path: Path) -> list[Model]:
    raw = _read_json(path)
    if not isinstance(raw, list) or not raw:
        raise InputError(f"{path} must be a non-empty list of models")
    models: list[Model] = []
    for entry in raw:
        try:
            model = Model(
                id=entry["id"],
                model=entry["model"],
                context_window_tokens=int(entry["context_window_tokens"]),
                label=entry.get("label") or entry["id"],
                extra_args=entry.get("extra_args") or None,
                max_concurrency=entry.get("max_concurrency"),
            )
        except KeyError as e:
            raise InputError(f"Model entry in {path} is missing {e}: {entry}") from e
        if not _ID_PATTERN.match(model.id):
            raise InputError(f"Model id {model.id!r} must match {_ID_PATTERN.pattern}")
        models.append(model)
    ids = [m.id for m in models]
    if len(set(ids)) != len(ids):
        raise InputError(f"Duplicate model ids in {path}")
    return models


def _load_tasks(inputs_dir: Path, selected: Any) -> list[Task]:
    raw = _read_json(inputs_dir / "tasks_and_rubrics.json")
    tasks = [
        Task(
            task_id=t["task_id"],
            task_name=t.get("task_name") or t["task_id"],
            world_id=t["world_id"],
            prompt=t["prompt"],
            rubric=list(t.get("rubric") or []),
            has_task_files=bool(t.get("task_input_files")),
        )
        for t in raw
    ]
    if selected in (None, "all"):
        return tasks
    if not isinstance(selected, list):
        raise InputError('"tasks" must be "all" or a list of task ids')
    by_id = {t.task_id: t for t in tasks}
    missing = [task_id for task_id in selected if task_id not in by_id]
    if missing:
        raise InputError(f"Unknown task ids: {missing}")
    return [by_id[task_id] for task_id in selected]


def load_grid(config_path: Path) -> Grid:
    config_path = config_path.resolve()
    base = config_path.parent
    cfg = _read_json(config_path)

    inputs_dir = _resolve(base, cfg.get("inputs"), base)
    tasks = _load_tasks(inputs_dir, cfg.get("tasks", "all"))
    if not tasks:
        raise InputError("The grid has no tasks")
    models = _load_models(_resolve(base, cfg.get("models"), base / "models.json"))

    judge = cfg.get("judge") or {}
    if not judge.get("model"):
        raise InputError('grid.json needs "judge": {"model": ...}')

    agent_config = _read_json(
        _resolve(base, cfg.get("agent_config"), DEFAULTS_DIR / "agent_config.json")
    )

    grid = Grid(
        grid_id=cfg.get("grid_id") or config_path.stem,
        config_path=config_path,
        inputs_dir=inputs_dir,
        output_dir=_resolve(base, cfg.get("output_dir"), base / "runs"),
        tasks=tasks,
        models=models,
        repeats=int(cfg.get("repeats", 1)),
        max_parallel=int(cfg.get("max_parallel", 2)),
        max_attempts=int(cfg.get("max_attempts", 3)),
        port_base=int(cfg.get("port_base", 8100)),
        agent_config=agent_config,
        system_prompt=_resolve(
            base, cfg.get("system_prompt"), DEFAULTS_DIR / "system_prompt.md"
        )
        .read_text()
        .strip(),
        mcp_config=_read_json(
            _resolve(base, cfg.get("mcp_config"), DEFAULTS_DIR / "mcp_config.json")
        ),
        grading_settings={
            "llm_judge_model": judge["model"],
            "llm_judge_extra_args": judge.get("extra_args"),
        },
        eval_configs=_read_json(
            _resolve(base, cfg.get("eval_configs"), DEFAULTS_DIR / "eval_configs.json")
        ),
        scoring_config=_read_json(
            _resolve(
                base, cfg.get("scoring_config"), DEFAULTS_DIR / "scoring_config.json"
            )
        ),
        env_image=cfg.get("env_image"),
        agent_grace_sec=int(cfg.get("agent_grace_sec", 600)),
        grading_timeout_sec=int(cfg.get("grading_timeout_sec", 3600)),
        pass_score=float(cfg.get("pass_score", 1.0)),
    )
    grid.world_ids = sorted({t.world_id for t in tasks})
    if not _ID_PATTERN.match(grid.grid_id):
        raise InputError(f"grid_id {grid.grid_id!r} must match {_ID_PATTERN.pattern}")
    if grid.repeats < 1 or grid.max_parallel < 1 or grid.max_attempts < 1:
        raise InputError("repeats, max_parallel and max_attempts must be at least 1")
    return grid


def agent_config_for(grid: Grid, model: Model) -> dict[str, Any]:
    """The grid's agent config with this model's context window filled in."""
    config = json.loads(json.dumps(grid.agent_config))
    values = config.setdefault("agent_config_values", {})
    values["context_window_tokens"] = model.context_window_tokens
    return config


def verifiers_for(task: Task) -> list[dict[str, Any]]:
    """One output_llm verifier per rubric criterion, as the HF example builds them."""
    return [
        {
            "verifier_id": c.get("verifier_id") or f"{task.task_id}_criterion_{i}",
            "verifier_version": 1,
            "world_id": task.world_id,
            "task_id": task.task_id,
            "eval_config_id": "ec_output_llm",
            "verifier_values": {
                "criteria": c["criteria"],
                "is_primary_objective": i == 0,
            },
            "verifier_index": i,
            "verifier_dependencies": None,
        }
        for i, c in enumerate(task.rubric)
    ]
