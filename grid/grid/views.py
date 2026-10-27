"""The dashboard API's payloads, built from the grid config and grid.db."""

import csv
import io
import json
import math
import statistics
import time
import zipfile
from pathlib import Path
from typing import Any

from grid.events import TOOLBELT, flatten, iso
from grid.inputs import Grid, verifiers_for
from grid.scheduler import grid_order
from grid.store import ACTIVE_STATES, PENDING_STATES, Run, Store

# A run's state, grouped for display.
PHASES = {
    "queued": "queued",
    "retry": "queued",
    "env_starting": "setup",
    "populating": "setup",
    "apps_starting": "setup",
    "agent_running": "agent",
    "snapshotting": "grading",
    "grading": "grading",
    "scored": "done",
    "gave_up": "done",
}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, NotADirectoryError):
        return None


def _provider(model: str) -> str:
    parts = model.split("/")
    if parts[0] == "openrouter" and len(parts) > 2:
        return parts[1]
    return parts[0]


def _world_info(grid: Grid, world_id: str, names: dict[str, str]) -> dict[str, Any]:
    path = grid.world_zip(world_id)
    file_count = 0
    apps: set[str] = set()
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                subsystem, _, rest = info.filename.partition("/")
                if subsystem == "filesystem":
                    file_count += 1
                elif subsystem == ".apps_data" and "/" in rest:
                    apps.add(rest.split("/", 1)[0])
        size = path.stat().st_size
    except (FileNotFoundError, zipfile.BadZipFile):
        size = 0
    return {
        "world_id": world_id,
        "name": names.get(world_id) or world_id,
        "size_bytes": size,
        "file_count": file_count,
        "apps": sorted(apps),
    }


def grid_view(grid: Grid, store: Store) -> dict[str, Any]:
    descriptions = _read_json(grid.inputs_dir / "world_descriptions.json") or []
    names = {
        d.get("world_id"): d.get("world_name") or d.get("name")
        for d in descriptions
        if isinstance(d, dict)
    }
    manifest = store.get_meta("manifest") or {}
    servers = sorted(grid.mcp_config.get("mcpServers", {}))
    return {
        "grid_id": grid.grid_id,
        "created_at": iso(store.get_meta("created_at")),
        "image_tag": manifest.get("image") or grid.env_image,
        "git_sha": manifest.get("git_sha"),
        "repeats": grid.repeats,
        "max_parallel": grid.max_parallel,
        "max_attempts": grid.max_attempts,
        "judge_model": grid.grading_settings["llm_judge_model"],
        "worlds": [_world_info(grid, w, names) for w in grid.world_ids],
        "tasks": [
            {
                "task_id": t.task_id,
                "task_name": t.task_name,
                "world_id": t.world_id,
                "prompt": t.prompt,
                "criteria_count": len(t.rubric),
                "criteria": [c["criteria"] for c in t.rubric],
                "mcp_servers": servers,
            }
            for t in grid.tasks
        ],
        "models": [
            {
                "id": m.id,
                "label": m.label,
                "model": m.model,
                "provider": _provider(m.model),
                "context_window_tokens": m.context_window_tokens,
                "max_concurrency": m.max_concurrency,
                # The frontend assigns colors from its own palette.
                "color": None,
            }
            for m in grid.models
        ],
        "totals": {
            "tasks": len(grid.tasks),
            "models": len(grid.models),
            "repeats": grid.repeats,
            "runs": len(grid_order(grid)),
        },
    }


def run_row(run: Run) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "task_id": run.task_id,
        "model_id": run.model_id,
        "repeat": run.repeat,
        "attempt": run.agent_attempts,
        "grading_attempt": run.grading_attempts,
        "state": run.state,
        "phase": PHASES.get(run.state, run.state),
        "score": run.score,
        "agent_status": run.agent_status,
        "classification": run.classification,
        "reason": run.reason,
        "port": run.port,
        "started_at": iso(run.started_at),
        "finished_at": iso(run.finished_at),
        "duration_s": run.duration_s,
        "tokens": run.tokens,
        "prompt_tokens": run.prompt_tokens,
        "completion_tokens": run.completion_tokens,
        "steps": run.steps,
        "tool_calls": run.tool_calls,
    }


def runs_view(
    store: Store,
    task: str | None = None,
    model: str | None = None,
    state: str | None = None,
) -> list[dict[str, Any]]:
    return [
        run_row(r)
        for r in store.runs()
        if (task is None or r.task_id == task)
        and (model is None or r.model_id == model)
        and (state is None or r.state == state)
    ]


