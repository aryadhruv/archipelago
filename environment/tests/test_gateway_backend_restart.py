import asyncio
import os
import signal
import sys
import time
from pathlib import Path

import pytest
from fastmcp import Client as FastMCPClient

from runner.gateway.gateway import _build_proxy

SERVER = """
import os
from fastmcp import FastMCP
m = FastMCP("pid")
@m.tool
def ping() -> str:
    return str(os.getpid())
if __name__ == "__main__":
    m.run()
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        # A reaped child is gone; an unreaped zombie still answers kill(0).
        return os.waitpid(pid, os.WNOHANG) == (0, 0)
    except ChildProcessError:
        return True


@pytest.mark.asyncio
async def test_concurrent_calls_after_crash_restart_one_backend(tmp_path: Path):
    script = tmp_path / "pid_server.py"
    script.write_text(SERVER)
    server = {"transport": "stdio", "command": sys.executable, "args": [str(script)]}
    proxy = _build_proxy({"mcpServers": {"a": server, "b": server}})

    async def ping() -> int:
        async with FastMCPClient(proxy) as client:
            result = await client.call_tool("a_ping", {})
            return int(result.content[0].text)

    async with proxy._lifespan_manager():
        first = {await ping() for _ in range(3)}
        assert len(first) == 1
        os.kill(first.pop(), signal.SIGKILL)
        await asyncio.sleep(0.5)

        after_crash = set(await asyncio.gather(*(ping() for _ in range(6))))
        assert len(after_crash) == 1, after_crash
        restarted = after_crash.pop()

    deadline = time.monotonic() + 10
    while _alive(restarted) and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
    assert not _alive(restarted)
