"""Tool errors: over-long lines are shortened, nothing else changes."""

from types import SimpleNamespace

from mcp.types import ImageContent, TextContent

from runner.utils.mcp import (
    ERROR_LINE_MAX_CHARS,
    shorten_error_content,
    shorten_error_lines,
)

GARBLED = 'rows": "a,b,c\\n' + "1,2,3\\n" * 800
ERROR = (
    "2 validation errors for Args\n"
    "path\n"
    "  Field required [type=missing, input_type=dict]\n"
    f"{GARBLED}\n"
    "  Extra inputs are not permitted [type=extra_forbidden, input_type=str]"
)


def test_long_line_cut_every_line_kept():
    out = shorten_error_lines(ERROR)
    lines, original = out.split("\n"), ERROR.split("\n")
    assert len(lines) == len(original)
    assert lines[:3] == original[:3] and lines[4] == original[4]
    assert len(lines[3]) < ERROR_LINE_MAX_CHARS
    assert lines[3].startswith(GARBLED[:200])
    assert lines[3].endswith(GARBLED[-100:])
    assert f"[... {len(GARBLED) - 300} characters cut ...]" in lines[3]


def test_short_error_unchanged():
    text = "Error: file not found: /a.txt\n" + "x" * ERROR_LINE_MAX_CHARS
    assert shorten_error_lines(text) == text


def test_only_error_results_are_shortened():
    image = ImageContent(type="image", data="AA==", mimeType="image/png")
    content = [TextContent(type="text", text=ERROR), image]

    ok = shorten_error_content(SimpleNamespace(isError=False), content)
    assert ok is content

    failed = shorten_error_content(SimpleNamespace(isError=True), content)
    assert len(failed[0].text) < len(ERROR) and failed[1] is image
