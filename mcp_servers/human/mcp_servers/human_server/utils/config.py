"""Settings for the simulated human, read from the environment the grid fills in."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_PERSONA = (
    "You are accommodating and busy. You answer the way a user answers an "
    "assistant's clarifying question: directly, in one to three sentences, "
    "with no pleasantries. You're happy to help, but you don't volunteer more "
    "than was asked."
)
# Arguments the human passes to litellm itself; extra_args can't set them.
RESERVED_ARGS = ("model", "messages", "tools")


@dataclass
class Config:
    model: str
    extra_args: dict[str, Any] = field(default_factory=dict)
    persona: str = DEFAULT_PERSONA
    task_prompt: str = ""
    max_questions: int | None = None
    # The human's own folder (knowledge.md, files/), on the host: it never
    # enters the agent's sandbox.
    data_root: Path = Path("human")
    # The agent's file paths, one per line, so the human can point to them.
    agent_files: Path | None = None
    # The agent's environment; shared files are posted to its /data/populate.
    env_url: str = "http://localhost:8080"
    # The folder under the agent's filesystem root that shared files land in.
    upload_dir: str = "from_user"

    def __post_init__(self) -> None:
        # Checked once at startup, so a bad setting stops the server instead
        # of failing every question.
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("HUMAN_MODEL must be a model string")
        if not isinstance(self.extra_args, dict):
            raise ValueError("HUMAN_EXTRA_ARGS must be a JSON object")
        if reserved := [k for k in RESERVED_ARGS if k in self.extra_args]:
            raise ValueError(f"HUMAN_EXTRA_ARGS can't set {', '.join(reserved)}")
        if self.max_questions is not None and (
            type(self.max_questions) is not int or self.max_questions < 0
        ):
            raise ValueError("HUMAN_MAX_QUESTIONS must be a whole number, at least 0")

    @property
    def files_root(self) -> Path:
        return self.data_root / "files"


def load_config() -> Config:
    max_questions = os.getenv("HUMAN_MAX_QUESTIONS", "").strip()
    agent_files = os.getenv("HUMAN_AGENT_FILES", "").strip()
    try:
        extra_args = json.loads(os.getenv("HUMAN_EXTRA_ARGS") or "{}")
    except json.JSONDecodeError as e:
        raise ValueError(f"HUMAN_EXTRA_ARGS isn't valid JSON: {e}") from e
    if max_questions and not max_questions.isdigit():
        raise ValueError("HUMAN_MAX_QUESTIONS must be a whole number, at least 0")
    return Config(
        model=os.getenv("HUMAN_MODEL", ""),
        extra_args=extra_args,
        persona=os.getenv("HUMAN_PERSONA", "").strip() or DEFAULT_PERSONA,
        task_prompt=os.getenv("HUMAN_TASK_PROMPT", "").strip(),
        max_questions=int(max_questions) if max_questions else None,
        data_root=Path(os.getenv("HUMAN_DATA_ROOT", "human")),
        agent_files=Path(agent_files) if agent_files else None,
        env_url=os.getenv("HUMAN_ENV_URL", "http://localhost:8080").rstrip("/"),
        upload_dir=os.getenv("HUMAN_UPLOAD_DIR", "from_user").strip("/"),
    )
