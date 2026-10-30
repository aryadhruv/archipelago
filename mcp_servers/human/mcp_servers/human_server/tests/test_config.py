import pytest
from utils.config import Config, load_config


def test_load_config_reads_the_environment(monkeypatch):
    monkeypatch.setenv("HUMAN_MODEL", "test/model")
    monkeypatch.setenv("HUMAN_EXTRA_ARGS", '{"temperature": 0}')
    monkeypatch.setenv("HUMAN_MAX_QUESTIONS", "3")
    config = load_config()
    assert config.extra_args == {"temperature": 0}
    assert config.max_questions == 3


@pytest.mark.parametrize(
    "env, match",
    [
        ({}, "HUMAN_MODEL"),
        ({"HUMAN_EXTRA_ARGS": "{nope"}, "valid JSON"),
        ({"HUMAN_EXTRA_ARGS": '"fast"'}, "JSON object"),
        ({"HUMAN_EXTRA_ARGS": '{"model": "x", "tools": []}'}, "model, tools"),
        ({"HUMAN_MAX_QUESTIONS": "abc"}, "whole number"),
        ({"HUMAN_MAX_QUESTIONS": "-1"}, "whole number"),
    ],
)
def test_load_config_rejects_bad_settings(monkeypatch, env, match):
    monkeypatch.delenv("HUMAN_MODEL", raising=False)
    if "HUMAN_MODEL" not in str(match):
        monkeypatch.setenv("HUMAN_MODEL", "test/model")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=match):
        load_config()


@pytest.mark.parametrize("cap", [True, 2.5, -1, "3"])
def test_config_rejects_a_bad_cap(cap):
    with pytest.raises(ValueError, match="HUMAN_MAX_QUESTIONS"):
        Config(model="test/model", max_questions=cap)
