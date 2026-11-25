"""Checks to pass before launching a grid. Each is pass, warn or fail."""

import asyncio
import json
import zipfile
from dataclasses import asdict, dataclass

import httpx
from docker.errors import DockerException

from grid.envctl import InfraError, client, run_cmd
from grid.inputs import Grid
from grid.worker import AGENTS_DIR, GRADING_DIR

# Asks a runner whether its own .env (or the environment) has each model's
# provider key, with the runner's own check. It prints key names, never values.
# Behind a LiteLLM proxy the check asks the proxy for its key and model list.
_KEY_CHECK = (
    "import sys\n"
    "from runner.utils.env_file import load_env_file, require_provider_keys\n"
    "load_env_file()\n"
    "require_provider_keys(*sys.argv[1:])\n"
)

# OpenRouter's public model list. A model it no longer serves returns 404 on
# every call, which the grid would otherwise retry as an agent error.
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


# One short call per model, through the agent runner's own LLM path (keys,
# proxy routing, extra_args), with one tool it is asked to call. Catches models
# that 404, reject tools, or write the call as text instead of making it,
# before a grid spends its runs on them. Prints one ROUND_TRIP line of JSON.
_ROUND_TRIP = """
import asyncio, json, sys
from runner.utils.env_file import load_env_file
load_env_file()
from runner.utils.llm import generate_response

TOOL = {"type": "function", "function": {
    "name": "echo",
    "description": "Echo the given text back.",
    "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                   "required": ["text"]}}}
PROMPT = "Call the echo tool with text set to ok. Do not reply in text."

async def probe(spec):
    try:
        response = await generate_response(
            spec["model"], [{"role": "user", "content": PROMPT}], [TOOL],
            90, spec.get("extra_args") or {}, max_attempts=2)
    except Exception as e:
        return {"id": spec["id"], "status": "error",
                "error": type(e).__name__, "detail": str(e)[:200]}
    message = response.choices[0].message if response.choices else None
    calls = getattr(message, "tool_calls", None) or []
    if any(c.function.name == "echo" for c in calls):
        return {"id": spec["id"], "status": "ok"}
    text = getattr(message, "content", None) or ""
    return {"id": spec["id"], "status": "text" if "echo" in text else "no_call",
            "detail": text[:120]}

async def main():
    specs = json.loads(sys.argv[1])
    results = await asyncio.gather(*(probe(s) for s in specs))
    print("ROUND_TRIP " + json.dumps(results))

asyncio.run(main())
"""
ROUND_TRIP_TIMEOUT_SEC = 300


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


async def _openrouter_check(models: list[str]) -> Check | None:
    """Fail if OpenRouter no longer serves a model; warn if the list is unreachable."""
    wanted = sorted(
        {m.removeprefix("openrouter/") for m in models if m.startswith("openrouter/")}
    )
    if not wanted:
        return None
    label = "OpenRouter serves every model"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(OPENROUTER_MODELS_URL)
            response.raise_for_status()
            served = {m["id"] for m in response.json()["data"]}
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        return Check("openrouter_models", label, "warn", f"couldn't list models: {e!r}")
    missing = [m for m in wanted if m not in served]
    return Check(
        "openrouter_models",
        label,
        "fail" if missing else "pass",
        f"not served: {', '.join(missing)}" if missing else "",
    )


# Errors that will come back on every call: the model id, the key, or the
# request (tools, params) is wrong. Anything else (rate limits, timeouts, 5xx,
# dropped connections) may clear by itself, and a run retries those, so it
# only warns rather than blocking the grid.
LASTING_ERRORS = frozenset(
    {
        "NotFoundError",
        "AuthenticationError",
        "PermissionDeniedError",
        "BadRequestError",
        "UnsupportedParamsError",
        "UnprocessableEntityError",
    }
)


def _round_trip_result(results: list[dict]) -> Check:
    """Fail on an error that will repeat; warn on anything else that isn't a tool call."""
    label = "Every model answers a call and calls a tool"
    failed, warned = [], []
    for r in results:
        status = r.get("status")
        if status == "ok":
            continue
        if status == "error":
            line = f"{r['id']}: {r.get('error')} {r.get('detail', '')}".strip()
            (failed if r.get("error") in LASTING_ERRORS else warned).append(line)
        elif status == "text":
            warned.append(f"{r['id']}: wrote the tool call as text")
        else:
            warned.append(f"{r['id']}: didn't call the tool")
    status = "fail" if failed else "warn" if warned else "pass"
    return Check("round_trip", label, status, "; ".join(failed + warned))


async def _round_trip_check(grid: Grid) -> Check:
    specs = [
        {"id": m.id, "model": m.model, "extra_args": m.extra_args} for m in grid.models
    ]
    label = "Every model answers a call and calls a tool"
    try:
        code, out = await asyncio.wait_for(
            run_cmd(
                "uv",
                "run",
                "--locked",
                "python",
                "-c",
                _ROUND_TRIP,
                json.dumps(specs),
                cwd=AGENTS_DIR,
                own_group=True,
            ),
            ROUND_TRIP_TIMEOUT_SEC,
        )
    except TimeoutError:
        return Check(
            "round_trip", label, "warn", f"no answer in {ROUND_TRIP_TIMEOUT_SEC}s"
        )
    for line in reversed(out.splitlines()):
        if line.startswith("ROUND_TRIP "):
            try:
                return _round_trip_result(json.loads(line.removeprefix("ROUND_TRIP ")))
            except (ValueError, KeyError, TypeError):
                break
    tail = (out.strip().splitlines() or [""])[-1][-300:]
    return Check("round_trip", label, "warn", f"probe didn't finish ({code}): {tail}")


async def run_preflight(grid: Grid, check_keys: bool = True) -> list[Check]:
    checks = _inputs_checks(grid) + _model_checks(grid)

    try:
        version = (await asyncio.to_thread(lambda: client().version()))["Version"]
        checks.append(Check("docker", "Docker is running", "pass", f"server {version}"))
    except (InfraError, DockerException) as e:
        checks.append(Check("docker", "Docker is running", "fail", str(e)[-300:]))

    if check_keys:
        agent_keys = await _key_check(
            "agent_keys",
            "Provider keys or proxy for the models (agents/.env)",
            AGENTS_DIR,
            [m.model for m in grid.models],
        )
        checks.append(agent_keys)
        checks.append(
            await _key_check(
                "judge_key",
                "Provider key or proxy for the judge (grading/.env)",
                GRADING_DIR,
                [grid.grading_settings["llm_judge_model"]],
            )
        )
        if grid.human is not None:
            # The grid passes agents/.env's keys to the simulated human.
            checks.append(
                await _key_check(
                    "human_key",
                    "Provider key or proxy for the simulated human (agents/.env)",
                    AGENTS_DIR,
                    [grid.human.model],
                )
            )
        models = [m.model for m in grid.models] + [
            grid.grading_settings["llm_judge_model"]
        ]
        if grid.human is not None:
            models.append(grid.human.model)
        openrouter = await _openrouter_check(models)
        if openrouter:
            checks.append(openrouter)
        # Only worth the calls once the keys are there and every model is served.
        if agent_keys.status == "pass" and (
            openrouter is None or openrouter.status != "fail"
        ):
            checks.append(await _round_trip_check(grid))
    return checks


def summarize(checks: list[Check]) -> dict:
    return {
        "ok": all(c.status != "fail" for c in checks),
        "checks": [asdict(c) for c in checks],
    }
