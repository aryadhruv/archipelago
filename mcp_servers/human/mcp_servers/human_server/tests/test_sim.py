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


class FakeUpload:
    def __init__(self):
        self.files: dict[str, bytes] = {}

    async def __call__(self, name, data):
        self.files[name] = data


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
    (data / "files" / "q4").mkdir()
    (data / "files" / "q4" / "budget.xlsx").write_bytes(b"q4-bytes")
    (data / "knowledge.md").write_text("The deadline is Friday.")
    (tmp_path / "agent_files.txt").write_text("reports/draft.md\n")
    (tmp_path / "secret.txt").write_text("outside")
    return Config(
        model="test/model",
        task_prompt="Write the summary.",
        data_root=data,
        agent_files=tmp_path / "agent_files.txt",
    )


@pytest.fixture
def upload() -> FakeUpload:
    return FakeUpload()


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


async def test_share_file_uploads_into_agent_fs(world, upload):
    human = SimulatedHuman(world, upload=upload)
    assert await human.share_file("q3/budget.xlsx") == "/from_user/budget.xlsx"
    assert upload.files == {"budget.xlsx": b"secret-bytes"}


async def test_share_file_keeps_same_names_apart(world, upload):
    human = SimulatedHuman(world, upload=upload)
    assert await human.share_file("q3/budget.xlsx") == "/from_user/budget.xlsx"
    assert await human.share_file("q4/budget.xlsx") == "/from_user/budget-2.xlsx"
    # Sharing the same file again keeps its name.
    assert await human.share_file("q3/budget.xlsx") == "/from_user/budget.xlsx"
    assert upload.files == {
        "budget.xlsx": b"secret-bytes",
        "budget-2.xlsx": b"q4-bytes",
    }


@pytest.mark.parametrize(
    "path", ["../knowledge.md", "../../secret.txt", "nope.txt", "q3"]
)
async def test_share_file_rejects_outside_or_missing(world, upload, path):
    human = SimulatedHuman(world, upload=upload)
    assert (await human.share_file(path)).startswith("Error")
    assert upload.files == {}


async def test_populate_upload_posts_a_one_file_tarball(monkeypatch):
    import io
    import tarfile

    import httpx
    from sim import populate_upload

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        body = request.read()
        start = body.index(b"\x1f\x8b")
        with tarfile.open(fileobj=io.BytesIO(body[start:]), mode="r:gz") as tar:
            seen["files"] = {m.name: tar.extractfile(m).read() for m in tar}
        return httpx.Response(200, json={})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    await populate_upload("http://env:8080", "from_user")("a.csv", b"x,y")
    # Extracted at the filesystem root, so the extraction filter checks
    # from_user/ itself for symlinks too.
    assert seen["url"] == "http://env:8080/data/populate?subsystem=filesystem"
    assert seen["files"] == {"from_user/a.csv": b"x,y"}


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
    human = SimulatedHuman(world, completion=completion, upload=FakeUpload())
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
    human = SimulatedHuman(world, completion=completion, upload=FakeUpload())
    answer = await human.ask("Budget?")
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
