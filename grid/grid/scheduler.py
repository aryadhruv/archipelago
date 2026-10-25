"""Run a grid: a pool of workers, one environment port each, over the queued runs."""

import asyncio
import contextlib
import json
import time
from collections.abc import Callable
from typing import Any

from grid.envctl import ensure_env_file, ensure_image, git_sha, port_is_free
from grid.inputs import Grid
from grid.store import PENDING_STATES, Run, Store
from grid.worker import Worker
from grid.worlds import overlay_tarballs, world_tarballs

Log = Callable[[str], None]


def grid_order(grid: Grid) -> list[tuple[str, str, int]]:
    """Every (task, model, repeat), repeats outermost and models innermost.

    Concurrent workers then spread over models, and a grid stopped early still
    has comparable coverage: every model on the same tasks.
    """
    return [
        (task.task_id, model.id, repeat)
        for repeat in range(1, grid.repeats + 1)
        for task in grid.tasks
        for model in grid.models
    ]


def allocate_ports(grid: Grid, count: int, scan: int = 200) -> list[int]:
    ports: list[int] = []
    port = grid.port_base
    while len(ports) < count and port < grid.port_base + scan:
        if port_is_free(port):
            ports.append(port)
        port += 1
    if len(ports) < count:
        raise RuntimeError(f"Found only {len(ports)} free ports from {grid.port_base}")
    return ports


class Scheduler:
    def __init__(self, grid: Grid, store: Store, log: Log = print):
        self.grid = grid
        self.store = store
        self.log = log
        self._wake = asyncio.Event()
        self._running: dict[str, int] = {}  # model_id -> runs in flight
        self._claimed: set[str] = set()

    def _model_has_room(self, model_id: str) -> bool:
        limit = self.grid.model(model_id).max_concurrency
        return limit is None or self._running.get(model_id, 0) < limit

    def _next_run(self) -> tuple[Run | None, float | None]:
        """The first runnable run, or the time until a backed-off one is due."""
        now = time.time()
        soonest: float | None = None
        for run in self.store.runs(PENDING_STATES):
            if run.run_id in self._claimed or not self._model_has_room(run.model_id):
                continue
            if run.not_before > now:
                wait = run.not_before - now
                soonest = wait if soonest is None else min(soonest, wait)
                continue
            return run, None
        return None, soonest

    async def _worker_loop(self, worker: Worker, port: int) -> None:
        while True:
            run, wait = self._next_run()
            if run is None:
                if wait is None and not self._claimed:
                    return  # nothing pending and nothing in flight
                self._wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=wait or 5)
                continue
            self._claimed.add(run.run_id)
            self._running[run.model_id] = self._running.get(run.model_id, 0) + 1
            try:
                await worker.execute(run, port)
            except Exception as e:  # a bug, not an outcome: don't take the pool down
                self.log(f"{run.run_id}: worker error {e!r}")
                self.store.add_event("infra", run.run_id, reason=f"worker error: {e!r}")
                self.store.update_run(
                    run.run_id, state="retry", reason=f"worker error: {e!r}",
                    not_before=time.time() + 60, port=None,
                )  # fmt: skip
            finally:
                self._claimed.discard(run.run_id)
                self._running[run.model_id] -= 1
                self._wake.set()

    async def prepare(self, rebuild: bool = False) -> dict[str, Any]:
        """Build or pick the image, pack the worlds, and register the runs."""
        ensure_env_file()
        image = await ensure_image(self.grid.env_image, rebuild=rebuild)
        self.log(f"Environment image: {image}")

        cache = self.grid.output_dir / ".cache"
        worker_tars: dict[str, dict] = {"worlds": {}, "overlays": {}}
        for world_id in self.grid.world_ids:
            self.log(f"Packing world {world_id}")
            worker_tars["worlds"][world_id] = await asyncio.to_thread(
                world_tarballs, self.grid.world_zip(world_id), cache
            )
        for task in self.grid.tasks:
            task_dir = self.grid.task_files_dir(task.task_id)
            if task_dir.is_dir():
                worker_tars["overlays"][task.task_id] = await asyncio.to_thread(
                    overlay_tarballs, task_dir, cache
                )

        added = self.store.add_runs(grid_order(self.grid))
        interrupted = self.store.reset_interrupted()
        if interrupted:
            self.log(f"Resuming {len(interrupted)} interrupted runs")
        manifest = {
            "grid_id": self.grid.grid_id,
            "config": str(self.grid.config_path),
            "image": image,
            "git_sha": await git_sha(),
            "tasks": [t.task_id for t in self.grid.tasks],
            "models": [{"id": m.id, "model": m.model} for m in self.grid.models],
            "repeats": self.grid.repeats,
            "judge": self.grid.grading_settings["llm_judge_model"],
        }
        previous = self.store.get_meta("manifest")
        if previous and previous.get("image") != image:
            self.log(
                f"Warning: this grid started on {previous.get('image')}, now {image}. "
                "Runs before and after aren't on the same build."
            )
        self.store.set_meta("manifest", manifest)
        (self.grid.grid_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2)
        )
        self.log(f"{added} new runs; {self.store.counts()}")
        return {"image": image, **worker_tars}

    async def run(self, rebuild: bool = False) -> dict[str, int]:
        prepared = await self.prepare(rebuild=rebuild)
        worker = Worker(self.grid, self.store, prepared["image"], self.log)
        worker.world_tars = prepared["worlds"]
        worker.overlay_tars = prepared["overlays"]

        pending = len(self.store.runs(PENDING_STATES))
        count = max(1, min(self.grid.max_parallel, pending))
        ports = allocate_ports(self.grid, count)
        self.log(f"Starting {count} workers on ports {ports}")
        self.store.add_event("grid_state", state="running", ports=ports)
        try:
            await asyncio.gather(*(self._worker_loop(worker, p) for p in ports))
        finally:
            self.store.add_event(
                "grid_state", state="stopped", counts=self.store.counts()
            )
        return self.store.counts()
