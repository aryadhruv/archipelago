"""Drive one environment container: compose up/down and the sandbox HTTP API."""

import asyncio
import hashlib
import os
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

import httpx

from grid.inputs import REPO_DIR

ENVIRONMENT_DIR = REPO_DIR / "environment"
IMAGE_REPO = "archipelago-environment"


class InfraError(RuntimeError):
    """The environment, not the model, failed. The run is retried."""


# Failures of the sandbox or the host rather than the model: a dropped or
# timed-out connection to the sandbox, a full disk. All count as infra.
INFRA_ERRORS = (InfraError, httpx.HTTPError, OSError)


async def run_cmd(
    *args: str, cwd: Path | None = None, env: dict[str, str] | None = None
) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace")


def compose_env(port: int, image: str) -> dict[str, str]:
    env = dict(os.environ)
    # It would override the per-port project name, so every environment would
    # share one project and one run's `down` would stop the others.
    env.pop("COMPOSE_PROJECT_NAME", None)
    env["ENV_PORT"] = str(port)
    env["ENV_IMAGE"] = image
    return env


def ensure_env_file() -> None:
    """Compose reads environment/.env; create it like the examples do."""
    env_file = ENVIRONMENT_DIR / ".env"
    if env_file.exists():
        return
    example = ENVIRONMENT_DIR / ".env.example"
    if example.exists():
        shutil.copy(example, env_file)
    else:
        env_file.touch()


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


# Grid containers carry the owning process's pid in their compose project
# name, so leftovers from a process that died can be told apart from those of
# a grid still running. The trailing dash keeps pid 12 from matching 123.
PROJECT_PREFIX = "archipelago-grid-"


def _owner_prefix(pid: int) -> str:
    return f"{PROJECT_PREFIX}{pid}-"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _remove_containers(name_filter: str) -> list[str]:
    _, out = await run_cmd("docker", "ps", "-aq", "--filter", f"name={name_filter}")
    ids = out.split()
    if ids:
        await run_cmd("docker", "rm", "-f", "-v", *ids)
    return ids


async def reap(own: bool = False) -> int:
    """Remove grid containers whose owning process is gone.

    With `own`, also remove this process's own. Returns how many were removed.
    """
    _, out = await run_cmd(
        "docker",
        "ps",
        "-a",
        "--filter",
        f"name={PROJECT_PREFIX}",
        "--format",
        '{{.Label "com.docker.compose.project"}}',
    )
    owners = set()
    for project in out.split():
        pid, _, _ = project.removeprefix(PROJECT_PREFIX).partition("-")
        if pid.isdigit():
            owners.add(int(pid))
    removed = 0
    for pid in owners:
        if (own and pid == os.getpid()) or not _pid_alive(pid):
            removed += len(await _remove_containers(_owner_prefix(pid)))
    return removed


_guarded = False


