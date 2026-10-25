"""Command line: validate a grid, run it, or print its status.

uv run python -m grid validate path/to/grid.json
uv run python -m grid run path/to/grid.json [--rebuild] [--skip-preflight]
uv run python -m grid status path/to/grid.json
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from grid.inputs import InputError, load_grid
from grid.preflight import run_preflight, summarize
from grid.scheduler import Scheduler, grid_order
from grid.store import Store

_MARK = {"pass": "ok  ", "warn": "WARN", "fail": "FAIL"}


def _log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def _print_checks(result: dict) -> None:
    for check in result["checks"]:
        detail = f"  ({check['detail']})" if check["detail"] else ""
        print(f"  {_MARK[check['status']]} {check['label']}{detail}")


async def _validate(args) -> int:
    grid = load_grid(args.config)
    runs = len(grid_order(grid))
    print(
        f"Grid {grid.grid_id}: {len(grid.tasks)} tasks x {len(grid.models)} models "
        f"x {grid.repeats} repeats = {runs} runs, up to {grid.max_parallel} at once"
    )
    result = summarize(await run_preflight(grid, check_keys=not args.no_keys))
    _print_checks(result)
    return 0 if result["ok"] else 1


async def _run(args) -> int:
    grid = load_grid(args.config)
    if not args.skip_preflight:
        result = summarize(await run_preflight(grid))
        _print_checks(result)
        if not result["ok"]:
            print("Preflight failed; fix the checks above or pass --skip-preflight.")
            return 1
    store = Store(grid.grid_dir / "grid.db")
    try:
        counts = await Scheduler(grid, store, _log).run(rebuild=args.rebuild)
    finally:
        store.close()
    _log(f"Done: {counts}")
    return 0 if not counts.get("gave_up") else 2


def _status(args) -> int:
    grid = load_grid(args.config)
    path = grid.grid_dir / "grid.db"
    if not path.exists():
        print(f"No runs yet at {path}")
        return 1
    store = Store(path)
    try:
        print(json.dumps(store.counts()))
        for run in store.runs():
            score = "" if run.score is None else f" score={run.score:.2f}"
            reason = f" ({run.reason})" if run.reason and run.state != "scored" else ""
            print(f"  {run.state:13} {run.run_id}{score}{reason}")
    finally:
        store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grid")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="check the config, inputs, Docker and keys")
    p.add_argument("config", type=Path)
    p.add_argument("--no-keys", action="store_true", help="skip the provider key check")

    p = sub.add_parser("run", help="run (or resume) the grid")
    p.add_argument("config", type=Path)
    p.add_argument(
        "--rebuild", action="store_true", help="rebuild the environment image"
    )
    p.add_argument("--skip-preflight", action="store_true")

    p = sub.add_parser("status", help="print each run's state")
    p.add_argument("config", type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            return asyncio.run(_validate(args))
        if args.command == "run":
            return asyncio.run(_run(args))
        return _status(args)
    except InputError as e:
        print(f"Invalid grid: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; `run` again to resume.", file=sys.stderr)
        return 130
