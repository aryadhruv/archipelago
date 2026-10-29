import asyncio
import json
from collections import Counter
from pathlib import Path

from grid.events import (
    AgentEventNormalizer,
    flatten,
    grading_progress,
    split_tool,
    tail_lines,
)

FIXTURE = Path(__file__).parent / "fixtures" / "agent_events.jsonl"
SERVERS = ["filesystem_server", "docs_server", "code_execution_server"]


def _normalized() -> list[dict]:
    normalizer = AgentEventNormalizer(SERVERS)
    events = []
    for line in FIXTURE.read_text().splitlines():
        event = normalizer.normalize(json.loads(line))
        if event:
            events.append(event)
    return events


def test_split_tool():
    assert split_tool("filesystem_server_read_file", SERVERS) == (
        "filesystem_server",
        "read_file",
    )
    assert split_tool("toolbelt_add_tool", SERVERS) == ("toolbelt", "toolbelt_add_tool")
    assert split_tool("final_answer", SERVERS) == ("toolbelt", "final_answer")
    assert split_tool("mystery", SERVERS) == (None, "mystery")


def test_real_agent_log():
    events = _normalized()
    kinds = Counter(e["kind"] for e in events)
    assert kinds["tool_call"] == kinds["tool_result"] == 8
    assert kinds["final_answer"] == 1
    assert kinds["toolbelt"] == 2
    # The harness's "Starting step N" lines are dropped; steps come from
    # llm_usage lines, which this log predates.
    assert kinds["step"] == 0

    calls = [e for e in events if e["kind"] == "tool_call"]
    assert {e["server"] for e in calls} == {"toolbelt", "filesystem_server"}
    images = [e for e in calls if e["tool"] == "read_image_file"]
    assert len(images) == 4
    assert all(e["path"].startswith("/animals/") for e in images)

    results = [e for e in events if e["kind"] == "tool_result"]
    assert all(e["ok"] for e in results)
    # Image results are summarized, not dumped.
    assert sum(e["text"] == "[image]" for e in results) == 4
    timed = [e for e in results if e["server"] == "filesystem_server"]
    assert all(e["duration_ms"] and e["duration_ms"] > 1000 for e in timed)


def test_tool_error_and_usage():
    normalizer = AgentEventNormalizer(SERVERS)
    normalizer.normalize(
        {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "level": "INFO",
            "message": "Calling tool docs_server_read",
            "extra": {
                "message_type": "tool_call",
                "name": "docs_server_read",
                "ref": "r1",
            },
        }
    )
    result = normalizer.normalize(
        {
            "timestamp": "2026-01-01T00:00:02+00:00",
            "level": "ERROR",
            "message": "Tool docs_server_read failed",
            "extra": {
                "message_type": "tool_result",
                "name": "docs_server_read",
                "ref": "r1",
            },
        }
    )
    assert result["ok"] is False
    assert result["duration_ms"] == 2000
    usage = normalizer.normalize(
        {
            "level": "DEBUG",
            "message": "LLM call usage",
            "extra": {
                "message_type": "llm_usage",
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "latency_ms": 900,
            },
        }
    )
    assert usage["kind"] == "step"
    assert usage["tokens"] == 120
    assert usage["duration_ms"] == 900


def test_grading_progress():
    line = "2026-01-01 | INFO | x - [JUDGE][GRADER][START] task=t1 | criteria=..."
    event = grading_progress(line)
    assert event["state"] == "start" and event["ok"] is None
    assert event["text"].startswith("task=t1")
    result = grading_progress(
        "[JUDGE][GRADER][RESULT] task=t1 | result=FAIL | score=0.0"
    )
    assert result["state"] == "result" and result["ok"] is False
    assert grading_progress("[JUDGE][DIFF][METRICS] nothing") is None


def test_flatten_fills_every_field():
    event = flatten(
        {
            "seq": 3,
            "ts": 1.0,
            "run_id": "r",
            "kind": "step",
            "data": {"text": "hi", "x": 1},
        }
    )
    assert event["seq"] == 3 and event["text"] == "hi" and event["x"] == 1
    assert event["server"] is None and event["ok"] is None