async def guard() -> None:
    """Once per process: clear dead grids' containers, and start a watchdog.

    The watchdog is a detached shell that outlives this process. When this
    process exits by any means (a crash, SIGKILL, a closed terminal), it
    removes the containers this process left, so none keep running.
    """
    global _guarded
    if _guarded:
        return
    _guarded = True
    await reap()
    pid = os.getpid()
    script = (
        f"while kill -0 {pid} 2>/dev/null; do sleep 5; done; "
        f"ids=$(docker ps -aq --filter name={_owner_prefix(pid)}); "
        '[ -n "$ids" ] && docker rm -f -v $ids'
    )
    subprocess.Popen(
        ["sh", "-c", script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


class Environment:
    def __init__(self, port: int, image: str):
        self.port = port
        self.image = image
        self.project = f"{_owner_prefix(os.getpid())}{port}"
        self.url = f"http://localhost:{port}"

    async def _compose(self, *args: str) -> tuple[int, str]:
        return await run_cmd(
            "docker",
            "compose",
            "-p",
            self.project,
            *args,
            cwd=ENVIRONMENT_DIR,
            env=compose_env(self.port, self.image),
        )

    async def up(self, health_timeout: float = 180) -> None:
        await guard()
        await self._compose("down", "-v")
        code, out = await self._compose("up", "-d", "--no-build")
        if code != 0:
            raise InfraError(f"compose up failed on port {self.port}: {out[-2000:]}")
        await self._wait_healthy(health_timeout)

    async def _wait_healthy(self, timeout: float) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        async with httpx.AsyncClient(timeout=5) as client:
            while asyncio.get_running_loop().time() < deadline:
                try:
                    if (await client.get(f"{self.url}/health")).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(1)
        raise InfraError(
            f"environment on port {self.port} not healthy after {timeout:.0f}s"
        )

    async def down(self) -> None:
        # Shielded: a run cancelled mid-way (Stop, Ctrl+C) must still remove
        # its container. If compose fails, remove it directly.
        async def _down() -> None:
            code, _ = await self._compose("down", "-v")
            if code != 0:
                await _remove_containers(f"{self.project}-")

        task = asyncio.ensure_future(_down())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def save_logs(self, path: Path) -> None:
        _, out = await self._compose("logs", "--no-color")
        path.write_text(out)

    async def populate(self, subsystem: str, tarball: Path) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=900) as client:
            with tarball.open("rb") as f:
                resp = await client.post(
                    f"{self.url}/data/populate",
                    params={"subsystem": subsystem},
                    files={"archive": (tarball.name, f, "application/gzip")},
                )
        if resp.status_code != 200:
            raise InfraError(
                f"populate {subsystem} failed: {resp.status_code} {resp.text[:500]}"
            )
        return resp.json()

    async def configure_apps(self, mcp_config: dict[str, Any]) -> None:
        async with httpx.AsyncClient(timeout=900) as client:
            resp = await client.post(f"{self.url}/apps", json=mcp_config)
        if resp.status_code != 200:
            # 503: the MCP servers weren't ready in time, often from many
            # environments starting at once.
            raise InfraError(f"/apps returned {resp.status_code}: {resp.text[:500]}")

    async def snapshot(self, dest: Path) -> Path:
        async with httpx.AsyncClient(timeout=900) as client:
            async with client.stream("POST", f"{self.url}/data/snapshot") as resp:
                if resp.status_code != 200:
                    await resp.aread()
                    raise InfraError(
                        f"snapshot failed: {resp.status_code} {resp.text[:500]}"
                    )
                with dest.open("wb") as f:
                    async for chunk in resp.aiter_bytes(1 << 16):
                        f.write(chunk)
        return dest


# What the image is built from (see environment/Dockerfile's COPY lines).
IMAGE_SOURCES = ("environment", "mcp_servers")


async def _image_sources_dirty() -> bool:
    _, out = await run_cmd(
        "git", "status", "--porcelain", "--", *IMAGE_SOURCES, cwd=REPO_DIR
    )
    return bool(out.strip())


async def git_sha() -> str:
    code, out = await run_cmd("git", "rev-parse", "--short=12", "HEAD", cwd=REPO_DIR)
    if code != 0:
        raise RuntimeError(f"git rev-parse failed: {out}")
    return out.strip()


async def image_tag() -> str:
    """A tag named after the git trees the image is built from.

    Commits that don't touch them reuse the image. Uncommitted changes there
    make the tag `-dirty`, which is rebuilt every time.
    """
    code, out = await run_cmd(
        "git", "rev-parse", *(f"HEAD:{s}" for s in IMAGE_SOURCES), cwd=REPO_DIR
    )
    if code != 0:
        raise RuntimeError(f"git rev-parse failed: {out}")
    digest = hashlib.sha256(out.encode()).hexdigest()[:12]
    dirty = "-dirty" if await _image_sources_dirty() else ""
    return f"{IMAGE_REPO}:tree-{digest}{dirty}"


async def image_exists(image: str) -> bool:
    code, _ = await run_cmd("docker", "image", "inspect", image)
    return code == 0


async def ensure_image(image: str | None, rebuild: bool = False) -> str:
    """Return the image every worker runs, building it once if needed.

    Without a configured image, the tag names the source trees (image_tag),
    so a grid's runs all use one build and the manifest says which.
    """
    tag = image or await image_tag()
    if image and not rebuild:
        if not await image_exists(tag):
            raise InfraError(f"Configured env_image {tag} does not exist")
        return tag
    if rebuild or tag.endswith("-dirty") or not await image_exists(tag):
        ensure_env_file()
        _, previous = await run_cmd("docker", "image", "inspect", "-f", "{{.Id}}", tag)
        code, out = await run_cmd(
            "docker",
            "compose",
            "-p",
            "archipelago-build",
            "build",
            cwd=ENVIRONMENT_DIR,
            env=compose_env(8080, tag),
        )
        if code != 0:
            raise InfraError(f"image build failed: {out[-3000:]}")
        # A rebuild moves the tag and leaves the old image untagged. The image
        # is large, so repeated rebuilds would fill the disk. It's still kept
        # if a container uses it.
        _, current = await run_cmd("docker", "image", "inspect", "-f", "{{.Id}}", tag)
        old = previous.strip()
        if old.startswith("sha256:") and old != current.strip():
            await run_cmd("docker", "image", "rm", old)
    return tag
