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
