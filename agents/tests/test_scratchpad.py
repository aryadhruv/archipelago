"""Append-only scratchpad: the store, the hash chain, and the agent wiring."""

import json
from types import SimpleNamespace

import pytest

from runner.agents.models import AgentRunInput, LitellmOutputMessage
from runner.agents.react_toolbelt_agent.main import ReActAgent
from runner.agents.react_toolbelt_agent.scratchpad import (
    MAX_NOTE_CHARS,
    MAX_READ_CHARS,
    SCRATCHPAD_TOOLS,
    Scratchpad,
    verify_chain,
)
from runner.agents.react_toolbelt_agent.tools import MetaToolHandler


def _write(sp: Scratchpad, note, step: int = 1) -> dict:
    return json.loads(sp.handle("scratchpad_write", json.dumps({"note": note}), step))


def _read(sp: Scratchpad, **args) -> dict:
    return json.loads(sp.handle("scratchpad_read", json.dumps(args), 1))


def test_write_then_read_oldest_first():
    sp = Scratchpad()
    assert _write(sp, "plan", 1) == {"success": True, "index": 0}
    assert _write(sp, "step one done", 2)["index"] == 1
    assert _read(sp) == {
        "total": 2,
        "notes": [
            {"index": 0, "step": 1, "note": "plan"},
            {"index": 1, "step": 2, "note": "step one done"},
        ],
    }
    assert _read(sp, last_n=1)["notes"] == [
        {"index": 1, "step": 2, "note": "step one done"}
    ]
    assert _read(sp, last_n=1.0)["notes"][0]["index"] == 1


@pytest.mark.parametrize("note", ["", "   ", None, 3, ["x"]])
def test_write_rejects_empty_or_non_string(note):
    sp = Scratchpad()
    assert "error" in _write(sp, note)
    assert sp.entries == ()


def test_write_rejects_oversized_note():
    sp = Scratchpad()
    assert "error" in _write(sp, "x" * (MAX_NOTE_CHARS + 1))


@pytest.mark.parametrize("last_n", [0, -1, True, "2", 1.5])
def test_read_rejects_bad_last_n(last_n):
    sp = Scratchpad()
    _write(sp, "a")
    assert "error" in _read(sp, last_n=last_n)


def test_bad_arguments():
    sp = Scratchpad()
    assert "error" in json.loads(sp.handle("scratchpad_write", "{not json", 1))
    assert "error" in json.loads(sp.handle("scratchpad_write", "[]", 1))


def test_lone_surrogate_does_not_crash():
    sp = Scratchpad()
    result = json.loads(sp.handle("scratchpad_write", '{"note": "half \\ud83d"}', 1))
    assert result["success"]
    assert verify_chain(sp.to_output())
    assert "notes" in _read(sp)


def test_read_is_capped_and_keeps_the_newest():
    sp = Scratchpad()
    for i in range(20):
        _write(sp, f"{i}:" + "x" * (MAX_NOTE_CHARS - 10))
    result = _read(sp)
    assert result["total"] == 20
    assert result["omitted"] > 0
    assert result["notes"][-1]["index"] == 19
    assert len(json.dumps(result["notes"])) <= MAX_READ_CHARS + 100


def test_chain_detects_edits_and_gaps():
    sp = Scratchpad()
    for note in ("a", "b", "c"):
        _write(sp, note)
    out = sp.to_output()
    assert verify_chain(out)

    edited = json.loads(json.dumps(out))
    edited[1]["note"] = "rewritten"
    assert not verify_chain(edited)

    retimed = json.loads(json.dumps(out))
    retimed[0]["elapsed_sec"] += 5
    assert not verify_chain(retimed)

    assert not verify_chain([out[0], out[2]])
    assert not verify_chain(out[1:])


@pytest.mark.parametrize(
    "bad",
    [None, "x", [None], [{}], [{"index": 0}], [{"index": False, "step": 1}]],
)
def test_chain_returns_false_on_malformed_input(bad):
    assert verify_chain(bad) is False


def test_no_edit_or_delete_tool():
    names = {t["function"]["name"] for t in SCRATCHPAD_TOOLS}
    assert names == {"scratchpad_write", "scratchpad_read"}
    assert not hasattr(Scratchpad, "remove")


def test_descriptions_ask_for_plan_and_steps():
    write = SCRATCHPAD_TOOLS[0]["function"]["description"].lower()
    assert "plan" in write and "step" in write
    for tool in SCRATCHPAD_TOOLS:
        text = json.dumps(tool).lower()
        assert "think" not in text
        # Told it's private: no hint that anyone reads or scores the notes.
        for word in ("review", "graded", "grading", "visible", "evaluat"):
            assert word not in text


# --- agent wiring -----------------------------------------------------------


def _agent(**config) -> ReActAgent:
    agent = ReActAgent(
        AgentRunInput(
            trajectory_id="t",
            initial_messages=[],
            mcp_gateway_url="http://gateway.invalid/mcp",
            mcp_gateway_auth_token=None,
            orchestrator_model="openrouter/x/y",
            orchestrator_extra_args=None,
            agent_config_values={"context_window_tokens": 100_000, **config},
        )
    )
    agent.meta_tool_handler = MetaToolHandler({}, set(), 80)
    agent.start_time = 0
    return agent