def test_tail_reads_until_stop(tmp_path):
    path = tmp_path / "log.jsonl"
    seen: list[str] = []

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(tail_lines(path, seen.append, stop, poll_sec=0.01))
        await asyncio.sleep(0.03)
        with path.open("a") as f:
            f.write("one\ntw")
        await asyncio.sleep(0.03)
        with path.open("a") as f:
            f.write("o\nthree")
        stop.set()
        await task

    asyncio.run(scenario())
    assert seen == ["one", "two", "three"]


def _call_and_result(normalizer, name, args, payload, ref="q1"):
    call = normalizer.normalize(
        {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "level": "INFO",
            "message": f"Calling tool {name}",
            "extra": {
                "message_type": "tool_call",
                "name": name,
                "ref": ref,
                "payload": args,
            },
        }
    )
    result = normalizer.normalize(
        {
            "timestamp": "2026-01-01T00:00:01+00:00",
            "level": "INFO",
            "message": f"Tool {name} done",
            "extra": {
                "message_type": "tool_result",
                "name": name,
                "ref": ref,
                "payload": payload,
            },
        }
    )
    return call, result


def test_ask_human_question_and_answer_are_readable():
    # The shapes the agent runner logs (from a real run): arguments as a JSON
    # string, the result as MCP content blocks.
    question = "Please provide:\n1. The shortlist\n2. Headcount " + "x" * 400
    answer = "15 people attending. I’ve shared the guest list. " + "y" * 300
    payload = [
        {
            "type": "text",
            "text": f"{answer}\n\nShared: /from_user/guest_list.csv",
            "annotations": None,
            "meta": None,
        }
    ]
    normalizer = AgentEventNormalizer([*SERVERS, "human_server"])
    call, result = _call_and_result(
        normalizer,
        "human_server_ask_human",
        json.dumps({"question": question}),
        payload,
    )
    assert json.loads(call["args_preview"]) == {"question": question}
    # Plain text with real newlines, past the usual preview length, so the
    # Shared: line survives.
    assert result["text"] == f"{answer}\n\nShared: /from_user/guest_list.csv"
    # Arguments as a dict (some harnesses log them parsed) work the same.
    call, _ = _call_and_result(
        normalizer, "human_server_ask_human", {"question": "Due?"}, payload, "q2"
    )
    assert json.loads(call["args_preview"]) == {"question": "Due?"}


def test_tool_results_unwrap_mcp_content_blocks():
    normalizer = AgentEventNormalizer(SERVERS)
    cases = [
        ([{"type": "text", "text": "a,b\n1,2"}], "a,b\n1,2"),
        (json.dumps([{"type": "text", "text": "serialized"}]), "serialized"),
        (
            {"content": [{"type": "text", "text": "wrapped"}], "isError": False},
            "wrapped",
        ),
        (
            [
                {"type": "text", "text": "Here:"},
                {"type": "image", "data": "AAAA", "mimeType": "image/png"},
            ],
            "Here:\n[image]",
        ),
        (
            [{"type": "resource", "resource": {"uri": "file:///x", "text": "body"}}],
            "body",
        ),
        ([{"type": "resource_link", "uri": "file:///x", "name": "x.csv"}], "[x.csv]"),
        ([{"type": "audio", "data": "AAAA"}], "[audio]"),
        # Not content blocks: passed through as before.
        ("plain text", "plain text"),
        ({"rows": 3}, '{"rows": 3}'),
    ]
    for i, (payload, expected) in enumerate(cases):
        _, result = _call_and_result(
            normalizer, "filesystem_server_read", "{}", payload, f"r{i}"
        )
        assert result["text"] == expected, payload
    # Other tools keep the short preview.
    _, result = _call_and_result(
        normalizer,
        "filesystem_server_read",
        "{}",
        [{"type": "text", "text": "z" * 900}],
    )
    assert len(result["text"]) == 300
