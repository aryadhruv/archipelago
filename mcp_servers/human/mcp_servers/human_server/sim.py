"""The simulated human: an LLM playing the person who assigned the task.

Like a real person, it doesn't open its files to answer. It answers from what it
knows (the task, its persona, knowledge.md), points to paths in the agent's
files, or shares one of its own files, which uploads it into the agent's
filesystem under ``from_user/``.

It runs outside the agent's sandbox, so its provider key and its files never
enter it: shared files go in through the environment's /data/populate.
"""

import asyncio
import io
import json
import tarfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import litellm
from loguru import logger
from utils.config import Config

# Model calls per question: a reply, plus retries when it names files it
# doesn't have.
MAX_ROUNDS = 3
MAX_KNOWLEDGE_CHARS = 50_000
MAX_LISTED_FILES = 300

# Past the question cap, or when the human's model fails.
UNAVAILABLE = "The human is unavailable at the moment. Please proceed with the task."
SHARE_FAILED = "I'm unable to share {name} right now. Please proceed without it."

# The human answers through this tool, so a file is shared only when it's
# named in the call, never inferred from the wording of the answer.
REPLY_TOOL = {
    "type": "function",
    "function": {
        "name": "reply",
        "description": "Answer the assistant, optionally uploading some of your files.",
        "parameters": {
            "type": "object",
            "properties": {
                "answer": {
                    "type": "string",
                    "description": "What you say to the assistant.",
                },
                "share": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Files to upload, by their path under 'Files you have'. "
                        "Leave empty unless you're sending something."
                    ),
                },
            },
            "required": ["answer"],
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
or summarize their contents. To send one, list it in the reply tool's `share`; \
that's the only way a file reaches the assistant.
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
- Always answer with the reply tool.
"""


Upload = Callable[[str, bytes], Awaitable[None]]


def _capped(lines: list[str]) -> list[str]:
    if len(lines) > MAX_LISTED_FILES:
        return [*lines[:MAX_LISTED_FILES], "- ... (more not shown)"]
    return lines


def _own_files(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    return _capped(
        [
            f"- {p.relative_to(root).as_posix()} ({p.stat().st_size} bytes)"
            for p in sorted(root.rglob("*"))
            if p.is_file()
        ]
    )


def _agent_files(listing: Path | None) -> list[str]:
    if listing is None or not listing.is_file():
        return []
    paths = [line.strip() for line in listing.read_text().splitlines()]
    return _capped([f"- /{p.lstrip('/')}" for p in paths if p])


def populate_upload(env_url: str, upload_dir: str) -> Upload:
    """Upload a file into the agent's filesystem through /data/populate.

    The tarball is extracted at the filesystem root with the member named
    ``<upload_dir>/<name>``, not into ``<upload_dir>`` itself, so populate's
    extraction filter resolves the upload folder too: if the agent turned it,
    or the file, into a symlink out of the filesystem, the upload is refused.
    """

    async def upload(name: str, data: bytes) -> None:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            member = tarfile.TarInfo(f"{upload_dir}/{name}")
            member.size = len(data)
            member.mode = 0o644
            tar.addfile(member, io.BytesIO(data))
        async with httpx.AsyncClient(timeout=120) as http:
            resp = await http.post(
                f"{env_url}/data/populate",
                params={"subsystem": "filesystem"},
                files={
                    "archive": ("share.tar.gz", buffer.getvalue(), "application/gzip")
                },
            )
        resp.raise_for_status()

    return upload


class SimulatedHuman:
    def __init__(
        self,
        config: Config,
        completion: Callable[..., Awaitable[Any]] = litellm.acompletion,
        upload: Upload | None = None,
    ) -> None:
        self.config = config
        self._completion = completion
        self._upload = upload or populate_upload(config.env_url, config.upload_dir)
        # Name each shared file got in from_user/, so two files with the same
        # name from different folders don't overwrite each other.
        self._shared_names: dict[Path, str] = {}
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
            own_files="\n".join(_own_files(self.config.files_root)) or "(none)",
            agent_files="\n".join(_agent_files(self.config.agent_files)) or "(none)",
        )

    def _name_for(self, src: Path) -> str:
        if src in self._shared_names:
            return self._shared_names[src]
        taken = set(self._shared_names.values())
        name, n = src.name, 2
        while name in taken:
            name = f"{src.stem}-{n}{src.suffix}"
            n += 1
        self._shared_names[src] = name
        return name

    async def share_file(self, path: str) -> str:
        """Upload one of the human's files into the agent's filesystem.

        Returns the path the agent sees, or an error message for the model.
        """
        root = self.config.files_root.resolve()
        src = (root / path.lstrip("/")).resolve()
        if not src.is_relative_to(root) or not src.is_file():
            return f"Error: you have no file at {path!r}."
        name = self._name_for(src)
        await self._upload(name, src.read_bytes())
        return f"/{self.config.upload_dir}/{name}"

    async def ask(self, question: str) -> str:
        if not self.config.model:
            raise ValueError("HUMAN_MODEL is not set")
        async with self._lock:
            if (
                self.config.max_questions is not None
                and self.questions_asked >= self.config.max_questions
            ):
                return UNAVAILABLE
            try:
                answer = await self._answer(question)
            except Exception:
                # A failed model call isn't the agent's fault and doesn't use
                # up a question.
                logger.exception("the human's model call failed")
                return UNAVAILABLE
            self.questions_asked += 1
            self.history += [
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer},
            ]
            return answer

    async def _answer(self, question: str) -> str:
        """One answer, sharing files along the way."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt()},
            *self.history,
            {"role": "user", "content": question},
        ]
        shared: list[str] = []
        # Files that couldn't be shared: the upload failed, or a path was
        # still wrong after the last try.
        failed: list[str] = []
        reply = ""
        for round_ in range(MAX_ROUNDS):
            # extra_args may raise num_retries, e.g. for a rate-limited free model.
            response = await self._completion(
                model=self.config.model,
                messages=messages,
                tools=[REPLY_TOOL],
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
            # A model that answers in plain text shares nothing.
            reply = (message.content or "").strip()
            if not tool_calls:
                break
            answers, results, missing = [], [], []
            for call in tool_calls:
                answer, result = await self._reply(
                    call.function.name,
                    call.function.arguments,
                    shared,
                    failed,
                    missing,
                )
                answers.append(answer)
                results.append(result)
            reply = "\n\n".join(a for a in answers if a) or reply
            if all(r is None for r in results):
                break
            if round_ == MAX_ROUNDS - 1:
                failed += missing
                break
            # Some files weren't shared: say why and let it reply again.
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
            for call, result in zip(tool_calls, results, strict=True):
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result or "Delivered.",
                    }
                )

        # The agent always learns where shared files landed, and which
        # couldn't be shared, whatever the model's reply says.
        names = dict.fromkeys(Path(p).name for p in failed)
        notes = [
            "\n".join(f"Shared: {p}" for p in shared),
            " ".join(SHARE_FAILED.format(name=n) for n in names),
        ]
        return "\n\n".join(p for p in [reply, *notes] if p) or "(no answer)"

    async def _reply(
        self,
        name: str,
        arguments: str,
        shared: list[str],
        failed: list[str],
        missing: list[str],
    ) -> tuple[str, str | None]:
        """Run one reply call: its answer, and what to tell the model, or None.

        Paths the agent now sees go in ``shared``, uploads that failed in
        ``failed``, and paths the human doesn't have in ``missing``.
        """
        if name != "reply":
            return "", f"Error: unknown tool {name!r}. Answer with reply."
        try:
            args = json.loads(arguments or "{}")
            answer = str(args.get("answer") or "").strip()
            paths = args.get("share") or []
        except (json.JSONDecodeError, AttributeError):
            return "", "Error: arguments must be JSON with an 'answer'."
        if isinstance(paths, str):
            paths = [paths]
        errors = []
        for path in map(str, paths):
            try:
                result = await self.share_file(path)
            except Exception:
                # Retrying won't fix the environment: tell the agent instead.
                logger.exception(f"could not upload {path!r}")
                failed.append(path)
                continue
            if result.startswith("Error"):
                errors.append(result)
                missing.append(path)
            elif result not in shared:
                shared.append(result)
        if not errors:
            return answer, None
        return answer, (
            "\n".join(errors) + "\nYour answer hasn't reached the assistant yet. "
            "Reply again, with corrected paths or none. Already shared: "
            + (", ".join(shared) or "nothing")
        )
