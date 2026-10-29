"""Turn the agent's log file and grading's output into dashboard events.

The agent runs with FILE_LOGGING, which writes one JSON line per log record:
``{"timestamp", "level", "message", "extra": {"message_type", "name", "ref",
"payload", ...}}``. Grading prints loguru lines with ``[JUDGE][GRADER][...]``
tags. Both are tailed while the process runs and normalized into one event
shape, stored in grid.db.
"""

import asyncio
import json
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PREVIEW_CHARS = 300
TEXT_CHARS = 2000

# The harness's own tools; MCP tools carry a `{server}_` prefix instead.
TOOLBELT = "toolbelt"
META_TOOLS = {
    "todo_write",
    "toolbelt_list_tools",
    "toolbelt_inspect_tool",
    "toolbelt_add_tool",
    "toolbelt_remove_tool",
    "final_answer",
}
PATH_KEYS = ("path", "file_path", "filepath", "file", "directory", "dir", "filename")
# The simulated human: its question and answer are kept whole (up to
# TEXT_CHARS) so Run Detail can show them, shared-file lines included.
ASK_HUMAN = ("human_server", "ask_human")

# Every event the API returns has these keys.
EVENT_FIELDS = (
    "model_id",
    "task_id",
    "server",
    "tool",
    "ref",
    "args_preview",
    "ok",
    "duration_ms",
    "tokens",
    "text",
    "state",
    "path",
)

_IMAGE_PAYLOAD = re.compile(r"""['"]type['"]:\s*['"]image['"]""")
_GRADER_TAG = re.compile(r"\[JUDGE\]\[GRADER\]\[(START|RESULT)\]")
_GRADER_RESULT = re.compile(r"\bresult=(PASS|FAIL)\b")


def _clip(value: Any, limit: int = PREVIEW_CHARS) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def split_tool(name: str, servers: Iterable[str]) -> tuple[str | None, str]:
    """(server, tool) for a tool name as the agent sees it."""
    if name in META_TOOLS or name.startswith("toolbelt_"):
        return TOOLBELT, name
    for server in sorted(servers, key=len, reverse=True):
        if name.startswith(f"{server}_"):
            return server, name[len(server) + 1 :]
    return None, name


def _args(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _content_blocks(payload: Any) -> list[dict[str, Any]] | None:
    """A tool result's MCP content blocks, or None if it isn't in that shape.

    The shape comes from the MCP protocol, not the model: every model's tool
    results arrive as ``[{"type": "text", "text": ...}, ...]`` (or a
    CallToolResult with them under ``content``), sometimes serialized.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if isinstance(payload, dict) and isinstance(payload.get("content"), list):
        payload = payload["content"]
    if (
        isinstance(payload, list)
        and payload
        and all(isinstance(b, dict) and isinstance(b.get("type"), str) for b in payload)
    ):
        return payload
    return None


def _block_text(block: dict[str, Any]) -> str:
    kind = block["type"]
    if kind == "text":
        return str(block.get("text") or "")
    if kind == "resource":
        resource = block.get("resource") or {}
        if isinstance(resource.get("text"), str):
            return resource["text"]
        return f"[resource {resource.get('uri') or ''}]".replace(" ]", "]")
    if kind == "resource_link":
        return f"[{block.get('name') or block.get('uri') or 'resource'}]"
    # image, audio, and any block type added later.
    return f"[{kind}]"


def _result_text(payload: Any) -> str | None:
    """A tool result as readable text: content blocks unwrapped, else as is."""
    if payload is None:
        return None
    blocks = _content_blocks(payload)
    if blocks is not None:
        return "\n".join(_block_text(b) for b in blocks)
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    return "[image]" if _IMAGE_PAYLOAD.search(text[:2000]) else text


def _result_preview(payload: Any, limit: int = PREVIEW_CHARS) -> str | None:
    return _clip(_result_text(payload), limit)


def _args_preview(server: str | None, tool: str, payload: Any) -> str | None:
    if (server, tool) == ASK_HUMAN:
        args = _args(payload)
        if args is not None and isinstance(args.get("question"), str):
            # Always valid JSON, so the dashboard can read the question.
            return json.dumps({"question": _clip(args["question"], TEXT_CHARS)})
    return _clip(payload)


def iso(epoch: float | None) -> str | None:
    """Epoch seconds as an ISO-8601 UTC string, the API's time format."""
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")


def _epoch(timestamp: str | None) -> float | None:
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp).timestamp()
    except ValueError:
        return None


