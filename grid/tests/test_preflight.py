import asyncio
import contextlib
import os
import signal
import subprocess
import time

import httpx
import pytest

from grid import preflight


def _serve(monkeypatch, handler):
    real = httpx.AsyncClient

    def client(**kwargs):
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(preflight.httpx, "AsyncClient", client)


def _check(models):
    return asyncio.run(preflight._openrouter_check(models))


@pytest.fixture
def served(monkeypatch):
    _serve(
        monkeypatch,
        lambda _: httpx.Response(200, json={"data": [{"id": "vendor/live:free"}]}),
    )


def test_openrouter_models_served(served):
    check = _check(["openrouter/vendor/live:free", "litellm_proxy/other"])
    assert check.status == "pass"


def test_openrouter_model_gone(served):
    check = _check(["openrouter/vendor/live:free", "openrouter/vendor/gone:free"])
    assert check.status == "fail"
    assert check.detail == "not served: vendor/gone:free"


def test_no_openrouter_models_skips_the_check():
    assert _check(["litellm_proxy/alias", "anthropic/claude"]) is None


def test_unreachable_list_only_warns(monkeypatch):
    def down(request):
        raise httpx.ConnectError("offline", request=request)

    _serve(monkeypatch, down)
    assert _check(["openrouter/vendor/live:free"]).status == "warn"


def test_round_trip_all_ok():
    check = preflight._round_trip_result([{"id": "a", "status": "ok"}])
    assert check.status == "pass"


def test_round_trip_error_fails():
    check = preflight._round_trip_result(
        [
            {"id": "a", "status": "ok"},
            {"id": "b", "status": "error", "error": "NotFoundError", "detail": "404"},
        ]
    )
    assert check.status == "fail"
    assert check.detail == "b: NotFoundError 404"


@pytest.mark.parametrize(
    ("result", "detail"),
    [
        ({"status": "text"}, "a: wrote the tool call as text"),
        ({"status": "no_call"}, "a: didn't call the tool"),
        ({"status": "error", "error": "RateLimitError"}, "a: RateLimitError"),
        ({"status": "error", "error": "Timeout"}, "a: Timeout"),
        (
            {"status": "error", "error": "ServiceUnavailableError"},
            "a: ServiceUnavailableError",
        ),
        ({"status": "error", "error": "APIConnectionError"}, "a: APIConnectionError"),
    ],
)
def test_round_trip_warnings(result, detail):
    check = preflight._round_trip_result([{"id": "a", **result}])
    assert check.status == "warn"
    assert check.detail == detail


def test_round_trip_probe_output_parsed(monkeypatch):
    from grid.inputs import Model

    async def fake_run_cmd(*args, cwd=None, env=None, own_group=False):
        assert args[-2] == preflight._ROUND_TRIP
        return 0, 'log noise\nROUND_TRIP [{"id": "m", "status": "ok"}]\n'

    monkeypatch.setattr(preflight, "run_cmd", fake_run_cmd)
    grid = type("G", (), {"models": [Model("m", "openrouter/x/y", 1000)]})()
    assert asyncio.run(preflight._round_trip_check(grid)).status == "pass"


def test_round_trip_probe_crash_warns(monkeypatch):
    from grid.inputs import Model

    async def fake_run_cmd(*args, cwd=None, env=None, own_group=False):
        return 1, "Traceback...\nImportError: boom"

    monkeypatch.setattr(preflight, "run_cmd", fake_run_cmd)
    grid = type("G", (), {"models": [Model("m", "openrouter/x/y", 1000)]})()
    check = asyncio.run(preflight._round_trip_check(grid))
    assert check.status == "warn"
    assert "ImportError: boom" in check.detail


@pytest.mark.parametrize(
    "error", ["NotFoundError", "AuthenticationError", "BadRequestError"]
)
def test_round_trip_lasting_errors_fail(error):
    check = preflight._round_trip_result(
        [{"id": "a", "status": "error", "error": error}]
    )
    assert check.status == "fail"


def test_cancelled_command_kills_its_children(tmp_path):
    from grid.envctl import run_cmd

    pid_file = tmp_path / "pid"
    script = f"sleep 30 & echo $! > {pid_file}; wait"

    async def go():
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(run_cmd("sh", "-c", script, own_group=True), 1)

    asyncio.run(go())
    child = int(pid_file.read_text())
    try:
        assert not _running(child)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(child, signal.SIGKILL)


def _running(pid: int) -> bool:
    """Alive and not a zombie, waiting briefly for the kill to land."""
    for _ in range(20):
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
        ).stdout.strip()
        if not state or state.startswith("Z"):
            return False
        time.sleep(0.1)
    return True


def test_dashboard_runs_one_preflight_at_a_time(monkeypatch):
    from grid import server

    calls = 0

    async def fake_run_preflight(grid):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return [preflight.Check("x", "x", "pass")]

    monkeypatch.setattr(server, "run_preflight", fake_run_preflight)
    shared = server.SharedPreflight(grid=None)

    async def go():
        first = await asyncio.gather(
            shared.get(), shared.get(), shared.get(refresh=True)
        )
        assert calls == 1 and all(r["ok"] for r in first)
        await shared.get()  # cached
        assert calls == 1
        await shared.get(refresh=True)
        assert calls == 2

    asyncio.run(go())
