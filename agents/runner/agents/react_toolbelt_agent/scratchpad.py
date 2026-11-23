"""Append-only scratchpad for the ReAct agent.

The agent writes notes with ``scratchpad_write`` and reads them back with
``scratchpad_read``. There is no tool to edit or delete a note, and the notes
live in the agent process, not in the sandbox, so filesystem or code tools
can't change them either. Each entry carries the SHA-256 of the entry before
it, so an edited log fails ``verify_chain``.

Notes survive ReSum context compaction: after a summary, the agent can call
``scratchpad_read`` to get its earlier notes back word for word.
"""

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from typing import Any

from loguru import logger
from openai.types.chat.chat_completion_tool_param import ChatCompletionToolParam

MAX_NOTE_CHARS = 8000
GENESIS_HASH = "0" * 64

SCRATCHPAD_WRITE = "scratchpad_write"
SCRATCHPAD_READ = "scratchpad_read"

SCRATCHPAD_TOOLS: list[ChatCompletionToolParam] = [
    {
        "type": "function",
        "function": {
            "name": SCRATCHPAD_WRITE,
            "description": (
                "Append a note to your scratchpad: observations, reasoning, "
                "intermediate results, open questions. The scratchpad is "
                "append-only: notes can't be edited or deleted, so add a new "
                "note to correct an earlier one. Notes are saved with the run "
                "and visible to the people reviewing it; they are not part of "
                f"your answer. At most {MAX_NOTE_CHARS} characters per note."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {
                        "type": "string",
                        "description": "The text to append.",
                    },
                },
                "required": ["note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": SCRATCHPAD_READ,
            "description": (
                "Read your scratchpad notes, oldest first. Useful after your "
                "context has been summarized."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "last_n": {
                        "type": "integer",
                        "description": "Only return the most recent N notes. Omit for all.",
                    },
                },
                "required": [],
            },
        },
    },
]

SCRATCHPAD_TOOL_NAMES = {tool["function"]["name"] for tool in SCRATCHPAD_TOOLS}


@dataclass(frozen=True)
class ScratchpadEntry:
    index: int
    step: int
    elapsed_sec: float
    note: str
    prev_hash: str
    hash: str


def _entry_hash(index: int, step: int, note: str, prev_hash: str) -> str:
    body = json.dumps(
        {"index": index, "step": step, "note": note, "prev_hash": prev_hash},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def verify_chain(entries: list[dict[str, Any]]) -> bool:
    """True when every entry's hash matches its content and the entry before it."""
    prev = GENESIS_HASH
    for i, entry in enumerate(entries):
        if entry.get("index") != i or entry.get("prev_hash") != prev:
            return False
        expected = _entry_hash(i, entry["step"], entry["note"], prev)
        if entry.get("hash") != expected:
            return False
        prev = expected
    return True


class Scratchpad:
    """An append-only list of notes; there is deliberately no remove or edit."""

    def __init__(self, start_time: float | None = None):
        self._entries: list[ScratchpadEntry] = []
        self._start_time = start_time if start_time is not None else time.time()

    def set_start_time(self, start_time: float) -> None:
        self._start_time = start_time

    @property
    def entries(self) -> tuple[ScratchpadEntry, ...]:
        return tuple(self._entries)

    def append(self, note: str, step: int) -> ScratchpadEntry:
        prev = self._entries[-1].hash if self._entries else GENESIS_HASH
        index = len(self._entries)
        entry = ScratchpadEntry(
            index=index,
            step=step,
            elapsed_sec=round(time.time() - self._start_time, 3),
            note=note,
            prev_hash=prev,
            hash=_entry_hash(index, step, note, prev),
        )
        self._entries.append(entry)
        return entry

    def handle(self, tool_name: str, arguments: str, step: int) -> str:
        try:
            args: Any = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            return json.dumps({"error": "Invalid JSON in arguments"})
        if not isinstance(args, dict):
            return json.dumps({"error": "Arguments must be a JSON object"})

        if tool_name == SCRATCHPAD_WRITE:
            note = args.get("note")
            if not isinstance(note, str) or not note.strip():
                return json.dumps({"error": "note must be a non-empty string"})
            if len(note) > MAX_NOTE_CHARS:
                return json.dumps(
                    {
                        "error": f"note is {len(note)} characters; the limit is "
                        f"{MAX_NOTE_CHARS}. Split it into several notes."
                    }
                )
            entry = self.append(note, step)
            logger.bind(message_type="tool").info(
                f"Scratchpad note {entry.index} ({len(note)} chars)"
            )
            return json.dumps({"success": True, "index": entry.index})

        if tool_name == SCRATCHPAD_READ:
            last_n = args.get("last_n")
            entries = self._entries
            if last_n is not None:
                if (
                    isinstance(last_n, bool)
                    or not isinstance(last_n, int)
                    or last_n < 1
                ):
                    return json.dumps({"error": "last_n must be a positive integer"})
                entries = entries[-last_n:]
            return json.dumps(
                {
                    "total": len(self._entries),
                    "notes": [
                        {"index": e.index, "step": e.step, "note": e.note}
                        for e in entries
                    ],
                },
                ensure_ascii=False,
            )

        return json.dumps({"error": f"Unknown scratchpad tool: {tool_name}"})

    def to_output(self) -> list[dict[str, Any]]:
        return [asdict(e) for e in self._entries]
