"""Archivist: calls LLM to structure raw OCR text into clean Markdown.

Uses the ARCHIVIST role config (ARCHIVIST_API_KEY/BASE_URL/MODEL)
with fallback to LLM_API_KEY/LLM_BASE_URL/LLM_MODEL defaults.
"""

import logging
import os

from openai import OpenAI

from tree.agents.prompts import ARCHIVIST_PROMPT

logger = logging.getLogger(__name__)


def structure(raw_text: str, model: str | None = None) -> str:
    """Structure raw OCR text into clean Markdown via archivist LLM.

    Reads ARCHIVIST_API_KEY/BASE_URL/MODEL with fallback to
    LLM_API_KEY/LLM_BASE_URL/LLM_MODEL.
    """
    api_key = os.environ.get("ARCHIVIST_API_KEY") or os.environ.get("LLM_API_KEY")
    if not api_key:
        logger.warning("No ARCHIVIST_API_KEY or LLM_API_KEY set, returning raw text unchanged")
        return raw_text

    base_url = (
        os.environ.get("ARCHIVIST_BASE_URL")
        or os.environ.get("LLM_BASE_URL")
        or "https://api.openai.com/v1"
    )
    model = (
        model
        or os.environ.get("ARCHIVIST_MODEL")
        or os.environ.get("LLM_MODEL")
        or "gpt-4o"
    )

    client = OpenAI(api_key=api_key, base_url=base_url)

    logger.info("Calling archivist LLM: model=%s, input_len=%d", model, len(raw_text))
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": ARCHIVIST_PROMPT},
            {"role": "user", "content": f"## Raw OCR Text\n{raw_text}"},
        ],
        temperature=0.1,
    )

    result = resp.choices[0].message.content
    logger.info("Archivist output length: %d", len(result))
    return result
