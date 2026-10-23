"""Load this runner's ``.env`` and fail fast when a model's provider key is unset."""

import os
import sys
from pathlib import Path

import litellm
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_env_file(path: Path = ENV_FILE) -> None:
    """Load ``path`` into ``os.environ``; variables already set win."""
    load_dotenv(path, override=False)


def require_provider_keys(*models: str) -> None:
    """Exit before any work if a model's provider key is missing.

    Skipped behind the LLM gateway, which holds the provider keys itself.
    Without this, a missing key surfaces only when the first call returns 401.
    """
    if os.environ.get("LLM_GATEWAY_API_BASE"):
        return
    for model in models:
        missing = litellm.validate_environment(model=model)["missing_keys"]
        if missing:
            sys.exit(
                f"Missing {', '.join(missing)} for model {model!r}. "
                f"Set it in {ENV_FILE} or the environment."
            )
