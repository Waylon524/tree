from tree.config import RoleConfig, Settings
from tree.model.client import LLMClient


def _role() -> RoleConfig:
    return RoleConfig(api_key="test-key", base_url="https://example.test/v1", model="test-model")


def test_llm_client_sets_explicit_timeout_and_disables_sdk_retries(monkeypatch) -> None:
    created = []

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            created.append(kwargs)

        async def close(self):
            pass

    monkeypatch.setattr("tree.model.client.AsyncOpenAI", FakeAsyncOpenAI)
    settings = Settings(
        examiner=_role(),
        student=_role(),
        writer=_role(),
        archivist=_role(),
        llm_timeout_sec=12.5,
    )

    LLMClient(settings)

    assert created
    assert all(item["timeout"] == 12.5 for item in created)
    assert all(item["max_retries"] == 0 for item in created)


def test_settings_reads_llm_timeout_from_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LLM_TIMEOUT_SEC", "7.5")
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    settings = Settings.from_env(tmp_path)

    assert settings.llm_timeout_sec == 7.5
