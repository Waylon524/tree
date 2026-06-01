"""Semantic Markdown chunker (simplified for the new architecture).

Chunks by heading boundaries + semantic blocks, preserving definition/proof/
example integrity. The old cut-plan machinery is gone: source MTUs are chunked
via `chunk_mtu` (one MTU is normally one chunk; oversized MTUs are split), while
finished outputs / drafts use generic `chunk_markdown`.

See docs/REBUILD-DESIGN.md §4 ⑤ / §5.2.
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from typing import Any

MAX_TOKENS = {"def": 2000, "proof": 3000, "example": 2400, "narrative": 1500}
TOKEN_SAFETY_MARGIN = 8
MAX_TOKENS_PER_CHUNK = 3000


# Rough token estimate: 1 token ≈ 1.5 Chinese chars or 4 English chars
def _estimate_tokens(text: str) -> int:
    cn = sum(1 for c in text if "一" <= c <= "鿿")
    en = len(text) - cn
    return int(cn / 1.5 + en / 4)


def _detect_chunk_type(text: str) -> str:
    lower = text[:200].lower()
    if any(kw in lower for kw in ["定义", "定理", "定律", "公理", "definition", "theorem"]):
        return "def"
    if any(kw in lower for kw in ["推导", "证明", "证", "derivation", "proof"]):
        return "proof"
    if any(kw in lower for kw in ["例题", "例", "example", "解"]):
        return "example"
    return "narrative"


def _extract_concepts(text: str) -> list[str]:
    """Weak concept hints (bold markers) for cheap similarity pre-screening."""
    concepts = []
    for m in re.finditer(r"\*\*([^*]+)\*\*", text):
        name = m.group(1).strip()
        if len(name) <= 20 and not name.startswith("["):
            concepts.append(name)
    return concepts[:10]


def _extract_formulas(text: str) -> list[str]:
    formulas = []
    for m in re.finditer(r"\\\[(.+?)\\\]", text, re.DOTALL):
        formulas.append(m.group(1).strip())
    for m in re.finditer(r"\\\((.+?)\\\)", text, re.DOTALL):
        f = m.group(1).strip()
        if len(f) > 3:
            formulas.append(f)
    for m in re.finditer(r"\$\$(.+?)\$\$", text, re.DOTALL):
        formulas.append(m.group(1).strip())
    for m in re.finditer(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", text):
        f = m.group(1).strip()
        if len(f) > 3:
            formulas.append(f)
    return formulas[:10]


def _formula_signatures(formulas: list[str]) -> list[str]:
    signatures = []
    for formula in formulas:
        normalized = formula.strip()
        normalized = re.sub(r"\\(?:mathrm|operatorname|text)\{([^{}]+)\}", r"\1", normalized)
        normalized = re.sub(r"\\(?:left|right)\b", "", normalized)
        normalized = re.sub(r"\\(?:,|;|:|!|quad|qquad)", "", normalized)
        normalized = re.sub(r"\s+", "", normalized)
        normalized = normalized.replace("{", "").replace("}", "").replace("\\", "").lower()
        if len(normalized) < 3:
            continue
        signatures.append(normalized[:160])
    return _unique(signatures)[:10]


def _heading_path(section: str, section_id: str) -> list[str]:
    headings = [
        match.group(1).strip()
        for match in re.finditer(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", section, flags=re.MULTILINE)
        if match.group(1).strip()
    ]
    if not headings and section_id:
        headings = [section_id]
    return _unique(_clean_heading(item) for item in headings if _clean_heading(item))[:6]


def chunk_markdown(
    file_seq: str,
    text: str,
    *,
    source_collection: str = "",
    is_draft: bool = False,
) -> list[dict]:
    """Chunk a Markdown file into semantic pieces by heading + token budget."""
    text = _prepare_markdown_text(text)
    sections = re.split(r"(?=^##\s)", text, flags=re.MULTILINE)

    chunks: list[dict] = []
    chunk_idx = 0
    for section in sections:
        section = section.strip()
        if not section:
            continue

        heading_match = re.match(r"^##\s+(.+)", section)
        section_id = heading_match.group(1).strip() if heading_match else "intro"
        section_id = re.sub(r"[^\w一-鿿-]", "-", section_id)[:50]
        heading_path = _heading_path(section, section_id)

        for part in re.split(r"(?=> \[!details\])", section):
            part = part.strip()
            if not part or _is_noise_chunk(part):
                continue

            chunk_type = "proof" if part.startswith("> [!details") else _detect_chunk_type(part)
            max_tok = MAX_TOKENS[chunk_type]
            tok_est = _estimate_tokens(part)

            if tok_est <= max_tok:
                chunks.append(
                    _make_chunk(file_seq, source_collection, section_id, heading_path,
                                chunk_type, part, chunk_idx, is_draft, tok_est)
                )
                chunk_idx += 1
                continue

            buffer, buffer_tokens = "", 0
            for para in re.split(r"\n\n+", part):
                para = para.strip()
                if not para:
                    continue
                for segment in _split_long_paragraph(para, max_tok):
                    segment_tokens = _estimate_tokens(segment)
                    if buffer_tokens + segment_tokens > max_tok and buffer:
                        chunks.append(
                            _make_chunk(file_seq, source_collection, section_id, heading_path,
                                        chunk_type, buffer, chunk_idx, is_draft, buffer_tokens)
                        )
                        chunk_idx += 1
                        buffer, buffer_tokens = segment, segment_tokens
                    else:
                        buffer = buffer + "\n\n" + segment if buffer else segment
                        buffer_tokens += segment_tokens
            if buffer:
                chunks.append(
                    _make_chunk(file_seq, source_collection, section_id, heading_path,
                                chunk_type, buffer, chunk_idx, is_draft, buffer_tokens)
                )
                chunk_idx += 1

    return chunks


def chunk_mtu(mtu: Any, text: str) -> list[dict]:
    """Chunk one Minimal Teachable Unit. Normally one chunk; split if oversized.

    `mtu` is duck-typed: needs .mtu_id, .collection, .title, .keywords,
    .unit_kind, .line_range. Each chunk carries the MTU metadata so retrieval
    can filter/label by unit.
    """
    chunks = chunk_markdown(mtu.mtu_id, text, source_collection=mtu.collection)
    if not chunks:
        chunks = [
            _make_chunk(mtu.mtu_id, mtu.collection, mtu.title, [mtu.title],
                        mtu.unit_kind, text.strip(), 0, False, _estimate_tokens(text))
        ]
    extra = {
        "mtu_id": mtu.mtu_id,
        "title": mtu.title,
        "keywords": list(mtu.keywords),
        "unit_kind": mtu.unit_kind,
        "line_range": list(mtu.line_range),
    }
    for chunk in chunks:
        chunk.update(extra)
    return chunks


def _prepare_markdown_text(text: str) -> str:
    text = _normalize_math_delimiters(text)
    text = _normalize_html(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _normalize_math_delimiters(text: str) -> str:
    text = re.sub(
        r"\\\[(.+?)\\\]",
        lambda m: f"\n\n$$\n{m.group(1).strip()}\n$$\n\n",
        text,
        flags=re.DOTALL,
    )
    return re.sub(r"\\\((.+?)\\\)", lambda m: f"${m.group(1).strip()}$", text, flags=re.DOTALL)


def _normalize_html(text: str) -> str:
    text = re.sub(
        r"<table\b.*?</table>",
        lambda m: _html_table_to_markdown(m.group(0)),
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<img\b[^>]*>", lambda m: _image_alt_text(m.group(0)), text, flags=re.IGNORECASE)
    text = re.sub(r"</?(?:div|span|p|br)\b[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text)


def _html_table_to_markdown(html: str) -> str:
    parser = _TableParser()
    parser.feed(html)
    rows = [[cell.strip() for cell in row if cell.strip()] for row in parser.rows]
    rows = [row for row in rows if row]
    if not rows:
        return ""

    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    header = padded[0]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    for row in padded[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n\n" + "\n".join(lines) + "\n\n"


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._current_row = []
        elif tag in ("td", "th") and self._current_row is not None:
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("td", "th") and self._current_row is not None and self._current_cell is not None:
            cell = re.sub(r"\s+", " ", "".join(self._current_cell)).strip()
            self._current_row.append(cell)
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None:
            self.rows.append(self._current_row)
            self._current_row = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)


def _image_alt_text(tag: str) -> str:
    match = re.search(r'alt=["\']([^"\']*)["\']', tag, flags=re.IGNORECASE)
    if not match:
        return ""
    alt = unescape(match.group(1)).strip()
    if not alt or alt.lower() == "image":
        return ""
    return f" [Image: {alt}] "


def _is_noise_chunk(text: str) -> bool:
    if _extract_formulas(text):
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return True
    if all(re.match(r"^#{1,6}\s+\S+", line) for line in lines):
        cleaned = " ".join(re.sub(r"^#{1,6}\s+", "", line) for line in lines)
        cleaned = re.sub(r"[^\w一-鿿]+", "", cleaned)
        return len(cleaned) <= 32
    return False


def _split_long_paragraph(paragraph: str, max_tokens: int) -> list[str]:
    safe_limit = max(1, max_tokens - TOKEN_SAFETY_MARGIN)
    if _estimate_tokens(paragraph) <= safe_limit:
        return [paragraph]

    pieces = [p.strip() for p in re.split(r"(?<=[。！？；;.!?])", paragraph) if p.strip()] or [paragraph]
    chunks: list[str] = []
    buffer = ""
    for piece in pieces:
        for atom in _split_oversized_text(piece, safe_limit):
            merged = f"{buffer}{atom}" if buffer else atom
            if buffer and _estimate_tokens(merged) > safe_limit:
                chunks.append(buffer)
                buffer = atom
            else:
                buffer = merged
    if buffer:
        chunks.append(buffer)
    return chunks


def _split_oversized_text(text: str, max_tokens: int) -> list[str]:
    if _estimate_tokens(text) <= max_tokens:
        return [text]
    max_chars = max(60, int(max_tokens * 1.2))
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]


def _make_chunk(
    file_seq: str,
    source_collection: str,
    section_id: str,
    heading_path: list[str],
    chunk_type: str,
    text: str,
    idx: int,
    is_draft: bool,
    token_estimate: int,
) -> dict:
    weak_concepts = _extract_concepts(text)
    raw_formulas = _extract_formulas(text)
    return {
        "chunk_id": f"{file_seq}-{idx:03d}",
        "chunk_index": idx,
        "text": text,
        "source_collection": source_collection,
        "file_seq": file_seq,
        "section_id": section_id,
        "heading_path": heading_path,
        "weak_concepts": weak_concepts,
        "raw_formulas": raw_formulas,
        "formula_signatures": _formula_signatures(raw_formulas),
        "concepts": weak_concepts,
        "formulas": raw_formulas,
        "chunk_type": chunk_type,
        "is_draft": is_draft,
        "token_estimate": token_estimate,
    }


def _clean_heading(value: str) -> str:
    value = re.sub(r"^\s*#+\s*", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" -—:：。；;，,")


def _unique(values) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result
