"""The simulated human: an LLM playing the person who assigned the task.

Like a real person, it doesn't open its files to answer. It answers from what it
knows (the task, its persona, knowledge.md), points to paths in the agent's
files, or shares one of its own files, which copies it into the agent's
filesystem under ``from_user/``.
"""

import asyncio
import json
import re
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import litellm
from loguru import logger
from utils.config import Config

# Model calls per question: rounds that may share files, then a final answer.
MAX_ROUNDS = 4
MAX_KNOWLEDGE_CHARS = 50_000
MAX_LISTED_FILES = 300

UNAVAILABLE = "The user isn't available right now. Use your best judgment."

# A reply that says a file was sent, to catch a model that claims to share
# without calling share_file.
_CLAIMS_SHARE = re.compile(r"\b(shar|sent|send|upload|attach)", re.IGNORECASE)
SHARE_NUDGE = (
    "You said you'd share a file, but you didn't call share_file, so the "
    "assistant has nothing. Call share_file now, or answer without claiming "
    "to have sent it."
)

SHARE_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "share_file",
        "description": (
            "Upload one of your files to the assistant. Use it when the "
            "assistant needs a file only you have."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file's path as listed under 'Files you have'.",
                }
            },
            "required": ["path"],
        },
    },
}

SYSTEM_PROMPT = """\
You are the person who assigned a task to an AI assistant. The assistant is \
working on it now and has stopped to ask you a question. Answer as that \
person, in your own voice.

# Your persona
{persona}

# The task you gave the assistant
{task_prompt}

# What you know
{knowledge}

# Files you have
These are on your own computer; the assistant can't see them. You know roughly \
what each one is from its name, but you don't have them open, so never quote \
or summarize their contents. If the assistant needs one, upload it with the \
share_file tool. Saying you've sent a file doesn't send it: only share_file \
does, so call it before you tell them it's on its way.
{own_files}

# Files the assistant can see
You can point the assistant to one of these by its path.
{agent_files}

# Rules
- Answer only from what you know above. If you don't know, say so plainly and \
let the assistant use its judgment. Never make up facts or figures.
- Answer the question asked. Don't do the task for the assistant and don't \
volunteer the full solution.
- Be brief, as your persona would be. No preamble.
"""


def _listing(root: Path, sizes: bool) -> list[str]:
    if not root.is_dir():
        return []
    lines = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        lines.append(f"- {rel} ({path.stat().st_size} bytes)" if sizes else f"- /{rel}")
        if len(lines) == MAX_LISTED_FILES:
            lines.append("- ... (more not shown)")
            break
    return lines


class SimulatedHuman:
    def __init__(
        self,
        config: Config,
        completion: Callable[..., Awaitable[Any]] = litellm.acompletion,
    ) -> None:
        self.config = config
        self._completion = completion
        # Earlier questions and answers in this run, so follow-ups stay coherent.
        self.history: list[dict[str, Any]] = []
        self.questions_asked = 0
        # The agent may call in parallel; one conversation, one question at a time.
        self._lock = asyncio.Lock()

    def system_prompt(self) -> str:
        knowledge_file = self.config.data_root / "knowledge.md"
        knowledge = (
            knowledge_file.read_text(errors="replace")[:MAX_KNOWLEDGE_CHARS].strip()
            if knowledge_file.is_file()
            else ""
        )
        return SYSTEM_PROMPT.format(
            persona=self.config.persona,
            task_prompt=self.config.task_prompt or "(not given)",
            knowledge=knowledge or "Nothing beyond the task itself.",
            own_files="\n".join(_listing(self.config.files_root, sizes=True))
            or "(none)",
            agent_files="\n".join(_listing(self.config.fs_root, sizes=False))
            or "(none)",
        )

    def share_file(self, path: str) -> str:
        """Copy one of the human's files into the agent's filesystem.

        Returns the path the agent sees, or an error message for the model.
        """
        root = self.config.files_root.resolve()
        src = (root / path.lstrip("/")).resolve()
        if not src.is_relative_to(root) or not src.is_file():
            return f"Error: you have no file at {path!r}."
        dest_dir = self.config.fs_root / self.config.upload_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest_dir / src.name)
        return f"/{self.config.upload_dir}/{src.name}"

    async def ask(self, question: str) -> str:
        if not self.config.model:
            raise ValueError("HUMAN_MODEL is not set")
        async with self._lock:
            if (
                self.config.max_questions is not None
                and self.questions_asked >= self.config.max_questions
            ):
                return UNAVAILABLE
            self.questions_asked += 1

            messages: list[dict[str, Any]] = [
                {"role": "system", "content": self.system_prompt()},
                *self.history,
                {"role": "user", "content": question},
            ]
            shared: list[str] = []
            reply = ""
            for round_ in range(MAX_ROUNDS):
                tools = {"tools": [SHARE_FILE_TOOL]} if round_ < MAX_ROUNDS - 1 else {}
                # extra_args may raise num_retries, e.g. for a rate-limited free model.
                response = await self._completion(
                    model=self.config.model,
                    messages=messages,
                    **tools,
                    **{"num_retries": 3, **self.config.extra_args},
                )
                message = response.choices[0].message
                usage = getattr(response, "usage", None)
                if usage is not None:
                    logger.info(
                        f"human usage: prompt={usage.prompt_tokens} "
                        f"completion={usage.completion_tokens}"
                    )
                tool_calls = getattr(message, "tool_calls", None) or []
                if not tool_calls:
                    reply = (message.content or "").strip()
                    if (
                        not shared
                        and round_ < MAX_ROUNDS - 2
                        and self._claims_share(reply)
                    ):
                        messages += [
                            {"role": "assistant", "content": reply},
                            {"role": "user", "content": SHARE_NUDGE},
                        ]
                        continue
                    break
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.content,
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.function.name,
                                    "arguments": call.function.arguments,
                                },
                            }
                            for call in tool_calls
                        ],
                    }
                )
                for call in tool_calls:
                    result = self._run_tool(call.function.name, call.function.arguments)
                    if not result.startswith("Error") and result not in shared:
                        shared.append(result)
                    messages.append(
                        {"role": "tool", "tool_call_id": call.id, "content": result}
                    )

            # The agent always learns where shared files landed, whatever the
            # model's reply says.
            answer = (
                "\n\n".join(
                    part
                    for part in [reply, "\n".join(f"Shared: {p}" for p in shared)]
                    if part
                )
                or "(no answer)"
            )
            self.history += [
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer},
            ]
            return answer

    def _claims_share(self, reply: str) -> bool:
        root = self.config.files_root
        names = (
            [p.name for p in root.rglob("*") if p.is_file()] if root.is_dir() else []
        )
        lowered = reply.lower()
        return bool(_CLAIMS_SHARE.search(reply)) and any(
            name.lower() in lowered for name in names
        )

    def _run_tool(self, name: str, arguments: str) -> str:
        if name != "share_file":
            return f"Error: unknown tool {name!r}."
        try:
            path = json.loads(arguments or "{}").get("path", "")
        except json.JSONDecodeError:
            return "Error: arguments must be JSON with a 'path'."
        return self.share_file(str(path))
