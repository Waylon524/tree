"""Tests for role-specific LLM request options."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tree.config import RoleConfig, Settings
from tree.model import client as model_client
from tree.model.budget import PromptBudgetExceededError
from tree.model.operations import OPERATION_SPECS, resolve_operation_spec
from tree.observability.operation_log import recent_operation_events
from tree.observability.retry import (
    LLMContentFilteredError,
    LLMOutputTruncatedError,
    LLMRefusalError,
    LLMToolCallError,
    MalformedLLMResponseError,
)


class _FakeCompletions:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content="ok")
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])


class _FakeAsyncOpenAI:
    instances = []

    def __init__(self, **_kwargs):
        self.chat = SimpleNamespace(completions=_FakeCompletions())
        _FakeAsyncOpenAI.instances.append(self)

    async def close(self):
        pass


def _settings(project_root: Path | None = None) -> Settings:
    role = RoleConfig(api_key="k", base_url="https://api.deepseek.com", model="deepseek-v4-flash")
    return Settings(
        examiner=role,
        student=role,
        writer=role,
        archivist=role,
        dagger=role,
        max_retries=0,
        project_root=project_root or Path("/tmp/tree-model-client-tests"),
    )


def test_settings_default_archivist_repair_attempts_absorbs_json_mode_retries():
    assert Settings.from_env(project_root=Path.cwd(), require_llm=False).archivist_mtu_repair_attempts == 8


def test_settings_default_dagger_build_timeout_is_480_seconds(monkeypatch, tmp_path):
    monkeypatch.delenv("DAGGER_BUILD_TIMEOUT_SEC", raising=False)
    assert Settings.from_env(project_root=tmp_path, require_llm=False).dagger_build_timeout_sec == 480.0


def test_settings_default_llm_timeout_is_480_seconds(monkeypatch, tmp_path):
    monkeypatch.delenv("LLM_TIMEOUT_SEC", raising=False)
    assert Settings.from_env(project_root=tmp_path, require_llm=False).llm_timeout_sec == 480.0


def test_settings_default_source_mtu_chunk_tokens_is_20000(monkeypatch, tmp_path):
    monkeypatch.setenv("TREE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SOURCE_MTU_CHUNK_TOKENS", raising=False)
    assert Settings.from_env(project_root=tmp_path, require_llm=False).source_mtu_chunk_tokens == 20_000


def test_settings_reads_source_mtu_chunk_tokens(monkeypatch, tmp_path):
    monkeypatch.setenv("TREE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SOURCE_MTU_CHUNK_TOKENS", "12345")
    assert Settings.from_env(project_root=tmp_path, require_llm=False).source_mtu_chunk_tokens == 12_345


def test_settings_default_model_is_deepseek_v4_flash(monkeypatch, tmp_path):
    monkeypatch.setenv("TREE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("LLM_MODEL", raising=False)
    for role in model_client.ROLES:
        monkeypatch.delenv(f"{role.upper()}_MODEL", raising=False)

    settings = Settings.from_env(project_root=tmp_path, require_llm=False)

    for role in model_client.ROLES:
        assert settings.role(role).model == "deepseek-v4-flash"


def test_settings_default_dagger_repair_attempts_is_three(monkeypatch, tmp_path):
    monkeypatch.delenv("DAGGER_REPAIR_ATTEMPTS", raising=False)
    assert Settings.from_env(project_root=tmp_path, require_llm=False).dagger_repair_attempts == 3


def test_settings_defaults_use_conservative_nested_concurrency(monkeypatch, tmp_path):
    monkeypatch.delenv("LLM_PROVIDER_CONCURRENCY", raising=False)
    monkeypatch.delenv("SOURCE_INGEST_CONCURRENCY", raising=False)
    monkeypatch.delenv("ARCHIVIST_CHUNK_CONCURRENCY", raising=False)
    monkeypatch.delenv("DAGGER_PREREQUISITE_CONCURRENCY", raising=False)
    monkeypatch.delenv("MAX_ACTIVE_NODE_RUNS", raising=False)
    settings = Settings.from_env(project_root=tmp_path, require_llm=False)
    assert settings.llm_provider_concurrency == 4
    assert settings.source_ingest_concurrency == 4
    assert settings.archivist_chunk_concurrency == 2
    assert settings.dagger_prerequisite_concurrency == 3
    assert settings.max_active_node_runs == 3


def test_settings_reads_dagger_prerequisite_concurrency(monkeypatch, tmp_path):
    monkeypatch.setenv("DAGGER_PREREQUISITE_CONCURRENCY", "3")
    assert Settings.from_env(project_root=tmp_path, require_llm=False).dagger_prerequisite_concurrency == 3


def test_settings_supports_global_and_role_provider_budgets(monkeypatch, tmp_path):
    monkeypatch.setenv("TREE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LLM_PROVIDER_PROFILE", "generic")
    monkeypatch.setenv("LLM_CONTEXT_WINDOW", "64000")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "4096")
    monkeypatch.setenv("DAGGER_PROVIDER_PROFILE", "deepseek")
    monkeypatch.setenv("DAGGER_CONTEXT_WINDOW", "128000")
    monkeypatch.setenv("DAGGER_MAX_OUTPUT_TOKENS", "8192")

    settings = Settings.from_env(project_root=tmp_path, require_llm=False)

    assert settings.writer.provider_profile == "generic"
    assert settings.writer.context_window == 64_000
    assert settings.writer.max_output_tokens == 4_096
    assert settings.dagger.provider_profile == "deepseek"
    assert settings.dagger.context_window == 128_000
    assert settings.dagger.max_output_tokens == 8_192


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
    assert calls["archivist"]["max_tokens"] == 131072
    assert "reasoning_effort" not in calls["archivist"]

    assert calls["dagger"]["response_format"] == {"type": "json_object"}
    assert calls["dagger"]["extra_body"] == {"thinking": {"type": "enabled"}}
    assert calls["dagger"]["reasoning_effort"] == "high"
    assert calls["dagger"]["max_tokens"] == 131072

    assert "response_format" not in calls["examiner"]
    assert calls["examiner"]["extra_body"] == {"thinking": {"type": "enabled"}}

    assert "response_format" not in calls["writer"]
    assert calls["writer"]["extra_body"] == {"thinking": {"type": "enabled"}}

    assert "response_format" not in calls["student"]
    assert calls["student"]["extra_body"] == {"thinking": {"type": "disabled"}}


async def test_unknown_compatible_provider_gets_only_standard_options(monkeypatch):
    _FakeAsyncOpenAI.instances = []
    monkeypatch.setattr(model_client, "AsyncOpenAI", _FakeAsyncOpenAI)
    generic = RoleConfig(
        api_key="k",
        base_url="https://compatible.example/v1",
        model="custom-model",
        provider_profile="auto",
    )
    settings = Settings(
        examiner=generic,
        student=generic,
        writer=generic,
        archivist=generic,
        dagger=generic,
        max_retries=0,
    )
    client = model_client.LLMClient(settings)

    await client.call("dagger", "system", "user")

    dagger_index = list(model_client.ROLES).index("dagger")
    call = _FakeAsyncOpenAI.instances[dagger_index].chat.completions.calls[0]
    assert call["max_tokens"] == 131072
    assert "extra_body" not in call
    assert "reasoning_effort" not in call
    assert "response_format" not in call


async def test_unsupported_option_is_disabled_once_and_cached(monkeypatch):
    class UnsupportedOptionError(Exception):
        status_code = 400

    class FallbackCompletions(_FakeCompletions):
        async def create(self, **kwargs):
            self.calls.append(kwargs)
            if "response_format" in kwargs:
                raise UnsupportedOptionError("response_format is not supported")
            message = SimpleNamespace(content="ok")
            return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])

    class FallbackClient(_FakeAsyncOpenAI):
        instances = []

        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FallbackCompletions())
            FallbackClient.instances.append(self)

    monkeypatch.setattr(model_client, "AsyncOpenAI", FallbackClient)
    client = model_client.LLMClient(_settings())

    await client.call("archivist", "system", "first")
    await client.call("archivist", "system", "second")

    archivist_index = list(model_client.ROLES).index("archivist")
    calls = FallbackClient.instances[archivist_index].chat.completions.calls
    assert len(calls) == 3
    assert "response_format" in calls[0]
    assert all("response_format" not in call for call in calls[1:])


async def test_prompt_budget_fails_before_provider_call(monkeypatch):
    _FakeAsyncOpenAI.instances = []
    monkeypatch.setattr(model_client, "AsyncOpenAI", _FakeAsyncOpenAI)
    constrained = RoleConfig(
        api_key="k",
        base_url="https://compatible.example/v1",
        model="small-context",
        provider_profile="generic",
        context_window=1_024,
        max_output_tokens=128,
    )
    settings = Settings(
        examiner=constrained,
        student=constrained,
        writer=constrained,
        archivist=constrained,
        dagger=constrained,
        max_retries=0,
        llm_prompt_safety_tokens=128,
    )
    client = model_client.LLMClient(settings)

    with pytest.raises(PromptBudgetExceededError, match="split coverage input"):
        await client.call("dagger", "system", "x" * 5_000)

    dagger_index = list(model_client.ROLES).index("dagger")
    assert _FakeAsyncOpenAI.instances[dagger_index].chat.completions.calls == []


def test_capability_downgrade_does_not_match_auth_or_generic_bad_requests():
    options = {"response_format": {"type": "json_object"}}
    auth_error = RuntimeError("response_format unsupported for this account")
    auth_error.status_code = 401
    ordinary_bad_request = RuntimeError("invalid request body")
    ordinary_bad_request.status_code = 400

    assert model_client._unsupported_option(auth_error, options) is None
    assert model_client._unsupported_option(ordinary_bad_request, options) is None


async def test_llm_client_passes_per_call_timeout_to_openai(monkeypatch):
    _FakeAsyncOpenAI.instances = []
    monkeypatch.setattr(model_client, "AsyncOpenAI", _FakeAsyncOpenAI)
    client = model_client.LLMClient(_settings())

    await client.call("dagger", "system", "user", timeout_sec=480.0)

    dagger_index = list(model_client.ROLES).index("dagger")
    call = _FakeAsyncOpenAI.instances[dagger_index].chat.completions.calls[0]
    assert call["timeout"] == 480.0


async def test_operation_spec_clamps_short_repair_budget_and_timeout(monkeypatch):
    _FakeAsyncOpenAI.instances = []
    monkeypatch.setattr(model_client, "AsyncOpenAI", _FakeAsyncOpenAI)
    client = model_client.LLMClient(_settings())

    await client.call(
        "archivist",
        "system",
        "user",
        operation="archivist.mtu_metadata_repair",
        timeout_sec=480.0,
    )

    archivist_index = list(model_client.ROLES).index("archivist")
    call = _FakeAsyncOpenAI.instances[archivist_index].chat.completions.calls[0]
    assert call["max_tokens"] == 8_192
    assert call["timeout"] == 180.0
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    assert call["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in call


async def test_operation_specs_negotiate_deepseek_openai_and_generic(monkeypatch):
    _FakeAsyncOpenAI.instances = []
    monkeypatch.setattr(model_client, "AsyncOpenAI", _FakeAsyncOpenAI)
    deepseek = RoleConfig(
        api_key="deepseek",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
    )
    openai = RoleConfig(
        api_key="openai",
        base_url="https://api.openai.com/v1",
        model="gpt-test",
    )
    generic = RoleConfig(
        api_key="generic",
        base_url="https://compatible.example/v1",
        model="custom-model",
    )
    settings = Settings(
        examiner=openai,
        student=generic,
        writer=openai,
        archivist=generic,
        dagger=deepseek,
        max_retries=0,
    )
    client = model_client.LLMClient(settings)

    await client.call(
        "dagger", "system", "user", operation="dagger.select_prerequisites"
    )
    await client.call(
        "examiner", "system", "user", operation="examiner.reconcile"
    )
    await client.call(
        "archivist", "system", "user", operation="archivist.mtu_metadata_repair"
    )

    calls = {
        role: fake.chat.completions.calls[0]
        for role, fake in zip(model_client.ROLES, _FakeAsyncOpenAI.instances)
        if fake.chat.completions.calls
    }
    assert calls["dagger"]["max_tokens"] == 32_768
    assert calls["dagger"]["extra_body"] == {"thinking": {"type": "enabled"}}
    assert calls["dagger"]["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in calls["dagger"]

    assert calls["examiner"]["max_completion_tokens"] == 131_072
    assert "max_tokens" not in calls["examiner"]
    assert "extra_body" not in calls["examiner"]
    assert "reasoning_effort" not in calls["examiner"]

    assert calls["archivist"]["max_tokens"] == 8_192
    assert "response_format" not in calls["archivist"]
    assert "extra_body" not in calls["archivist"]


async def test_operation_telemetry_records_usage_without_prompt_text(monkeypatch, caplog, tmp_path):
    class UsageCompletions(_FakeCompletions):
        async def create(self, **kwargs):
            self.calls.append(kwargs)
            message = SimpleNamespace(content="ok")
            usage = SimpleNamespace(prompt_tokens=123, completion_tokens=45)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message, finish_reason="stop")],
                usage=usage,
            )

    class UsageClient(_FakeAsyncOpenAI):
        instances = []

        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=UsageCompletions())
            UsageClient.instances.append(self)

    monkeypatch.setattr(model_client, "AsyncOpenAI", UsageClient)
    client = model_client.LLMClient(_settings(tmp_path))
    caplog.set_level("INFO", logger="tree.model.client")

    await client.call(
        "dagger",
        "SECRET_SYSTEM_PROMPT",
        "SECRET_USER_PROMPT",
        operation="dagger.select_prerequisites",
    )

    log = caplog.text
    assert "operation=dagger.select_prerequisites" in log
    assert "provider=deepseek" in log
    assert "requested_output=32768" in log
    assert "usage_input=123" in log
    assert "usage_output=45" in log
    assert "finish_reason=stop" in log
    assert "retries=0" in log
    assert "SECRET_SYSTEM_PROMPT" not in log
    assert "SECRET_USER_PROMPT" not in log

    records = recent_operation_events(tmp_path)
    assert records[-1] == {
        "timestamp": records[-1]["timestamp"],
        "event": "complete",
        "operation": "dagger.select_prerequisites",
        "role": "dagger",
        "effective_role": "dagger",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "estimated_input_tokens": records[-1]["estimated_input_tokens"],
        "requested_output_tokens": 32768,
        "usage_input_tokens": 123,
        "usage_output_tokens": 45,
        "finish_reason": "stop",
        "retries": 0,
        "latency_ms": records[-1]["latency_ms"],
        "degraded": False,
    }
    serialized = str(records)
    assert "SECRET_SYSTEM_PROMPT" not in serialized
    assert "SECRET_USER_PROMPT" not in serialized
    assert "Authorization" not in serialized
    assert "api_key" not in serialized


async def test_operation_telemetry_records_output_truncation(monkeypatch, tmp_path):
    class TruncatedCompletions(_FakeCompletions):
        async def create(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="partial", refusal=None, tool_calls=None),
                        finish_reason="length",
                    )
                ]
            )

    class TruncatedClient(_FakeAsyncOpenAI):
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=TruncatedCompletions())

    monkeypatch.setattr(model_client, "AsyncOpenAI", TruncatedClient)
    client = model_client.LLMClient(_settings(tmp_path))

    with pytest.raises(LLMOutputTruncatedError):
        await client.call("archivist", "system", "user", operation="archivist.clean")

    failed = recent_operation_events(tmp_path)[-1]
    assert failed["event"] == "failed"
    assert failed["operation"] == "archivist.clean"
    assert failed["finish_reason"] == "length"
    assert failed["error_type"] == "LLMOutputTruncatedError"


def test_operation_registry_covers_all_agent_call_classes():
    assert set(OPERATION_SPECS) == {
        "archivist.clean",
        "archivist.clean_range_repair",
        "archivist.mtu_segment",
        "archivist.mtu_assignment",
        "archivist.mtu_metadata_repair",
        "archivist.mtu_units_repair",
        "archivist.mtu_duplicate_define_repair",
        "dagger.build_nodes",
        "dagger.select_prerequisites",
        "dagger.repair_defines",
        "dagger.repair_prerequisites",
        "examiner.compose",
        "examiner.audit",
        "examiner.reconcile",
        "examiner.compose_format_repair",
        "examiner.audit_format_repair",
        "examiner.reconcile_format_repair",
        "student.answer",
        "writer.create",
        "writer.fast_create",
        "writer.optimize",
        "writer.feedback_revision",
    }


def test_operation_registry_rejects_unknown_or_cross_role_operation():
    with pytest.raises(ValueError, match="Unknown LLM operation"):
        resolve_operation_spec("writer", "writer.missing")
    with pytest.raises(ValueError, match="belongs to role dagger"):
        resolve_operation_spec("writer", "dagger.build_nodes")


def test_llm_client_shares_limiter_for_roles_on_same_provider(monkeypatch):
    _FakeAsyncOpenAI.instances = []
    monkeypatch.setattr(model_client, "AsyncOpenAI", _FakeAsyncOpenAI)
    client = model_client.LLMClient(_settings())

    limiters = {id(client._limiters[client._provider_keys[role]]) for role in model_client.ROLES}
    assert len(limiters) == 1
    assert next(iter(client._limiters.values())).limit == 4


@pytest.mark.parametrize(
    ("choice", "error_type", "error"),
    [
        (
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="", refusal=None, tool_calls=None),
            ),
            MalformedLLMResponseError,
            "content is empty",
        ),
        (
            SimpleNamespace(
                finish_reason="length",
                message=SimpleNamespace(content="partial", refusal=None, tool_calls=None),
            ),
            LLMOutputTruncatedError,
            "finish_reason=length",
        ),
        (
            SimpleNamespace(
                finish_reason="content_filter",
                message=SimpleNamespace(content="partial", refusal=None, tool_calls=None),
            ),
            LLMContentFilteredError,
            "finish_reason=content_filter",
        ),
        (
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="", refusal="policy", tool_calls=None),
            ),
            LLMRefusalError,
            "refused",
        ),
        (
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(content="", refusal=None, tool_calls=[object()]),
            ),
            LLMToolCallError,
            "unsupported tools",
        ),
    ],
)
def test_extract_chat_content_rejects_incomplete_responses(choice, error_type, error):
    with pytest.raises(error_type, match=error):
        model_client._extract_chat_content(SimpleNamespace(choices=[choice]))


def test_extract_chat_content_rejects_missing_choices():
    with pytest.raises(MalformedLLMResponseError, match="missing choices"):
        model_client._extract_chat_content(SimpleNamespace(choices=[]))
