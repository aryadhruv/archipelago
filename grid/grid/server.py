"""The dashboard's HTTP API: REST over grid.db, an SSE event stream, and controls.

    uv run --locked python -m grid serve path/to/grid.json [--start]

Binds 127.0.0.1 by default. The scheduler runs in the same process, started
from the UI or with --start.
"""

import asyncio
import contextlib
import dataclasses
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from grid import views
from grid.events import flatten
from grid.inputs import Grid
from grid.preflight import run_preflight, summarize
from grid.scheduler import Log, Scheduler
from grid.store import ACTIVE_STATES, PENDING_STATES, Store

STATIC_DIR = Path(__file__).parent / "static"
STREAM_POLL_SEC = 0.5
STREAM_HEARTBEAT_SEC = 15
STREAM_BATCH = 500
# A new connection starts this many events back, so a fresh page has context.
STREAM_BACKFILL = 200


class GridControl:
    """Starts, pauses and cancels the scheduler for the API."""

    def __init__(self, grid: Grid, store: Store, log: Log, rebuild: bool = False):
        self.grid = grid
        self.store = store
        self.log = log
        self.rebuild = rebuild
        self.scheduler = Scheduler(grid, store, log)
        self.task: asyncio.Task | None = None
        self.error: str | None = None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    @property
    def state(self) -> str:
        if self.running:
            return "paused" if self.scheduler.paused else "running"
        if self.error:
            return "failed"
        runs = self.store.runs()
        if runs and not any(r.state in PENDING_STATES + ACTIVE_STATES for r in runs):
            return "done"
        return "idle"

    def start(self) -> None:
        if self.running:
            return
        self.error = None
        self.scheduler = Scheduler(self.grid, self.store, self.log)
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            await self.scheduler.run(rebuild=self.rebuild)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.error = repr(e)
            self.log(f"Grid failed: {e!r}")
            self.store.add_event("grid_state", state="failed", text=repr(e))

    def pause(self) -> None:
        if self.running:
            self.scheduler.set_paused(True)

    def resume(self) -> None:
        if self.running:
            self.scheduler.set_paused(False)

    async def cancel(self) -> None:
        """Stop every worker. Their environments come down; runs requeue."""
        if not self.running or self.task is None:
            return
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.task
        requeued = self.store.reset_interrupted()
        self.store.add_event("grid_state", state="cancelled", requeued=requeued)


class Smoke:
    """One cheap run per model on the first task, in its own grid and grid.db.

    The fairness rules ask for this before a full grid, so provider quirks
    (tool-call formats, image input, rate limits) show up before they cost
    a grid's worth of scores.
    """

    def __init__(self, grid: Grid, log: Log):
        self.grid = dataclasses.replace(
            grid,
            grid_id=f"{grid.grid_id}-smoke",
            tasks=grid.tasks[:1],
            repeats=1,
            max_parallel=min(grid.max_parallel, len(grid.models)),
        )
        self.log = log
        self.task: asyncio.Task | None = None
        self.error: str | None = None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def start(self) -> None:
        if not self.running:
            self.error = None
            self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        store = Store(self.grid.grid_dir / "grid.db")
        try:
            await Scheduler(self.grid, store, self.log).run()
        except Exception as e:
            self.error = repr(e)
            self.log(f"Smoke failed: {e!r}")
        finally:
            store.close()

    def status(self) -> dict[str, Any]:
        path = self.grid.grid_dir / "grid.db"
        runs: list[dict[str, Any]] = []
        if path.exists():
            store = Store(path)
            try:
                runs = views.runs_view(store)
            finally:
                store.close()
        done = bool(runs) and all(r["phase"] == "done" for r in runs)
        state = "running" if self.running else ("done" if done else "idle")
        return {
            "state": state,
            # Every model finished a run, whatever its score: the point is that
            # each one can call the tools and get graded.
            "ok": None
            if state != "done"
            else all(r["state"] == "scored" for r in runs),
            "task_id": self.grid.tasks[0].task_id,
            "runs": runs,
            "error": self.error,
        }


