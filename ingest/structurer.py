"""Archivist: calls LLM to structure raw OCR text into clean Markdown.

Uses the ARCHIVIST role config (ARCHIVIST_API_KEY/BASE_URL/MODEL)
with fallback to LLM_API_KEY/LLM_BASE_URL/LLM_MODEL defaults.
"""

import logging
import os
from pathlib import Path

from openai import OpenAI

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = (Path(__file__).parent / "prompts" / "structurer.txt").read_text()


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

    prompt = PROMPT_TEMPLATE.replace("{raw_text}", raw_text)

    client = OpenAI(api_key=api_key, base_url=base_url)

    logger.info("Calling archivist LLM: model=%s, input_len=%d", model, len(raw_text))
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是文档结构化专家，输出纯Markdown。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
    )

    result = resp.choices[0].message.content
    logger.info("Archivist output length: %d", len(result))
    return result
