"""Execute one run: environment → populate → /apps → agent → snapshot → grading.

A run's agent attempts and grading attempts are counted separately. A grading
error reruns grading on the same trajectory and snapshots, not the agent.
"""

import asyncio
import contextlib
import json
import os
import signal
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from grid.classify import Outcome, Verdict, classify_agent, classify_grading
from grid.envctl import INFRA_ERRORS, Environment
from grid.events import AgentEventNormalizer, grading_progress, tail_lines
from grid.human import HumanServer
from grid.inputs import (
    REPO_DIR,
    Grid,
    agent_config_for,
    run_mcp_config,
    verifiers_for,
)
from grid.store import Run, Store
from grid.worlds import tar_gz_to_zip

AGENTS_DIR = REPO_DIR / "agents"
GRADING_DIR = REPO_DIR / "grading"

# Seconds before a retry; doubles per attempt, capped.
RETRY_BACKOFF_SEC = 30
RETRY_BACKOFF_MAX_SEC = 600

Log = Callable[[str], None]


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write_json(path: Path, value: Any) -> Path:
    path.write_text(json.dumps(value, indent=2))
    return path


async def _run_logged(
    cmd: list[str],
    cwd: Path,
    log_path: Path,
    env: dict[str, str],
    timeout: float | None,
) -> int | None:
    """Run a command with its output in log_path. None if it was killed at timeout."""
    with log_path.open("wb") as out:
        # Its own process group: killing `uv run` alone leaves its Python
        # child running, still spending tokens and writing into the attempt.
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd, env=env, stdout=out, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )  # fmt: skip
        try:
            return await asyncio.wait_for(proc.wait(), timeout)
        except TimeoutError:
            await _kill_group(proc)
            return None
        except asyncio.CancelledError:
            await _kill_group(proc)
            raise


