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
@pytest.mark.parametrize(
    ("names", "tool"), [(["a"], "ping"), (["a", "b"], "a_ping")], ids=["one", "two"]
)
async def test_concurrent_calls_after_crash_restart_one_backend(
    tmp_path: Path, names: list[str], tool: str
):
    script = tmp_path / "pid_server.py"
    script.write_text(SERVER)
    server = {"transport": "stdio", "command": sys.executable, "args": [str(script)]}
    proxy = _build_proxy({"mcpServers": {n: server for n in names}})

    async def ping() -> int:
        async with FastMCPClient(proxy) as client:
            result = await client.call_tool(tool, {})
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


SLOW_SERVER = """
import time
time.sleep(1)
from fastmcp import FastMCP
m = FastMCP("slow")
@m.tool
def echo(x: str) -> str:
    return x
if __name__ == "__main__":
    m.run()
"""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("names", "tool"), [(["a"], "echo"), (["a", "b"], "a_echo")], ids=["one", "two"]
)
async def test_call_cancelled_mid_connect_leaves_backend_usable(
    tmp_path: Path, names: list[str], tool: str
):
    script = tmp_path / "slow_server.py"
    script.write_text(SLOW_SERVER)
    server = {"transport": "stdio", "command": sys.executable, "args": [str(script)]}
    proxy = _build_proxy({"mcpServers": {n: server for n in names}})

    async def echo() -> str:
        async with FastMCPClient(proxy) as client:
            return (await client.call_tool(tool, {"x": "hi"})).content[0].text

    async with proxy._lifespan_manager():
        first = asyncio.create_task(echo())
        await asyncio.sleep(0.3)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert await asyncio.wait_for(echo(), 30) == "hi"
