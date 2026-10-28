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


@dataclass
class Config:
    model: str
    extra_args: dict[str, Any] = field(default_factory=dict)
    persona: str = DEFAULT_PERSONA
    task_prompt: str = ""
    max_questions: int | None = None
    # The human's own folder (knowledge.md, files/). The agent can't reach it.
    data_root: Path = Path("/.apps_data/human")
    # The agent's filesystem root, and the folder under it that shared files land in.
    fs_root: Path = Path("/filesystem")
    upload_dir: str = "from_user"

    @property
    def files_root(self) -> Path:
        return self.data_root / "files"


def load_config() -> Config:
    max_questions = os.getenv("HUMAN_MAX_QUESTIONS", "").strip()
    return Config(
        model=os.getenv("HUMAN_MODEL", ""),
        extra_args=json.loads(os.getenv("HUMAN_EXTRA_ARGS") or "{}"),
        persona=os.getenv("HUMAN_PERSONA", "").strip() or DEFAULT_PERSONA,
        task_prompt=os.getenv("HUMAN_TASK_PROMPT", "").strip(),
        max_questions=int(max_questions) if max_questions else None,
        data_root=Path(os.getenv("HUMAN_DATA_ROOT", "/.apps_data/human")),
        fs_root=Path(os.getenv("HUMAN_FS_ROOT", "/filesystem")),
        upload_dir=os.getenv("HUMAN_UPLOAD_DIR", "from_user").strip("/"),
    )
