"""Load this runner's ``.env`` and fail fast when a model's provider key is unset."""

import os
import sys
from fnmatch import fnmatchcase
from pathlib import Path

import httpx
import litellm
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_env_file(path: Path = ENV_FILE) -> None:
    """Load ``path`` into ``os.environ``; variables already set win."""
    load_dotenv(path, override=False)


def require_provider_keys(*models: str) -> None:
    """Exit before any work if a model's provider key is missing.

    Skipped behind the LLM gateway, which holds the provider keys itself. A
    LiteLLM proxy (both variables set, as in ``utils/llm.py``) holds them too,
    so it is asked instead whether it accepts its key and serves each model.
    Without this, a missing key surfaces only when the first call returns 401.
    """
    if os.environ.get("LLM_GATEWAY_API_BASE"):
        return
    base = os.environ.get("LITELLM_PROXY_API_BASE")
    key = os.environ.get("LITELLM_PROXY_API_KEY")
    if base and key:
        _check_proxy(base, key, models)
        return
    for model in models:
        missing = litellm.validate_environment(model=model)["missing_keys"]
        if missing:
            sys.exit(
                f"Missing {', '.join(missing)} for model {model!r}. "
                f"Set it in {ENV_FILE} or the environment."
            )


def _check_proxy(base: str, key: str, models: tuple[str, ...]) -> None:
    """Exit unless the proxy accepts ``key`` and serves every model in ``models``.

    In proxy mode litellm sends the model string without its ``litellm_proxy/``
    prefix, so that is the alias the proxy must list. A wildcard route such as
    ``azure/*`` covers every alias it matches. ``/v1/models`` leaves out some
    routable aliases (router group aliases, per-key aliases); for those, set
    ``LITELLM_PROXY_CHECK_MODELS=false`` to check only the key.
    """
    url = f"{base.rstrip('/').removesuffix('/v1')}/v1/models"
    # Errors name the URL without any user:pass@ it embeds.
    shown = httpx.URL(url).copy_with(username=None, password=None)
    try:
        response = httpx.get(
            url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=15,
            follow_redirects=True,
        )
        response.raise_for_status()
        served = {m["id"] for m in response.json()["data"]}
    except httpx.HTTPStatusError as e:
        reason = f"HTTP {e.response.status_code} from {shown}"
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        reason = f"{type(e).__name__} from {shown}"
    else:
        reason = None
    if reason:
        sys.exit(
            f"LiteLLM proxy check failed: {reason}. Check LITELLM_PROXY_API_BASE and "
            f"LITELLM_PROXY_API_KEY in {ENV_FILE} or the environment."
        )
    if os.environ.get("LITELLM_PROXY_CHECK_MODELS", "").lower() == "false":
        return
    wildcards = [route for route in served if "*" in route]
    for model in models:
        alias = model.removeprefix("litellm_proxy/")
        if alias in served or any(fnmatchcase(alias, w) for w in wildcards):
            continue
        listed = sorted(served)
        more = f" and {len(listed) - 20} more" if len(listed) > 20 else ""
        sys.exit(
            f"LiteLLM proxy does not list {alias!r} (for model {model!r}); it lists "
            f"{', '.join(listed[:20]) or 'nothing'}{more}. If the proxy routes it "
            "anyway, set LITELLM_PROXY_CHECK_MODELS=false."
        )
