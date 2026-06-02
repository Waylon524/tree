"""Tests for role-specific LLM request options."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tree.config import RoleConfig, Settings
from tree.model import client as model_client


class _FakeCompletions:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content="ok")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _FakeAsyncOpenAI:
    instances = []

    def __init__(self, **_kwargs):
        self.chat = SimpleNamespace(completions=_FakeCompletions())
        _FakeAsyncOpenAI.instances.append(self)

    async def close(self):
        pass


def _settings() -> Settings:
    role = RoleConfig(api_key="k", base_url="https://api.deepseek.com", model="deepseek-v4-flash")
    return Settings(
        examiner=role,
        student=role,
        writer=role,
        archivist=role,
        dagger=role,
        max_retries=0,
    )


def test_settings_default_archivist_repair_attempts_absorbs_json_mode_retries():
    assert Settings.from_env(project_root=Path.cwd(), require_llm=False).archivist_mtu_repair_attempts == 8


def test_settings_default_dagger_build_timeout_is_480_seconds(monkeypatch, tmp_path):
    monkeypatch.delenv("DAGGER_BUILD_TIMEOUT_SEC", raising=False)
    assert Settings.from_env(project_root=tmp_path, require_llm=False).dagger_build_timeout_sec == 480.0


def test_settings_default_llm_timeout_is_480_seconds(monkeypatch, tmp_path):
    monkeypatch.delenv("LLM_TIMEOUT_SEC", raising=False)
    assert Settings.from_env(project_root=tmp_path, require_llm=False).llm_timeout_sec == 480.0


def test_settings_default_dagger_repair_attempts_is_three(monkeypatch, tmp_path):
    monkeypatch.delenv("DAGGER_REPAIR_ATTEMPTS", raising=False)
    assert Settings.from_env(project_root=tmp_path, require_llm=False).dagger_repair_attempts == 3


async def test_llm_client_sets_role_specific_deepseek_options(monkeypatch):
    _FakeAsyncOpenAI.instances = []
    monkeypatch.setattr(model_client, "AsyncOpenAI", _FakeAsyncOpenAI)
    client = model_client.LLMClient(_settings())

    for role in ("archivist", "dagger", "examiner", "writer", "student"):
        await client.call(role, "system", "user")

    calls = {
        role: fake.chat.completions.calls[0]
        for role, fake in zip(model_client.ROLES, _FakeAsyncOpenAI.instances)
    }

    assert calls["archivist"]["response_format"] == {"type": "json_object"}
    assert calls["archivist"]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in calls["archivist"]

    assert calls["dagger"]["response_format"] == {"type": "json_object"}
    assert calls["dagger"]["extra_body"] == {"thinking": {"type": "enabled"}}
    assert calls["dagger"]["reasoning_effort"] == "high"

    assert "response_format" not in calls["examiner"]
    assert calls["examiner"]["extra_body"] == {"thinking": {"type": "enabled"}}

    assert "response_format" not in calls["writer"]
    assert calls["writer"]["extra_body"] == {"thinking": {"type": "enabled"}}

    assert "response_format" not in calls["student"]
    assert calls["student"]["extra_body"] == {"thinking": {"type": "disabled"}}


async def test_llm_client_passes_per_call_timeout_to_openai(monkeypatch):
    _FakeAsyncOpenAI.instances = []
    monkeypatch.setattr(model_client, "AsyncOpenAI", _FakeAsyncOpenAI)
    client = model_client.LLMClient(_settings())

    await client.call("dagger", "system", "user", timeout_sec=480.0)

    dagger_index = list(model_client.ROLES).index("dagger")
    call = _FakeAsyncOpenAI.instances[dagger_index].chat.completions.calls[0]
    assert call["timeout"] == 480.0
