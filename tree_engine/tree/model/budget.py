"""Conservative, provider-independent prompt budget checks."""

from __future__ import annotations

import math


class PromptBudgetExceededError(RuntimeError):
    """Raised before a provider call when a prompt cannot fit its declared budget."""

    def __init__(
        self,
        *,
        role: str,
        estimated_input_tokens: int,
        input_budget_tokens: int,
        context_window: int,
        reserved_output_tokens: int,
        safety_tokens: int,
    ) -> None:
        self.role = role
        self.estimated_input_tokens = estimated_input_tokens
        self.input_budget_tokens = input_budget_tokens
        self.context_window = context_window
        self.reserved_output_tokens = reserved_output_tokens
        self.safety_tokens = safety_tokens
        super().__init__(
            f"Prompt for role {role} is estimated at {estimated_input_tokens} tokens, "
            f"exceeding its {input_budget_tokens}-token input budget "
            f"(context_window={context_window}, reserved_output={reserved_output_tokens}, "
            f"safety={safety_tokens}). Reduce RAG/history, split coverage input, or increase "
            f"{role.upper()}_CONTEXT_WINDOW after verifying the provider limit."
        )


def estimate_text_tokens(text: str) -> int:
    """Estimate tokens conservatively across English and CJK without a model tokenizer."""
    if not text:
        return 0
    # UTF-8 bytes / 3 is deliberately conservative for typical prose: it is close
    # to one token per CJK character and overestimates most English text.
    return max(1, math.ceil(len(text.encode("utf-8")) / 3))


def estimate_chat_tokens(system_prompt: str, user_prompt: str) -> int:
    """Include a small fixed allowance for Chat Completions message framing."""
    return 16 + estimate_text_tokens(system_prompt) + estimate_text_tokens(user_prompt)