def _text(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text") for b in content if isinstance(b, dict)]
        return "\n".join(p for p in parts if p) or None
    return None


def final_answer(trajectory: dict[str, Any] | None) -> str | None:
    """The answer the agent submitted: trajectory.final_answer when the runner
    filled it in, otherwise the last final_answer tool message."""
    if not trajectory:
        return None
    if isinstance(trajectory.get("final_answer"), str):
        return trajectory["final_answer"]
    for message in reversed(trajectory.get("messages") or []):
        if message.get("name") == "final_answer":
            return _text(message.get("content"))
    return None


def run_view(grid: Grid, store: Store, run_id: str) -> dict[str, Any]:
    run = store.run(run_id)
    directory = Path(run.attempt_dir) if run.attempt_dir else None
    trajectory = _read_json(directory / "trajectory.json") if directory else None
    grades = _read_json(directory / "grades.json") if directory else None

    criteria_text = {
        v["verifier_id"]: v["verifier_values"]["criteria"]
        for v in verifiers_for(grid.task(run.task_id))
    }
    criteria = []
    for result in (grades or {}).get("verifier_results") or []:
        values = result.get("verifier_result_values") or {}
        score = result.get("score")
        criteria.append(
            {
                "verifier_id": result.get("verifier_id"),
                "criteria": criteria_text.get(result.get("verifier_id")),
                "score": score,
                "passed": score is not None and score >= grid.pass_score,
                "rationale": values.get("grade_rationale") or result.get("message"),
                "status": result.get("status"),
            }
        )
    usage = (trajectory or {}).get("usage") or {}
    return {
        **run_row(run),
        "world_id": grid.task(run.task_id).world_id,
        "attempts": [
            {
                "attempt": a["number"],
                "kind": a["kind"],
                "state": a["outcome"] or "running",
                "reason": a["reason"],
                "started_at": iso(a["started_at"]),
                "finished_at": iso(a["finished_at"]),
            }
            for a in store.attempts(run_id)
        ],
        "usage": {"call_log": usage.get("call_log") or []},
        "final_answer": final_answer(trajectory),
        "grades": None
        if grades is None
        else {
            "final_score": (grades.get("scoring_results") or {}).get("final_score"),
            "grading_run_status": grades.get("grading_run_status"),
            "criteria": criteria,
        },
    }


def events_view(
    store: Store,
    run_id: str | None = None,
    after: int = 0,
    kinds: set[str] | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    events = [flatten(e) for e in store.events(run_id, after=after, limit=limit)]
    if kinds:
        events = [e for e in events if e["kind"] in kinds]
    return events


def summary_view(
    grid: Grid, store: Store, state: str, ports: list[int]
) -> dict[str, Any]:
    runs = store.runs()
    counts = {"queued": 0, "running": 0, "retrying": 0, "scored": 0, "gave_up": 0}
    for run in runs:
        if run.state in ACTIVE_STATES:
            counts["running"] += 1
        elif run.state == "retry":
            counts["retrying"] += 1
        elif run.state in counts:
            counts[run.state] += 1

    started = [r.started_at for r in runs if r.started_at]
    started_at = min(started) if started else None
    now = time.time()
    if runs and not any(r.state in ACTIVE_STATES + PENDING_STATES for r in runs):
        # Finished: the clock stops at the last run's end.
        now = max((r.finished_at for r in runs if r.finished_at), default=now)
    finished = [
        r.finished_at - r.started_at
        for r in runs
        if r.finished_at and r.started_at and r.state == "scored"
    ]
    left = counts["queued"] + counts["running"] + counts["retrying"]
    eta = (
        statistics.mean(finished) * left / max(1, grid.max_parallel)
        if finished and left
        else None
    )
    by_port = {r.port: r for r in runs if r.port and r.state in ACTIVE_STATES}
    return {
        "state": state,
        "counts": counts,
        "total": len(runs),
        "started_at": iso(started_at),
        "elapsed_s": now - started_at if started_at else 0,
        "eta_s": eta,
        "tokens": {
            "prompt": sum(r.prompt_tokens or 0 for r in runs),
            "completion": sum(r.completion_tokens or 0 for r in runs),
        },
        "attempts": sum(r.agent_attempts + r.grading_attempts for r in runs),
        "retries": sum(
            max(0, r.agent_attempts - 1) + max(0, r.grading_attempts - 1) for r in runs
        ),
        "workers": [
            {
                "port": port,
                "run_id": by_port[port].run_id if port in by_port else None,
                "state": by_port[port].state if port in by_port else "idle",
                "health": "ok",
            }
            for port in ports
        ],
    }


def world_files_view(grid: Grid, world_id: str) -> dict[str, Any]:
    """The world's filesystem as the agent sees it (paths from /)."""
    if world_id not in grid.world_ids:
        raise KeyError(world_id)
    files = []
    with zipfile.ZipFile(grid.world_zip(world_id)) as zf:
        for info in zf.infolist():
            subsystem, _, rest = info.filename.partition("/")
            if subsystem == "filesystem" and rest and not info.is_dir():
                files.append({"path": f"/{rest}", "size_bytes": info.file_size})
    return {"world_id": world_id, "files": sorted(files, key=lambda f: f["path"])}


def _ci95(scores: list[float]) -> float | None:
    if len(scores) < 2:
        return None
    return 1.96 * statistics.stdev(scores) / math.sqrt(len(scores))


def leaderboard_view(grid: Grid, store: Store) -> dict[str, Any]:
    runs = store.runs()
    # Every server for every model, zeros included, so profiles line up.
    servers = [*sorted(grid.mcp_config.get("mcpServers", {})), TOOLBELT]
    tool_counts = {m.id: dict.fromkeys(servers, 0) for m in grid.models}
    for model_id, server, count in store.tool_call_counts():
        if model_id in tool_counts:
            tool_counts[model_id][server or "other"] = count

    models = []
    for model in grid.models:
        mine = [r for r in runs if r.model_id == model.id]
        scored = [r for r in mine if r.state == "scored" and r.score is not None]
        scores = [r.score for r in scored]
        tokens = [r.tokens for r in scored if r.tokens is not None]
        durations = [r.duration_s for r in scored if r.duration_s is not None]
        models.append(
            {
                "model_id": model.id,
                "mean_score": statistics.mean(scores) if scores else None,
                "ci95": _ci95(scores),
                "pass_rate": (
                    sum(s >= grid.pass_score for s in scores) / len(scores)
                    if scores
                    else None
                ),
                "runs": len(scored),
                "avg_tokens": statistics.mean(tokens) if tokens else None,
                "avg_duration_s": statistics.mean(durations) if durations else None,
                # Extra attempts after infra or grading errors: a flaky
                # provider shows here, not in the score.
                "errors": sum(
                    max(0, r.agent_attempts - 1) + max(0, r.grading_attempts - 1)
                    for r in mine
                ),
                # Gave up after max_attempts errors; left out of the score.
                "gave_up": sum(r.state == "gave_up" for r in mine),
                # Counted against the model: agent failed or timed out.
                "fails": sum(
                    r.classification == "fail_timeout" or r.agent_status == "failed"
                    for r in scored
                ),
                "tool_profile": tool_counts.get(model.id, {}),
            }
        )
    models.sort(key=lambda m: (m["mean_score"] is None, -(m["mean_score"] or 0)))

    matrix = []
    for task in grid.tasks:
        for model in grid.models:
            scores = [
                r.score
                for r in runs
                if r.task_id == task.task_id
                and r.model_id == model.id
                and r.state == "scored"
                and r.score is not None
            ]
            matrix.append(
                {
                    "task_id": task.task_id,
                    "model_id": model.id,
                    "mean_score": statistics.mean(scores) if scores else None,
                    "n": len(scores),
                }
            )
    return {
        "models": models,
        "matrix": matrix,
        "pass_score": grid.pass_score,
        "rules": (
            "Scored runs only. A failed agent or a wall-clock timeout counts "
            "against the model; runs that gave up on infra errors are left out "
            f"of the score. Pass = score ≥ {grid.pass_score:.2f}."
        ),
    }


EXPORT_COLUMNS = (
    "run_id",
    "task_id",
    "model_id",
    "repeat",
    "state",
    "score",
    "agent_status",
    "classification",
    "attempt",
    "grading_attempt",
    "tokens",
    "prompt_tokens",
    "completion_tokens",
    "steps",
    "tool_calls",
    "duration_s",
    "reason",
)


def export_csv(store: Store) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for run in store.runs():
        writer.writerow(run_row(run))
    return out.getvalue()
