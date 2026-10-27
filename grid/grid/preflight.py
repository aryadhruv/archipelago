"""Checks to pass before launching a grid. Each is pass, warn or fail."""

import asyncio
import zipfile
from dataclasses import asdict, dataclass

from docker.errors import DockerException

from grid.envctl import InfraError, client, run_cmd
from grid.inputs import Grid
from grid.worker import AGENTS_DIR, GRADING_DIR

# Asks a runner whether its own .env (or the environment) has each model's
# provider key, with the runner's own check. It prints key names, never values.
_KEY_CHECK = (
    "import sys\n"
    "from runner.utils.env_file import load_env_file, require_provider_keys\n"
    "load_env_file()\n"
    "require_provider_keys(*sys.argv[1:])\n"
)


@dataclass
class Check:
    id: str
    label: str
    status: str  # pass | warn | fail
    detail: str = ""


def _inputs_checks(grid: Grid) -> list[Check]:
    checks: list[Check] = []
    bad_worlds = []
    for world_id in grid.world_ids:
        path = grid.world_zip(world_id)
        if not zipfile.is_zipfile(path):
            bad_worlds.append(f"{world_id} (missing or not a zip)")
            continue
        with zipfile.ZipFile(path) as zf:
            if not any(n.startswith("filesystem/") for n in zf.namelist()):
                bad_worlds.append(f"{world_id} (no filesystem/ folder)")
    checks.append(
        Check(
            "worlds",
            f"{len(grid.world_ids)} worlds readable",
            "fail" if bad_worlds else "pass",
            "; ".join(bad_worlds),
        )
    )

    no_rubric = [t.task_id for t in grid.tasks if not t.rubric]
    bad_criteria = [
        t.task_id for t in grid.tasks if any(not c.get("criteria") for c in t.rubric)
    ]
    missing_files = [
        t.task_id
        for t in grid.tasks
        if t.has_task_files and not grid.task_files_dir(t.task_id).is_dir()
    ]
    problems = []
    if no_rubric:
        problems.append(f"no rubric: {', '.join(no_rubric)}")
    if bad_criteria:
        problems.append(f"empty criteria: {', '.join(bad_criteria)}")
    if missing_files:
        problems.append(f"task_files/ missing: {', '.join(missing_files)}")
    checks.append(
        Check(
            "tasks",
            f"{len(grid.tasks)} tasks resolve",
            "fail" if problems else "pass",
            "; ".join(problems),
        )
    )
    return checks


def _model_checks(grid: Grid) -> list[Check]:
    no_window = [m.id for m in grid.models if m.context_window_tokens <= 0]
    judge = grid.grading_settings["llm_judge_model"]
    judged_self = [m.id for m in grid.models if m.model == judge]
    return [
        Check(
            "context_windows",
            "Every model has context_window_tokens",
            "fail" if no_window else "pass",
            ", ".join(no_window),
        ),
        Check(
            "judge_independent",
            "The judge isn't one of the models being tested",
            "warn" if judged_self else "pass",
            f"{judge} is also tested as {', '.join(judged_self)}"
            if judged_self
            else judge,
        ),
    ]


async def _key_check(id_: str, label: str, cwd, models: list[str]) -> Check:
    code, out = await run_cmd(
        "uv", "run", "--locked", "python", "-c", _KEY_CHECK, *models, cwd=cwd
    )
    lines = [line for line in out.strip().splitlines() if line.startswith("Missing")]
    detail = lines[-1] if lines else (out.strip().splitlines() or [""])[-1]
    return Check(
        id_, label, "pass" if code == 0 else "fail", "" if code == 0 else detail
    )


async def run_preflight(grid: Grid, check_keys: bool = True) -> list[Check]:
    checks = _inputs_checks(grid) + _model_checks(grid)

    try:
        version = (await asyncio.to_thread(lambda: client().version()))["Version"]
        checks.append(Check("docker", "Docker is running", "pass", f"server {version}"))
    except (InfraError, DockerException) as e:
        checks.append(Check("docker", "Docker is running", "fail", str(e)[-300:]))

    if check_keys:
        checks.append(
            await _key_check(
                "agent_keys",
                "Provider keys for the models (agents/.env)",
                AGENTS_DIR,
                [m.model for m in grid.models],
            )
        )
        checks.append(
            await _key_check(
                "judge_key",
                "Provider key for the judge (grading/.env)",
                GRADING_DIR,
                [grid.grading_settings["llm_judge_model"]],
            )
        )
    return checks


def summarize(checks: list[Check]) -> dict:
    return {
        "ok": all(c.status != "fail" for c in checks),
        "checks": [asdict(c) for c in checks],
    }