class _RevalidatedStatic(StaticFiles):
    """Static files the browser must revalidate, so an updated UI loads
    without a hard reload (unchanged files still come back as 304)."""

    def file_response(self, *args: Any, **kwargs: Any):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def _attempt_file(store: Store, run_id: str, name: str) -> Path:
    try:
        run = store.run(run_id)
    except KeyError as e:
        raise HTTPException(404, f"No run {run_id}") from e
    if not run.attempt_dir:
        raise HTTPException(404, "The run hasn't started")
    root = Path(run.attempt_dir).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, f"No file {name}")
    return path


def create_app(grid: Grid, store: Store, control: GridControl) -> FastAPI:
    preflight: dict[str, Any] = {}
    smoke = Smoke(grid, control.log)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await control.cancel()

    app = FastAPI(title="Grid", lifespan=lifespan)
    # Endpoints that touch the control are async: they must run on the
    # event loop the scheduler runs on, not in the threadpool.

    @app.get("/api/grid")
    def get_grid() -> dict[str, Any]:
        return views.grid_view(grid, store)

    @app.get("/api/preflight")
    async def get_preflight(refresh: bool = False) -> dict[str, Any]:
        if refresh or not preflight:
            preflight.clear()
            preflight.update(summarize(await run_preflight(grid)))
        return preflight

    @app.post("/api/grid/start")
    async def start() -> dict[str, str]:
        if smoke.running:
            raise HTTPException(409, "The smoke test is running")
        control.start()
        return {"state": control.state}

    @app.post("/api/grid/pause")
    async def pause() -> dict[str, str]:
        control.pause()
        return {"state": control.state}

    @app.post("/api/grid/resume")
    async def resume() -> dict[str, str]:
        control.resume()
        return {"state": control.state}

    @app.post("/api/grid/cancel")
    async def cancel() -> dict[str, str]:
        await control.cancel()
        return {"state": control.state}

    @app.post("/api/smoke")
    async def start_smoke() -> dict[str, Any]:
        # Both would take ports from port_base.
        if control.running:
            raise HTTPException(409, "The grid is running")
        smoke.start()
        return smoke.status()

    @app.get("/api/smoke")
    def smoke_status() -> dict[str, Any]:
        return smoke.status()

    @app.get("/api/worlds/{world_id}/files")
    def world_files(world_id: str) -> dict[str, Any]:
        try:
            return views.world_files_view(grid, world_id)
        except KeyError as e:
            raise HTTPException(404, f"No world {world_id}") from e

    @app.get("/api/summary")
    def summary() -> dict[str, Any]:
        state = control.state
        result = views.summary_view(
            grid,
            store,
            # A failed grid reads as stopped; `error` says why.
            "idle" if state == "failed" else state,
            control.scheduler.ports,
        )
        result["error"] = control.error
        return result

    @app.get("/api/runs")
    def runs(
        task: str | None = None, model: str | None = None, state: str | None = None
    ) -> list[dict[str, Any]]:
        return views.runs_view(store, task=task, model=model, state=state)

    @app.get("/api/runs/{run_id}")
    def run(run_id: str) -> dict[str, Any]:
        try:
            return views.run_view(grid, store, run_id)
        except KeyError as e:
            raise HTTPException(404, f"No run {run_id}") from e

    @app.get("/api/runs/{run_id}/events")
    def run_events(
        run_id: str, after: int = 0, limit: int = 5000
    ) -> list[dict[str, Any]]:
        return views.events_view(store, run_id=run_id, after=after, limit=limit)

    @app.post("/api/runs/{run_id}/retry")
    async def retry(run_id: str) -> dict[str, Any]:
        try:
            current = store.run(run_id)
        except KeyError as e:
            raise HTTPException(404, f"No run {run_id}") from e
        if current.state in ACTIVE_STATES:
            raise HTTPException(409, "The run is in flight")
        # A fresh start: a new agent attempt, then grading, each with the
        # full max_attempts again. Attempt numbers keep counting up, so
        # earlier attempts' files stay.
        store.update_run(
            run_id,
            state="queued",
            resume_from="agent",
            agent_floor=current.agent_attempts,
            grading_floor=current.grading_attempts,
            not_before=0,
            score=None,
            classification=None,
            reason="manual retry",
            finished_at=None,
        )
        store.add_event(
            "run_state",
            run_id,
            model_id=current.model_id,
            task_id=current.task_id,
            state="queued",
            text="manual retry",
        )
        # A finished grid has no workers left; start them for this run.
        if not control.running and not smoke.running:
            control.start()
        return views.run_row(store.run(run_id))

    @app.get("/api/runs/{run_id}/trajectory")
    def trajectory(run_id: str) -> FileResponse:
        return FileResponse(_attempt_file(store, run_id, "trajectory.json"))

    @app.get("/api/runs/{run_id}/grades")
    def grades(run_id: str) -> FileResponse:
        return FileResponse(_attempt_file(store, run_id, "grades.json"))

    @app.get("/api/runs/{run_id}/files/{name:path}")
    def run_file(run_id: str, name: str) -> FileResponse:
        return FileResponse(_attempt_file(store, run_id, name))

    @app.get("/api/leaderboard")
    def leaderboard() -> dict[str, Any]:
        return views.leaderboard_view(grid, store)

    @app.get("/api/export.csv")
    def export_csv() -> PlainTextResponse:
        return PlainTextResponse(
            views.export_csv(store),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{grid.grid_id}.csv"'
            },
        )

    @app.get("/api/export.json")
    def export_json() -> dict[str, Any]:
        return {
            "grid": views.grid_view(grid, store),
            "runs": views.runs_view(store),
            "leaderboard": views.leaderboard_view(grid, store),
        }

    @app.get("/api/stream")
    async def stream(
        request: Request,
        run: str | None = None,
        kinds: str | None = None,
        after: int | None = None,
    ) -> StreamingResponse:
        """Server-sent events. Reconnects resume from Last-Event-ID; a new
        connection starts STREAM_BACKFILL events back unless ?after= says
        otherwise."""
        header = request.headers.get("last-event-id")
        if header and header.isdigit():
            cursor = int(header)
        elif after is not None:
            cursor = after
        else:
            cursor = max(0, store.max_seq() - STREAM_BACKFILL)
        wanted = set(kinds.split(",")) if kinds else None

        async def generate() -> AsyncIterator[str]:
            nonlocal cursor
            idle = 0.0
            yield "retry: 2000\n\n"
            while not await request.is_disconnected():
                rows = store.events(run, after=cursor, limit=STREAM_BATCH)
                for row in rows:
                    cursor = row["seq"]
                    event = flatten(row)
                    if wanted and event["kind"] not in wanted:
                        continue
                    yield f"id: {cursor}\ndata: {json.dumps(event, default=str)}\n\n"
                if rows:
                    idle = 0.0
                    if len(rows) == STREAM_BATCH:
                        continue
                else:
                    idle += STREAM_POLL_SEC
                    if idle >= STREAM_HEARTBEAT_SEC:
                        idle = 0.0
                        yield ": keepalive\n\n"
                await asyncio.sleep(STREAM_POLL_SEC)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if STATIC_DIR.is_dir():
        app.mount(
            "/", _RevalidatedStatic(directory=STATIC_DIR, html=True), name="static"
        )
    return app


async def serve(
    grid: Grid,
    store: Store,
    log: Log,
    host: str = "127.0.0.1",
    port: int = 7070,
    start: bool = False,
    rebuild: bool = False,
) -> None:
    import uvicorn

    control = GridControl(grid, store, log, rebuild=rebuild)
    app = create_app(grid, store, control)
    if start:
        control.start()
    log(f"Dashboard on http://{host}:{port}")
    server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level="warning")
    )
    await server.serve()
