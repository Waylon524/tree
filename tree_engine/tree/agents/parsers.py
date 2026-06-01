"""Parsers for agent outputs (exam sections, audit route, strict JSON).

TODO:
  - parse_exam_sections(raw) -> ExamSections   (## [Section] blocks)
  - parse_audit(raw) -> AuditResult            (ROUTE: PASS|FAIL_KNOWLEDGE_GAP)
  - extract_json_object(raw) -> dict           (tolerant strict-JSON extraction
                                                for archivist MTU & dagger output)
"""

from __future__ import annotations

import json
import re
from typing import Any


def extract_json_object(raw: str) -> dict[str, Any]:
    """Best-effort extraction of the first top-level JSON object in `raw`."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in response")
    return json.loads(text[start : end + 1])
