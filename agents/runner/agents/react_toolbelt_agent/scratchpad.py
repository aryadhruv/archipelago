"""Append-only scratchpad for the ReAct agent.

The agent writes notes with ``scratchpad_write`` and reads them back with
``scratchpad_read``. There is no tool to edit or delete a note, and the notes
live in the agent process, not in the sandbox, so filesystem or code tools
can't change them either. That separation is what keeps the agent from
rewriting its log.

Each entry also carries a SHA-256 over its content and the hash of the entry
before it, and every entry is written to the agent's event log as it is
made. The chain on its own is a checksum: anyone who can edit the trajectory
can recompute it. Compare it with the logged hashes to catch edits, dropped
entries or truncation after the run.

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
# Upper bound on one scratchpad_read result, so a long log can't flood the context.
MAX_READ_CHARS = 40_000
GENESIS_HASH = "0" * 64

SCRATCHPAD_WRITE = "scratchpad_write"
SCRATCHPAD_READ = "scratchpad_read"
# Log record type for each new entry: the copy kept outside the trajectory.
SCRATCHPAD_LOG_TYPE = "scratchpad_entry"

SCRATCHPAD_TOOLS: list[ChatCompletionToolParam] = [
    {
        "type": "function",
        "function": {
            "name": SCRATCHPAD_WRITE,
            "description": (
                "Append a note to your scratchpad, a running log of your work "
                "on this task. Use it throughout the task: write your plan "
                "before you start, then a short note after each step saying "
                "what you did, what you found and what you'll do next, and "
                "note any change of plan and why. Log steps as you go rather "
                "than all at the end. The scratchpad is append-only: notes "
                "can't be edited or deleted, so add a new note to correct an "
                "earlier one. The scratchpad is private to you and is not part "
                "of your answer. "
                f"At most {MAX_NOTE_CHARS} characters per note."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {
                        "type": "string",
                        "description": "The text to append, e.g. your plan, or the step you just took and its result.",
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
                "Read your scratchpad notes, oldest first, to review your plan "
                "and the steps so far, for example after your context has "
                "been summarized or before submitting your answer."
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


def _entry_hash(
    index: int, step: int, elapsed_sec: float, note: str, prev_hash: str
) -> str:
    # ensure_ascii keeps lone surrogates (valid JSON, invalid UTF-8) hashable.
    body = json.dumps(
        {
            "index": index,
            "step": step,
            "elapsed_sec": elapsed_sec,
            "note": note,
            "prev_hash": prev_hash,
        },
        sort_keys=True,
        ensure_ascii=True,
    )
    return hashlib.sha256(body.encode("ascii")).hexdigest()


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def verify_chain(entries: Any) -> bool:
    """True when every entry's hash matches its content and the entry before it.

    A checksum, not a seal: it catches corruption and careless edits, but
    anyone who can edit the list can recompute it. Never raises."""
    if not isinstance(entries, list):
        return False
    prev = GENESIS_HASH
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            return False
        step, elapsed, note = (
            entry.get("step"),
            entry.get("elapsed_sec"),
            entry.get("note"),
        )
        if (
            not _is_int(entry.get("index"))
            or entry["index"] != i
            or entry.get("prev_hash") != prev
            or not _is_int(step)
            or not isinstance(elapsed, (int, float))
            or isinstance(elapsed, bool)
            or not isinstance(note, str)
        ):
            return False
        expected = _entry_hash(i, step, elapsed, note, prev)
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
        elapsed = round(time.time() - self._start_time, 3)
        entry = ScratchpadEntry(
            index=index,
            step=step,
            elapsed_sec=elapsed,
            note=note,
            prev_hash=prev,
            hash=_entry_hash(index, step, elapsed, note, prev),
        )
        self._entries.append(entry)
        # The event log is written as the run goes, so it keeps every entry
        # even if the trajectory is later lost, cut short or edited.
        logger.bind(message_type=SCRATCHPAD_LOG_TYPE, payload=asdict(entry)).info(
            f"Scratchpad note {index} ({len(note)} chars)"
        )
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
            return json.dumps({"success": True, "index": entry.index})

        if tool_name == SCRATCHPAD_READ:
            last_n = args.get("last_n")
            entries = self._entries
            if last_n is not None:
                # Some providers send whole numbers as floats (3.0).
                if isinstance(last_n, float) and last_n.is_integer():
                    last_n = int(last_n)
                if not _is_int(last_n) or last_n < 1:
                    return json.dumps({"error": "last_n must be a positive integer"})
                entries = entries[-last_n:]
            return self._read_result(entries)

        return json.dumps({"error": f"Unknown scratchpad tool: {tool_name}"})

    def _read_result(self, entries: list[ScratchpadEntry]) -> str:
        """Newest notes that fit in MAX_READ_CHARS, returned oldest first."""
        kept: list[dict[str, Any]] = []
        size = 0
        for e in reversed(entries):
            item = {"index": e.index, "step": e.step, "note": e.note}
            item_size = len(json.dumps(item))
            if kept and size + item_size > MAX_READ_CHARS:
                break
            kept.append(item)
            size += item_size
        kept.reverse()
        result: dict[str, Any] = {"total": len(self._entries), "notes": kept}
        omitted = len(entries) - len(kept)
        if omitted:
            result["omitted"] = omitted
            result["hint"] = (
                f"{omitted} older notes left out to keep this result short; "
                "they are all still saved."
            )
        return json.dumps(result)

    def to_output(self) -> list[dict[str, Any]]:
        return [asdict(e) for e in self._entries]
