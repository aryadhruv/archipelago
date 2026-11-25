import asyncio

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
    ],
)
def test_round_trip_warnings(result, detail):
    check = preflight._round_trip_result([{"id": "a", **result}])
    assert check.status == "warn"
    assert check.detail == detail


def test_round_trip_probe_output_parsed(monkeypatch):
    from grid.inputs import Model

    async def fake_run_cmd(*args, cwd=None, env=None):
        assert args[-2] == preflight._ROUND_TRIP
        return 0, 'log noise\nROUND_TRIP [{"id": "m", "status": "ok"}]\n'

    monkeypatch.setattr(preflight, "run_cmd", fake_run_cmd)
    grid = type("G", (), {"models": [Model("m", "openrouter/x/y", 1000)]})()
    assert asyncio.run(preflight._round_trip_check(grid)).status == "pass"


def test_round_trip_probe_crash_warns(monkeypatch):
    from grid.inputs import Model

    async def fake_run_cmd(*args, cwd=None, env=None):
        return 1, "Traceback...\nImportError: boom"

    monkeypatch.setattr(preflight, "run_cmd", fake_run_cmd)
    grid = type("G", (), {"models": [Model("m", "openrouter/x/y", 1000)]})()
    check = asyncio.run(preflight._round_trip_check(grid))
    assert check.status == "warn"
    assert "ImportError: boom" in check.detail
