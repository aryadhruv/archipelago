"""The simulated human (``ask_human``), run on the host for one agent attempt.

It runs outside the sandbox, like the agent runner, so its provider key and the
human's own files never enter the container: model code there can't read them,
however it gets past the code sandbox. The sandbox's MCP gateway reaches it over
HTTP at host.docker.internal, under a per-run path token, and it uploads shared
files back through the environment's /data/populate.
"""

import asyncio
import contextlib
import functools
import json
import os
import secrets
import socket
from pathlib import Path

from docker.errors import DockerException

from grid.envctl import InfraError, client, run_cmd
from grid.inputs import REPO_DIR, Grid, Task

HUMAN_PROJECT = REPO_DIR / "mcp_servers" / "human"
SERVER_DIR = HUMAN_PROJECT / "mcp_servers" / "human_server"
PYTHON = HUMAN_PROJECT / ".venv" / "bin" / "python"
# The name containers use for the host; envctl maps it to host-gateway.
HOST_ALIAS = "host.docker.internal"
AGENTS_ENV_FILE = REPO_DIR / "agents" / ".env"


async def ensure_venv() -> None:
    """Install the human server's locked dependencies, as uv run does for the runners."""
    code, out = await run_cmd(
        "uv", "sync", "--locked", "--no-dev", "--project", str(HUMAN_PROJECT)
    )
    if code != 0:
        raise RuntimeError(f"could not install the simulated human: {out[-1000:]}")


@functools.cache
def bind_address() -> str:
    """The host address the human server listens on, reachable from containers.

    On native Linux that's the default bridge's gateway, a host interface the
    containers route to; listening there, not on 0.0.0.0, keeps the server off
    the network. Docker Desktop runs containers in a VM whose host-gateway
    leads to the host's loopback, and the bridge's address isn't local, so
    127.0.0.1.
    """
    with contextlib.suppress(DockerException, LookupError, TypeError, OSError):
        gateway = client().networks.get("bridge").attrs["IPAM"]["Config"][0]["Gateway"]
        with socket.socket() as s:
            s.bind((gateway, 0))
        return gateway
    return "127.0.0.1"


def _free_port(host: str) -> int:
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


class HumanServer:
    def __init__(self, grid: Grid, task: Task, folder: Path, env_url: str, log: Path):
        assert grid.human is not None
        self.grid = grid
        self.task = task
        self.folder = folder
        self.env_url = env_url
        self.log = log
        self.proc: asyncio.subprocess.Process | None = None

    def _env(self, host: str, port: int, path: str) -> dict[str, str]:
        human = self.grid.human
        assert human is not None
        return {
            **os.environ,
            # Keys come from agents/.env, read with dotenv as the agent runner
            # reads it, so the human uses the tested models' keys.
            "HUMAN_ENV_FILE": str(AGENTS_ENV_FILE),
            "HUMAN_PARENT_PID": str(os.getpid()),
            "MCP_TRANSPORT": "http",
            "MCP_HOST": host,
            "MCP_PORT": str(port),
            "MCP_PATH": path,
            "HUMAN_MODEL": human.model,
            "HUMAN_EXTRA_ARGS": json.dumps(human.extra_args or {}),
            "HUMAN_PERSONA": self.grid.personas.get(self.task.world_id, ""),
            "HUMAN_TASK_PROMPT": self.task.prompt,
            "HUMAN_MAX_QUESTIONS": ""
            if human.max_questions is None
            else str(human.max_questions),
            "HUMAN_DATA_ROOT": str(self.folder / "human"),
            "HUMAN_AGENT_FILES": str(self.folder / "agent_files.txt"),
            "HUMAN_ENV_URL": self.env_url,
        }

    async def start(self, timeout: float = 60) -> str:
        """Start the server; returns the URL the sandbox's gateway uses."""
        host = bind_address()
        port = _free_port(host)
        path = f"/{secrets.token_urlsafe(16)}/mcp"
        with self.log.open("ab") as log:
            self.proc = await asyncio.create_subprocess_exec(
                str(PYTHON),
                "main.py",
                cwd=SERVER_DIR,
                env=self._env(host, port, path),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=log,
                stderr=asyncio.subprocess.STDOUT,
            )
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if self.proc.returncode is not None:
                raise InfraError(
                    f"the simulated human exited with {self.proc.returncode} "
                    f"(see {self.log.name})"
                )
            with contextlib.suppress(OSError):
                _, writer = await asyncio.open_connection(host, port)
                writer.close()
                return f"http://{HOST_ALIAS}:{port}{path}"
            await asyncio.sleep(0.2)
        raise InfraError(f"the simulated human didn't start in {timeout:.0f}s")

    async def stop(self) -> None:
        if self.proc is None or self.proc.returncode is not None:
            return
        self.proc.terminate()
        try:
            await asyncio.wait_for(self.proc.wait(), 5)
        except TimeoutError:
            self.proc.kill()
            await self.proc.wait()
