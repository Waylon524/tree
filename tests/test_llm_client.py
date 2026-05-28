from types import SimpleNamespace

from tree.deepseek.client import MalformedLLMResponseError, _extract_chat_content
from tree.observability.retry import is_retryable


def test_missing_choices_response_is_retryable_malformed_llm_response():
    response = SimpleNamespace(choices=None)

    try:
        _extract_chat_content(response)
    except MalformedLLMResponseError as exc:
        assert "missing choices" in str(exc)
        assert is_retryable(exc)
    else:
        raise AssertionError("expected malformed LLM response")


def test_extract_chat_content_accepts_empty_message_content():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=None),
            )
        ],
    )

    assert _extract_chat_content(response) == ""