class AgentEventNormalizer:
    """Stateful: pairs each tool result with its call to time it."""

    def __init__(self, servers: Iterable[str]):
        self.servers = list(servers)
        self._calls: dict[str, tuple[float | None, str | None, str]] = {}

    def normalize(self, record: dict[str, Any]) -> dict[str, Any] | None:
        extra = record.get("extra") or {}
        kind = extra.get("message_type")
        level = record.get("level")
        message = record.get("message") or ""
        ts = _epoch(record.get("timestamp"))

        if kind == "tool_call":
            name = extra.get("name") or ""
            server, tool = split_tool(name, self.servers)
            args = _args(extra.get("payload"))
            ref = extra.get("ref")
            if ref:
                self._calls[ref] = (ts, server, tool)
            path = next(
                (args[k] for k in PATH_KEYS if args and isinstance(args.get(k), str)),
                None,
            )
            return {
                "kind": "tool_call",
                "server": server,
                "tool": tool,
                "ref": ref,
                "args_preview": _args_preview(server, tool, extra.get("payload")),
                "path": path,
                "ts": ts,
            }
        if kind == "tool_result":
            if extra.get("mcp_tool_retry"):
                return {
                    "kind": "infra",
                    "ok": None,
                    "text": _clip(message, TEXT_CHARS),
                    "ts": ts,
                }
            ref = extra.get("ref")
            started, server, tool = (
                self._calls.pop(ref, (None, None, None)) if ref else (None, None, None)
            )
            if tool is None:
                server, tool = split_tool(extra.get("name") or "", self.servers)
            return {
                "kind": "tool_result",
                "server": server,
                "tool": tool,
                "ref": ref,
                "ok": level not in ("ERROR", "WARNING", "CRITICAL"),
                "duration_ms": round((ts - started) * 1000) if ts and started else None,
                "text": _result_preview(
                    extra.get("payload"),
                    TEXT_CHARS if (server, tool) == ASK_HUMAN else PREVIEW_CHARS,
                )
                or _clip(message),
                "ts": ts,
            }
        if kind == "llm_usage":
            # One model call: the start of a step in the run's timeline.
            prompt = extra.get("prompt_tokens") or 0
            completion = extra.get("completion_tokens") or 0
            return {
                "kind": "step",
                "tokens": prompt + completion,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "duration_ms": extra.get("latency_ms"),
                "text": f"Model call: {prompt:,} in, {completion:,} out",
                "ts": ts,
            }
        if kind == "step":
            # The harness's own step lines ("Starting step 3") repeat what the
            # model calls and tool calls already show; keep only its warnings
            # (output cut off, empty reply).
            if level in ("WARNING", "ERROR", "CRITICAL"):
                return {
                    "kind": "infra",
                    "ok": False,
                    "text": _clip(message, TEXT_CHARS),
                    "ts": ts,
                }
            return None
        simple = {
            "response": "response",
            "reasoning": "reasoning",
            "thinking": "reasoning",
            "tool": "toolbelt",
            "resum": "compaction",
            "final_answer": "final_answer",
        }
        if kind in simple:
            return {"kind": simple[kind], "text": _clip(message, TEXT_CHARS), "ts": ts}
        if level in ("ERROR", "CRITICAL"):
            return {
                "kind": "infra",
                "ok": False,
                "text": _clip(message, TEXT_CHARS),
                "ts": ts,
            }
        return None


def grading_progress(line: str) -> dict[str, Any] | None:
    """A grading_progress event for a judge start/result line, else None."""
    match = _GRADER_TAG.search(line)
    if not match:
        return None
    detail = line[match.end() :].strip(" -|")
    result = _GRADER_RESULT.search(detail)
    return {
        "kind": "grading_progress",
        "state": match.group(1).lower(),
        "ok": None if result is None else result.group(1) == "PASS",
        "text": _clip(detail, TEXT_CHARS),
    }


async def tail_lines(
    path: Path,
    on_line: Callable[[str], None],
    stop: asyncio.Event,
    poll_sec: float = 0.25,
) -> None:
    """Call on_line for each complete line appended to path until stop is set.

    After stop it reads whatever is left, so nothing written before the
    process exited is missed.
    """
    offset = 0
    pending = b""
    while True:
        stopping = stop.is_set()
        try:
            with path.open("rb") as f:
                f.seek(offset)
                chunk = f.read()
        except FileNotFoundError:
            chunk = b""
        if chunk:
            offset += len(chunk)
            pending += chunk
            *lines, pending = pending.split(b"\n")
            for raw in lines:
                if raw.strip():
                    on_line(raw.decode(errors="replace"))
        if stopping:
            if pending.strip():
                on_line(pending.decode(errors="replace"))
            return
        try:
            await asyncio.wait_for(stop.wait(), poll_sec)
        except TimeoutError:
            pass


def flatten(row: dict[str, Any]) -> dict[str, Any]:
    """A stored event as the API returns it: every field present."""
    data = row.get("data") or {}
    event = {
        "seq": row["seq"],
        "ts": iso(data.get("ts") or row["ts"]),
        "run_id": row["run_id"],
        "kind": row["kind"],
    }
    for key in EVENT_FIELDS:
        event[key] = data.get(key)
    for key, value in data.items():
        event.setdefault(key, value)
    return event
