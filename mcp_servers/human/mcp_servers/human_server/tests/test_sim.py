import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sim import REPLY_TOOL, SHARE_FAILED, UNAVAILABLE, SimulatedHuman
from utils.config import DEFAULT_PERSONA, Config


def _response(content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=2)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def _reply(answer, share=None, call_id="c1"):
    args = {"answer": answer} if share is None else {"answer": answer, "share": share}
    function = SimpleNamespace(name="reply", arguments=json.dumps(args))
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


async def test_ask_answers_through_the_reply_tool(world):
    completion = FakeCompletion(_response(tool_calls=[_reply("Friday.")]))
    assert await SimulatedHuman(world, completion=completion).ask("Due?") == "Friday."
    assert completion.calls[0]["tools"] == [REPLY_TOOL]


async def test_ask_shares_file_and_reports_path(world):
    upload = FakeUpload()
    completion = FakeCompletion(
        _response(tool_calls=[_reply("Sent you the budget.", ["q3/budget.xlsx"])])
    )
    human = SimulatedHuman(world, completion=completion, upload=upload)
    answer = await human.ask("Do you have the budget?")
    assert answer == "Sent you the budget.\n\nShared: /from_user/budget.xlsx"
    assert upload.files == {"budget.xlsx": b"secret-bytes"}
    assert len(completion.calls) == 1


@pytest.mark.parametrize(
    "answer",
    [
        "I've shared budget.xlsx with you.",
        "I can't send budget.xlsx, use your judgment.",
        "budget.xlsx is sharper than you think.",
    ],
)
async def test_wording_never_shares_or_retries(world, answer):
    # Only the share list sends files, whatever the answer says.
    upload = FakeUpload()
    for response in (_response(answer), _response(tool_calls=[_reply(answer)])):
        completion = FakeCompletion(response)
        human = SimulatedHuman(world, completion=completion, upload=upload)
        assert await human.ask("Budget?") == answer
        assert len(completion.calls) == 1
    assert upload.files == {}


async def test_wrong_path_is_retried_and_good_shares_kept(world):
    upload = FakeUpload()
    completion = FakeCompletion(
        _response(tool_calls=[_reply("Both attached.", ["q3/budget.xlsx", "nope"])]),
        _response(tool_calls=[_reply("Here's the Q4 one too.", ["q4/budget.xlsx"])]),
    )
    human = SimulatedHuman(world, completion=completion, upload=upload)
    answer = await human.ask("Budgets?")
    assert answer == (
        "Here's the Q4 one too.\n\n"
        "Shared: /from_user/budget.xlsx\nShared: /from_user/budget-2.xlsx"
    )
    tool_message = completion.calls[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "c1"
    assert "'nope'" in tool_message["content"]
    assert "/from_user/budget.xlsx" in tool_message["content"]


async def test_every_round_keeps_tools(world):
    completion = FakeCompletion(
        *[_response(tool_calls=[_reply("Here.", ["nope"], str(i))]) for i in range(3)]
    )
    answer = await SimulatedHuman(world, completion=completion).ask("File?")
    # Still wrong after the last try: the agent is told it isn't coming.
    assert answer == "Here.\n\n" + SHARE_FAILED.format(name="nope")
    assert len(completion.calls) == 3
    assert all(call["tools"] == [REPLY_TOOL] for call in completion.calls)


async def test_failed_upload_tells_agent_to_proceed(world):
    async def broken(name, data):
        raise RuntimeError("populate refused it")

    completion = FakeCompletion(
        _response(tool_calls=[_reply("Here it is.", ["q3/budget.xlsx"])])
    )
    human = SimulatedHuman(world, completion=completion, upload=broken)
    answer = await human.ask("Budget?")
    assert answer == "Here it is.\n\n" + SHARE_FAILED.format(name="budget.xlsx")
    # Not retried: another try won't fix the environment.
    assert len(completion.calls) == 1


async def test_failed_model_call_is_unavailable_and_not_counted(world):
    world.max_questions = 1

    class Flaky(FakeCompletion):
        async def __call__(self, **kwargs):
            if not self.calls:
                self.calls.append(kwargs)
                raise RuntimeError("429")
            return await super().__call__(**kwargs)

    completion = Flaky(_response("Friday."))
    human = SimulatedHuman(world, completion=completion)
    assert await human.ask("Due?") == UNAVAILABLE
    assert await human.ask("Due?") == "Friday."
    assert human.history == [
        {"role": "user", "content": "Due?"},
        {"role": "assistant", "content": "Friday."},
    ]


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