async def _kill_group(proc: asyncio.subprocess.Process, grace: float = 10) -> None:
    """SIGTERM the process group, then SIGKILL whatever is left after grace."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, sig)
        try:
            await asyncio.wait_for(proc.wait(), grace)
            return
        except TimeoutError:
            continue


@contextlib.asynccontextmanager
async def _tailing(path: Path, on_line: Callable[[str], None]) -> AsyncIterator[None]:
    """Feed lines appended to path to on_line while the block runs, then drain."""
    stop = asyncio.Event()
    task = asyncio.create_task(tail_lines(path, on_line, stop))
    try:
        yield
    finally:
        stop.set()
        await task


class Worker:
    def __init__(self, grid: Grid, store: Store, image: str, log: Log):
        self.grid = grid
        self.store = store
        self.image = image
        self.log = log
        self.world_tars: dict[str, dict[str, Path]] = {}
        self.overlay_tars: dict[str, dict[str, Path]] = {}
        # task_id -> the simulated human's folder on the host (worlds.human_folder).
        self.human_folders: dict[str, Path] = {}

    def _emit(self, run: Run, kind: str, **data: Any) -> int:
        return self.store.add_event(
            kind, run.run_id, model_id=run.model_id, task_id=run.task_id, **data
        )

    def _state(self, run: Run, state: str, **fields: Any) -> None:
        self.store.update_run(run.run_id, state=state, **fields)
        self._emit(run, "run_state", state=state, text=fields.get("reason"), **fields)
        self.log(f"{run.run_id}: {state}")

    async def execute(self, run: Run, port: int) -> None:
        """Advance the run by one attempt and leave it scored, gave_up or retry."""
        if run.started_at is None:
            self.store.update_run(run.run_id, started_at=time.time())
        if run.resume_from == "agent":
            outcome = await self._agent_attempt(run, port)
            if outcome.verdict == Verdict.RERUN_AGENT:
                self._retry_or_give_up(
                    run, "agent", run.agent_attempts + 1 - run.agent_floor, outcome
                )
                return
            if outcome.verdict == Verdict.FAIL_TIMEOUT:
                self._finish(run, outcome)
                return
            self.store.update_run(run.run_id, resume_from="grading")
            run = self.store.run(run.run_id)

        outcome = await self._grading_attempt(run)
        if outcome.verdict == Verdict.RERUN_GRADING:
            self._retry_or_give_up(
                run, "grading", run.grading_attempts + 1 - run.grading_floor, outcome
            )
        else:
            self._finish(run, outcome)

    def _finish(self, run: Run, outcome: Outcome) -> None:
        self._state(
            run,
            "scored",
            score=outcome.score,
            classification=outcome.verdict.value,
            reason=outcome.reason,
            finished_at=time.time(),
            port=None,
        )

    def worker_error(self, run_id: str, error: Exception) -> None:
        """An unexpected exception: count it as an attempt of the current phase."""
        run = self.store.run(run_id)
        kind = run.resume_from
        used = (
            run.agent_attempts - run.agent_floor
            if kind == "agent"
            else run.grading_attempts - run.grading_floor
        )
        verdict = Verdict.RERUN_AGENT if kind == "agent" else Verdict.RERUN_GRADING
        self._retry_or_give_up(
            run, kind, max(used, 1), Outcome(verdict, f"worker error: {error!r}")
        )

    def _retry_or_give_up(
        self, run: Run, kind: str, used: int, outcome: Outcome
    ) -> None:
        if used >= self.grid.max_attempts:
            self._state(
                run,
                "gave_up",
                classification=outcome.verdict.value,
                reason=f"{kind}: {used} attempts; last: {outcome.reason}",
                finished_at=time.time(),
                port=None,
            )
            return
        delay = min(RETRY_BACKOFF_SEC * 2 ** (used - 1), RETRY_BACKOFF_MAX_SEC)
        self._state(
            run,
            "retry",
            classification=outcome.verdict.value,
            reason=outcome.reason,
            not_before=time.time() + delay,
            port=None,
        )

    # agent

    async def _agent_attempt(self, run: Run, port: int) -> Outcome:
        number = run.agent_attempts + 1
        directory = (
            self.grid.grid_dir
            / run.task_id
            / run.model_id
            / f"r{run.repeat}"
            / f"a{number}"
        )
        directory.mkdir(parents=True, exist_ok=True)
        self.store.update_run(
            run.run_id,
            agent_attempts=number,
            attempt_dir=str(directory),
            port=port,
            # The last attempt's outcome no longer describes the run.
            reason=None,
            classification=None,
            agent_status=None,
        )
        attempt_id = self.store.start_attempt(run.run_id, "agent", number, directory)
        try:
            outcome = await self._agent_phase(run, port, directory, number)
        except INFRA_ERRORS as e:
            outcome = Outcome(Verdict.RERUN_AGENT, f"infra: {e!r}")
            self._emit(run, "infra", text=str(e)[:2000])
        self.store.finish_attempt(attempt_id, outcome.verdict.value, outcome.reason)
        return outcome

    async def _agent_phase(
        self, run: Run, port: int, directory: Path, number: int
    ) -> Outcome:
        task = self.grid.task(run.task_id)
        env = Environment(port, self.image)
        human: HumanServer | None = None
        self._state(run, "env_starting", port=port)
        try:
            await env.up()
            self._state(run, "populating")
            layers = [
                self.world_tars[task.world_id],
                self.overlay_tars.get(task.task_id, {}),
            ]
            for layer in layers:
                for subsystem, tarball in layer.items():
                    await env.populate(subsystem, tarball)
            # The state the agent starts from (world plus overlay) is what
            # grading diffs the final state against.
            initial = await env.snapshot(directory / "initial.tar.gz")
            await asyncio.to_thread(tar_gz_to_zip, initial, directory / "initial.zip")
            initial.unlink()

            self._state(run, "apps_starting")
            human_url = None
            if self.grid.human is not None:
                human = HumanServer(
                    self.grid,
                    task,
                    self.human_folders[task.task_id],
                    env.url,
                    directory / "human.log",
                )
                human_url = await human.start()
                await env.check_reaches(human_url)
            await env.configure_apps(run_mcp_config(self.grid, human_url))

            self._state(run, "agent_running")
            agent_started = time.time()
            trajectory = await self._run_agent(run, port, directory, number)
            agent_secs = time.time() - agent_started

            self._state(run, "snapshotting")
            final = await env.snapshot(directory / "final.tar.gz")
            await asyncio.to_thread(tar_gz_to_zip, final, directory / "final.zip")
            final.unlink()
        except INFRA_ERRORS:
            await env.save_logs(directory / "env.log")
            raise
        finally:
            if human is not None:
                await human.stop()
            await env.down()

        trajectory = trajectory or {}
        fields: dict[str, Any] = {
            "agent_status": trajectory.get("status"),
            # A killed or crashed agent writes no time_elapsed; use the clock.
            "duration_s": trajectory.get("time_elapsed") or agent_secs,
        }
        # Without usage (killed at the wall clock, or crashed), keep the
        # running totals counted from the event log.
        if usage := trajectory.get("usage"):
            fields.update(
                tokens=usage.get("total_tokens"),
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                steps=len(usage.get("call_log") or []) or None,
            )
        self.store.update_run(run.run_id, **fields)
        return classify_agent(trajectory or None)

    async def _run_agent(
        self, run: Run, port: int, directory: Path, number: int
    ) -> dict[str, Any] | None:
        task = self.grid.task(run.task_id)
        model = self.grid.model(run.model_id)
        agent_config = agent_config_for(self.grid, model)
        messages = [
            {"role": "system", "content": self.grid.system_prompt},
            {"role": "user", "content": task.prompt},
        ]
        trajectory_path = directory / "trajectory.json"
        cmd = [
            "uv", "run", "--locked", "python", "-m", "runner.main",
            "--trajectory-id", f"{run.run_id}__a{number}",
            "--initial-messages", str(_write_json(directory / "initial_messages.json", messages)),
            "--mcp-gateway-url", f"http://localhost:{port}/mcp/",
            "--agent-config", str(_write_json(directory / "agent_config.json", agent_config)),
            "--orchestrator-model", model.model,
            "--output", str(trajectory_path),
        ]  # fmt: skip
        if model.extra_args:
            extra = _write_json(
                directory / "orchestrator_extra_args.json", model.extra_args
            )
            cmd += ["--orchestrator-extra-args", str(extra)]

        env = dict(os.environ)
        env.update(
            FILE_LOGGING="true",
            FILE_LOG_PATH=str(directory / "agent_events.jsonl"),
            # Every model's window is set explicitly; never guess one.
            CONTEXT_WINDOW_FALLBACK_ENABLED="false",
        )
        wall_clock = agent_config["agent_config_values"].get(
            "wall_clock_timeout_sec", 10800
        )
        self.store.update_run(
            run.run_id, tokens=0, prompt_tokens=0, completion_tokens=0, tool_calls=0
        )
        async with _tailing(directory / "agent_events.jsonl", self._agent_line(run)):
            code = await _run_logged(
                cmd, AGENTS_DIR, directory / "agent.stdout.log", env,
                timeout=float(wall_clock) + self.grid.agent_grace_sec,
            )  # fmt: skip
        if code is None:
            # The harness should stop itself at its wall clock; past that plus
            # the grace period it's still a timeout, which counts as a fail.
            trajectory = {
                "status": "error",
                "fault": {"kind": "budget_exhausted", "budget": "wall_clock"},
                "error": "killed by the grid after the wall clock and grace period",
            }
            _write_json(trajectory_path, trajectory)
            return trajectory
        return _read_json(trajectory_path)

    def _agent_line(self, run: Run) -> Callable[[str], None]:
        normalizer = AgentEventNormalizer(self.grid.mcp_config.get("mcpServers", {}))
        totals = {"prompt": 0, "completion": 0, "calls": 0}

        def on_line(line: str) -> None:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                return
            event = normalizer.normalize(record)
            if event is None:
                return
            kind = event.pop("kind")
            self._emit(run, kind, **event)
            # Running totals, so a live view needn't sum events. The
            # trajectory's own numbers replace them when the run ends.
            if kind == "step" and "prompt_tokens" in event:
                totals["prompt"] += event["prompt_tokens"]
                totals["completion"] += event["completion_tokens"]
            elif kind == "tool_call":
                totals["calls"] += 1
            else:
                return
            self.store.update_run(
                run.run_id,
                prompt_tokens=totals["prompt"],
                completion_tokens=totals["completion"],
                tokens=totals["prompt"] + totals["completion"],
                tool_calls=totals["calls"],
            )

        return on_line

    # grading

    async def _grading_attempt(self, run: Run) -> Outcome:
        number = run.grading_attempts + 1
        directory = Path(run.attempt_dir or "")
        self.store.update_run(run.run_id, grading_attempts=number)
        attempt_id = self.store.start_attempt(run.run_id, "grading", number, directory)
        self._state(run, "grading")
        task = self.grid.task(run.task_id)
        grades_path = directory / "grades.json"
        grades_path.unlink(missing_ok=True)
        cmd = [
            "uv", "run", "--locked", "python", "-m", "runner.main",
            "--grading-run-id", f"{run.run_id}__g{number}",
            "--trajectory-id", f"{run.run_id}__a{run.agent_attempts}",
            "--task-id", task.task_id,
            "--world-id", task.world_id,
            "--initial-snapshot", str(directory / "initial.zip"),
            "--final-snapshot", str(directory / "final.zip"),
            "--trajectory", str(directory / "trajectory.json"),
            "--grading-settings", str(_write_json(directory / "grading_settings.json", self.grid.grading_settings)),
            "--verifiers", str(_write_json(directory / "verifiers.json", verifiers_for(task))),
            "--eval-configs", str(_write_json(directory / "eval_configs.json", self.grid.eval_configs)),
            "--scoring-config", str(_write_json(directory / "scoring_config.json", self.grid.scoring_config)),
            "--output", str(grades_path),
        ]  # fmt: skip
        log_path = directory / f"grading.{number}.stdout.log"

        def on_line(line: str) -> None:
            if (event := grading_progress(line)) is not None:
                self._emit(run, event.pop("kind"), **event)

        async with _tailing(log_path, on_line):
            code = await _run_logged(
                cmd, GRADING_DIR, log_path, dict(os.environ),
                timeout=self.grid.grading_timeout_sec,
            )  # fmt: skip
        if code is None:
            # A hung judge call: rerun grading, not the agent.
            outcome = Outcome(
                Verdict.RERUN_GRADING,
                f"grading killed after {self.grid.grading_timeout_sec}s",
            )
        else:
            outcome = classify_grading(_read_json(grades_path))
        grades = _read_json(grades_path) if outcome.verdict == Verdict.SCORED else None
        self.store.finish_attempt(attempt_id, outcome.verdict.value, outcome.reason)
        if outcome.verdict == Verdict.SCORED:
            self._emit(
                run,
                "grade",
                ok=(outcome.score or 0) >= self.grid.pass_score,
                text=f"Score {outcome.score:.2f}",
                score=outcome.score,
                criteria=[
                    {"verifier_id": v.get("verifier_id"), "score": v.get("score")}
                    for v in (grades or {}).get("verifier_results") or []
                ],
            )
        return outcome