def _call(i: int, name: str, args: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"c{i}", function=SimpleNamespace(name=name, arguments=json.dumps(args))
    )


def _tool_names(agent: ReActAgent) -> list[str]:
    return [t["function"]["name"] for t in agent._get_tools()]


@pytest.mark.parametrize("value", [False, None, 1, "1", "yes"])
def test_off_unless_true(value):
    config = {} if value is None else {"scratchpad": value}
    agent = _agent(**config)
    assert agent.scratchpad is None
    assert "scratchpad_write" not in _tool_names(agent)
    assert agent._build_output().output is None


@pytest.mark.parametrize("value", [True, "true", " True "])
def test_on(value):
    agent = _agent(scratchpad=value)
    names = _tool_names(agent)
    assert "scratchpad_write" in names and "scratchpad_read" in names
    assert agent._build_output().output == {"scratchpad": []}


async def test_notes_saved_in_output():
    agent = _agent(scratchpad=True)
    agent._step_number = 3
    await agent._handle_tool_calls(
        None, [_call(1, "scratchpad_write", {"note": "plan"})]
    )
    out = agent._build_output().output["scratchpad"]
    assert [(e["note"], e["step"]) for e in out] == [("plan", 3)]
    assert verify_chain(out)


async def test_note_in_same_batch_as_final_answer_is_kept():
    agent = _agent(scratchpad=True)
    await agent._handle_tool_calls(
        None,
        [
            _call(1, "final_answer", {"answer": "done", "status": "completed"}),
            _call(2, "scratchpad_write", {"note": "after"}),
        ],
    )
    assert agent._finalized
    assert [e.note for e in agent.scratchpad.entries] == ["after"]
    assert {m.tool_call_id for m in agent.messages} == {"c1", "c2"}


async def test_note_answered_when_final_answer_is_rejected():
    agent = _agent(scratchpad=True)
    agent.meta_tool_handler.handle(
        "todo_write",
        json.dumps(
            {
                "todos": [{"id": "a", "content": "x", "status": "pending"}],
                "merge": False,
            }
        ),
    )
    await agent._handle_tool_calls(
        None,
        [
            _call(1, "final_answer", {"answer": "done", "status": "completed"}),
            _call(2, "scratchpad_write", {"note": "kept"}),
        ],
    )
    assert not agent._finalized
    assert [e.note for e in agent.scratchpad.entries] == ["kept"]
    assert {m.tool_call_id for m in agent.messages} == {"c1", "c2"}


@pytest.mark.parametrize("rejected", [True, False])
async def test_every_call_answered_after_final_answer(rejected):
    agent = _agent()
    if rejected:
        agent.meta_tool_handler.handle(
            "todo_write",
            json.dumps(
                {
                    "todos": [{"id": "a", "content": "x", "status": "pending"}],
                    "merge": False,
                }
            ),
        )
    await agent._handle_tool_calls(
        None,
        [
            _call(1, "todo_write", {"todos": [], "merge": True}),
            _call(2, "final_answer", {"answer": "done", "status": "completed"}),
            _call(3, "fs_read", {}),
            _call(4, "todo_write", {"todos": [], "merge": True}),
        ],
    )
    assert agent._finalized is not rejected
    assert [m.tool_call_id for m in agent.messages] == ["c1", "c2", "c3", "c4"]
    assert agent.messages[2].content.startswith("Not run")
    assert agent.messages[3].content.startswith("Not run")


def test_no_duplicate_names_with_a_same_named_mcp_tool():
    agent = _agent(scratchpad=True, use_toolbelt=False)
    clash = {
        "type": "function",
        "function": {"name": "scratchpad_write", "parameters": {}},
    }
    agent.all_tools["scratchpad_write"] = clash
    agent.toolbelt.add("scratchpad_write")
    names = _tool_names(agent)
    assert len(names) == len(set(names))


async def test_results_keep_call_order(monkeypatch):
    agent = _agent(scratchpad=True)

    async def fake_mcp(client, tool_call, deferred):
        agent.messages.append(
            LitellmOutputMessage(
                role="tool",
                tool_call_id=tool_call.id,
                name=tool_call.function.name,
                content="ok",
            )
        )
        deferred.append({"role": "user", "content": "image"})

    monkeypatch.setattr(agent, "_execute_mcp_tool", fake_mcp)
    agent.all_tools["fs_read"] = {"type": "function", "function": {"name": "fs_read"}}
    agent.toolbelt.add("fs_read")
    await agent._handle_tool_calls(
        None,
        [
            _call(1, "fs_read", {}),
            _call(2, "scratchpad_write", {"note": "n"}),
            _call(3, "todo_write", {"todos": [], "merge": True}),
            _call(4, "fs_read", {}),
        ],
    )
    roles_ids = [
        (m["role"], None) if isinstance(m, dict) else (m.role, m.tool_call_id)
        for m in agent.messages
    ]
    assert roles_ids == [
        ("tool", "c1"),
        ("tool", "c2"),
        ("tool", "c3"),
        ("tool", "c4"),
        ("user", None),  # one deferred image per MCP call, after all results
        ("user", None),
    ]
