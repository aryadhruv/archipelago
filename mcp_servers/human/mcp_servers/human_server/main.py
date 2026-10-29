"""Human MCP Server.

One tool, ask_human: the agent asks the person who assigned the task a
question, and a simulated human (see sim.py) answers. Configured by the HUMAN_*
environment variables in utils/config.py, which the grid fills in per run.

The grid runs it on the host, outside the agent's sandbox, and the sandbox's
MCP gateway reaches it over HTTP.
"""

import os
import sys
import threading
import time

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from tools.ask_human import ask_human

mcp = FastMCP(
    "human-server",
    instructions=(
        "Ask the person who assigned your task a question when you're missing "
        "information only they have. They answer briefly, and may point you to "
        "a file or share one, which lands under /from_user/."
    ),
)
mcp.add_middleware(ErrorHandlingMiddleware(include_traceback=True))
mcp.tool(ask_human)


def _exit_with_parent(pid: int) -> None:
    """Stop when the grid that started this server is gone, even if it was killed."""

    def watch() -> None:
        while os.getppid() == pid:
            time.sleep(2)
        os._exit(0)

    threading.Thread(target=watch, daemon=True).start()


if __name__ == "__main__":
    # Provider keys, read the way the agent runner reads its own .env:
    # variables already set win.
    if env_file := os.getenv("HUMAN_ENV_FILE"):
        load_dotenv(env_file, override=False)
    if parent := os.getenv("HUMAN_PARENT_PID"):
        _exit_with_parent(int(parent))
    transport = os.getenv("MCP_TRANSPORT", "http").lower()
    if transport == "http":
        host = os.getenv("MCP_HOST", "127.0.0.1")
        port = int(os.getenv("MCP_PORT", "5000"))
        path = os.getenv("MCP_PATH", "/mcp")
        print(f"[human] Starting HTTP transport on {host}:{port}", file=sys.stderr)
        mcp.run(transport="http", host=host, port=port, path=path)
    else:
        print("[human] Starting stdio transport", file=sys.stderr)
        mcp.run(transport="stdio")
