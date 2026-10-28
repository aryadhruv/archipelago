"""Human MCP Server.

One tool, ask_human: the agent asks the person who assigned the task a
question, and a simulated human (see sim.py) answers. Configured by the HUMAN_*
environment variables in utils/config.py, which the grid fills in per run.
"""

import os
import sys

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

if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "http").lower()
    if transport == "http":
        port = int(os.getenv("MCP_PORT", "5000"))
        print(f"[human] Starting HTTP transport on port {port}", file=sys.stderr)
        mcp.run(transport="http", host="0.0.0.0", port=port)
    else:
        print("[human] Starting stdio transport", file=sys.stderr)
        mcp.run(transport="stdio")
