"""Middleware that sanitizes Pydantic validation errors for LLM agents.

Intercepts verbose ``ValidationError`` messages (which include type metadata
and ``https://errors.pydantic.dev/`` URLs) and re-raises them as concise,
URL-free ``Exception`` instances that the MCP SDK wraps with ``isError=True``.
"""

from typing import override

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from loguru import logger
from mcp.types import CallToolRequestParams
from pydantic import ValidationError as PydanticValidationError


# A malformed call can turn a whole payload into one key, and every key is
# echoed back by name. Cut long ones so each error stays one short readable item.
_LOC_MAX_CHARS = 80
_LOC_HEAD_CHARS = 60


def _short_loc(segment: object) -> str:
    text = str(segment)
    if len(text) <= _LOC_MAX_CHARS and "\n" not in text:
        return text
    head = text[:_LOC_HEAD_CHARS].replace("\n", "\\n")
    return f"{head}... ({len(text)} characters)"


def format_validation_error(exc: PydanticValidationError) -> str:
    """Format a Pydantic ValidationError into a concise, URL-free string."""
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(_short_loc(segment) for segment in err["loc"])
        msg = err["msg"]
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "Validation error: " + "; ".join(parts)


class ValidationErrorSanitizerMiddleware(Middleware):
    """Catches Pydantic ``ValidationError`` and re-raises a concise ``Exception``."""

    @override
    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        try:
            return await call_next(context)
        except PydanticValidationError as exc:
            clean = format_validation_error(exc)
            logger.debug(
                f"Sanitized validation error for {context.message.name}: {clean}"
            )
            raise Exception(clean) from None  # noqa: TRY002
