"""Drive one environment container (Docker Engine API) and its sandbox HTTP API."""

import asyncio
import contextlib
import hashlib
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import docker
import httpx
from docker.errors import DockerException, ImageNotFound, NotFound
from docker.models.containers import Container

from grid.inputs import REPO_DIR

ENVIRONMENT_DIR = REPO_DIR / "environment"
IMAGE_REPO = "archipelago-environment"


class InfraError(RuntimeError):
    """The environment, not the model, failed. The run is retried."""


# Failures of the sandbox or the host rather than the model: a dropped or
# timed-out connection to the sandbox or to Docker, a full disk. All count as
# infra.
INFRA_ERRORS = (InfraError, httpx.HTTPError, DockerException, OSError)


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
    """The environment for `docker compose build` (the only compose call)."""
    env = dict(os.environ)
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


# Every grid container is labelled with the pid of the process that started
# it, so leftovers from a process that died can be told apart from those of a
# grid still running.
OWNER_LABEL = "archipelago.grid.owner"
CONTAINER_PORT = "8080/tcp"

_client: docker.DockerClient | None = None


def client() -> docker.DockerClient:
    """The Docker Engine API client (DOCKER_HOST, or the default socket)."""
    global _client
    if _client is None:
        try:
            _client = docker.from_env()
        except DockerException as e:
            raise InfraError(f"Can't reach Docker: {e}") from e
    return _client


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _remove(container: Container) -> None:
    with contextlib.suppress(NotFound):
        container.remove(force=True, v=True)


def _remove_owned(pid: int) -> int:
    containers = client().containers.list(
        all=True, filters={"label": f"{OWNER_LABEL}={pid}"}
    )
    for container in containers:
        _remove(container)
    return len(containers)


def _reap(own: bool) -> int:
    owners = {
        int(pid)
        for c in client().containers.list(all=True, filters={"label": OWNER_LABEL})
        if (pid := c.labels.get(OWNER_LABEL, "")).isdigit()
    }
    return sum(
        _remove_owned(pid)
        for pid in owners
        if (own and pid == os.getpid()) or not _pid_alive(pid)
    )


async def reap(own: bool = False) -> int:
    """Remove grid containers whose owning process is gone.

    With `own`, also remove this process's own. Returns how many were removed.
    """
    return await asyncio.to_thread(_reap, own)


# Run by the watchdog, a separate Python process: wait for the owner to exit,
# then remove its containers.
_WATCHDOG = """
import os, sys, time
import docker
pid, label = int(sys.argv[1]), sys.argv[2]
while True:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        break
    except PermissionError:
        pass
    time.sleep(5)
client = docker.from_env()
for c in client.containers.list(all=True, filters={"label": f"{label}={pid}"}):
    try:
        c.remove(force=True, v=True)
    except Exception:
        pass
"""

_guarded = False


async def guard() -> None:
    """Once per process: clear dead grids' containers, and start a watchdog.

    The watchdog is a detached process that outlives this one. When this
    process exits by any means (a crash, SIGKILL, a closed terminal), it
    removes the containers this process left, so none keep running.
    """
    global _guarded
    if _guarded:
        return
    _guarded = True
    await reap()
    subprocess.Popen(
        [sys.executable, "-c", _WATCHDOG, str(os.getpid()), OWNER_LABEL],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def env_file_values() -> dict[str, str]:
    """environment/.env as KEY=VALUE pairs, the way compose's env_file reads it."""
    values: dict[str, str] = {}
    env_file = ENVIRONMENT_DIR / ".env"
    if not env_file.exists():
        return values
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


class Environment:
    def __init__(self, port: int, image: str):
        self.port = port
        self.image = image
        self.name = f"archipelago-grid-{os.getpid()}-{port}"
        self.url = f"http://localhost:{port}"
        self.container: Container | None = None

    def _start(self) -> Container:
        containers = client().containers
        # A container of this name is a leftover of this process's last run
        # on the port.
        with contextlib.suppress(NotFound):
            _remove(containers.get(self.name))
        return containers.run(
            self.image,
            name=self.name,
            detach=True,
            environment=env_file_values(),
            # Loopback only: the sandbox API has no auth.
            ports={CONTAINER_PORT: ("127.0.0.1", self.port)},
            # The default bridge, not a network per container: Docker runs
            # out of address pools after a few dozen networks.
            network_mode="bridge",
            labels={OWNER_LABEL: str(os.getpid())},
        )

    async def up(self, health_timeout: float = 180) -> None:
        await guard()
        try:
            self.container = await asyncio.to_thread(self._start)
        except DockerException as e:
            raise InfraError(f"container start failed on port {self.port}: {e}") from e
        await self._wait_healthy(health_timeout)

    async def _wait_healthy(self, timeout: float) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        async with httpx.AsyncClient(timeout=5) as http:
            while asyncio.get_running_loop().time() < deadline:
                try:
                    if (await http.get(f"{self.url}/health")).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(1)
        raise InfraError(
            f"environment on port {self.port} not healthy after {timeout:.0f}s"
        )

    def _remove_self(self) -> None:
        if self.container is not None:
            _remove(self.container)
        else:
            with contextlib.suppress(NotFound):
                _remove(client().containers.get(self.name))

    async def down(self) -> None:
        # The removal runs in a thread that a cancelled run (Stop, Ctrl+C)
        # can't interrupt; wait for it before passing the cancel on.
        task = asyncio.ensure_future(asyncio.to_thread(self._remove_self))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def save_logs(self, path: Path) -> None:
        if self.container is None:
            return
        try:
            out = await asyncio.to_thread(self.container.logs)
        except DockerException as e:
            out = f"could not read logs: {e}".encode()
        path.write_bytes(out)

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


def _image_id(image: str) -> str | None:
    try:
        return client().images.get(image).id
    except ImageNotFound:
        return None


async def image_exists(image: str) -> bool:
    return await asyncio.to_thread(_image_id, image) is not None


def _remove_image(image_id: str) -> None:
    # Kept if a container still uses it.
    with contextlib.suppress(DockerException):
        client().images.remove(image_id)


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
        previous = await asyncio.to_thread(_image_id, tag)
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
        # is large, so repeated rebuilds would fill the disk.
        if previous and previous != await asyncio.to_thread(_image_id, tag):
            await asyncio.to_thread(_remove_image, previous)
    return tag
