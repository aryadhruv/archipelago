import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sim import SHARE_NUDGE, UNAVAILABLE, SimulatedHuman
from utils.config import DEFAULT_PERSONA, Config


def _response(content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=2)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def _share(path, call_id="c1"):
    function = SimpleNamespace(name="share_file", arguments=json.dumps({"path": path}))
    return SimpleNamespace(id=call_id, function=function)


class FakeCompletion:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


@pytest.fixture
def world(tmp_path: Path) -> Config:
    data = tmp_path / "human"
    (data / "files" / "q3").mkdir(parents=True)
    (data / "files" / "q3" / "budget.xlsx").write_bytes(b"secret-bytes")
    (data / "knowledge.md").write_text("The deadline is Friday.")
    (tmp_path / "fs" / "reports").mkdir(parents=True)
    (tmp_path / "fs" / "reports" / "draft.md").write_text("draft")
    (tmp_path / "secret.txt").write_text("outside")
    return Config(
        model="test/model",
        task_prompt="Write the summary.",
        data_root=data,
        fs_root=tmp_path / "fs",
    )


def test_system_prompt(world):
    prompt = SimulatedHuman(world).system_prompt()
    assert DEFAULT_PERSONA in prompt
    assert "Write the summary." in prompt
    assert "The deadline is Friday." in prompt
    assert "- q3/budget.xlsx (12 bytes)" in prompt
    assert "- /reports/draft.md" in prompt
    # It knows its files by name only.
    assert "secret-bytes" not in prompt


def test_persona_override(world):
    world.persona = "Terse manager."
    prompt = SimulatedHuman(world).system_prompt()
    assert "Terse manager." in prompt
    assert DEFAULT_PERSONA not in prompt


def test_share_file_copies_into_agent_fs(world):
    human = SimulatedHuman(world)
    assert human.share_file("q3/budget.xlsx") == "/from_user/budget.xlsx"
    assert (world.fs_root / "from_user" / "budget.xlsx").read_bytes() == b"secret-bytes"


@pytest.mark.parametrize(
    "path", ["../knowledge.md", "../../secret.txt", "nope.txt", "q3"]
)
def test_share_file_rejects_outside_or_missing(world, path):
    assert SimulatedHuman(world).share_file(path).startswith("Error")
    assert not (world.fs_root / "from_user").exists()


async def test_ask_answers_and_keeps_history(world):
    completion = FakeCompletion(_response("Friday."), _response("Yes, the Q3 one."))
    human = SimulatedHuman(world, completion=completion)
    assert await human.ask("When is it due?") == "Friday."
    assert await human.ask("And which quarter?") == "Yes, the Q3 one."
    second = completion.calls[1]["messages"]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "user"]
    assert second[2]["content"] == "Friday."
    assert completion.calls[0]["model"] == "test/model"


async def test_ask_shares_file_and_reports_path(world):
    completion = FakeCompletion(
        _response(tool_calls=[_share("q3/budget.xlsx")]),
        _response("Sent you the budget."),
    )
    human = SimulatedHuman(world, completion=completion)
    answer = await human.ask("Do you have the budget?")
    assert answer == "Sent you the budget.\n\nShared: /from_user/budget.xlsx"
    tool_message = completion.calls[1]["messages"][-1]
    assert tool_message == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": "/from_user/budget.xlsx",
    }


async def test_last_round_has_no_tools(world):
    completion = FakeCompletion(
        _response(tool_calls=[_share("nope", "a")]),
        _response(tool_calls=[_share("nope", "b")]),
        _response(tool_calls=[_share("nope", "c")]),
        _response("I don't have that."),
    )
    answer = await SimulatedHuman(world, completion=completion).ask("File?")
    assert answer == "I don't have that."
    assert all("tools" in call for call in completion.calls[:3])
    assert "tools" not in completion.calls[3]


async def test_claimed_share_without_tool_call_is_nudged(world):
    completion = FakeCompletion(
        _response("I've shared budget.xlsx with you."),
        _response(tool_calls=[_share("q3/budget.xlsx")]),
        _response("Sent."),
    )
    answer = await SimulatedHuman(world, completion=completion).ask("Budget?")
    assert answer == "Sent.\n\nShared: /from_user/budget.xlsx"
    assert {"role": "user", "content": SHARE_NUDGE} in completion.calls[1]["messages"]


async def test_mentioning_a_file_without_claiming_to_send_it_is_fine(world):
    completion = FakeCompletion(
        _response("It's all in budget.xlsx, ask if you need it.")
    )
    human = SimulatedHuman(world, completion=completion)
    assert await human.ask("Where?") == "It's all in budget.xlsx, ask if you need it."
    assert len(completion.calls) == 1


async def test_question_cap(world):
    world.max_questions = 1
    completion = FakeCompletion(_response("Friday."))
    human = SimulatedHuman(world, completion=completion)
    assert await human.ask("Due?") == "Friday."
    assert await human.ask("Again?") == UNAVAILABLE
    assert len(completion.calls) == 1


async def test_extra_args_passed(world):
    world.extra_args = {"temperature": 0}
    completion = FakeCompletion(_response("ok"))
    await SimulatedHuman(world, completion=completion).ask("q")
    assert completion.calls[0]["temperature"] == 0
    assert completion.calls[0]["num_retries"] == 3


async def test_extra_args_override_retries(world):
    world.extra_args = {"num_retries": 8}
    completion = FakeCompletion(_response("ok"))
    await SimulatedHuman(world, completion=completion).ask("q")
    assert completion.calls[0]["num_retries"] == 8
