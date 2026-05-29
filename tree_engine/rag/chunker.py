"""Semantic chunker for T.R.E.E. textbook Markdown files.

Chunks by heading boundaries and semantic blocks, preserving
definition/proof/example integrity.

Usage:
    from rag.chunker import chunk_markdown

    chunks = chunk_markdown("01.质点与参考系.md", markdown_text, chapter="01-力学")
    for c in chunks:
        print(c["chunk_id"], c["chunk_type"], c["text"][:50])
"""

import re
from html import unescape
from html.parser import HTMLParser

MAX_TOKENS = {
    "def": 2000,
    "proof": 3000,
    "example": 2400,
    "narrative": 1500,
}
TOKEN_SAFETY_MARGIN = 8

# Rough token estimate: 1 token ≈ 1.5 Chinese chars or 4 English chars
def _estimate_tokens(text: str) -> int:
    cn = sum(1 for c in text if "一" <= c <= "鿿")
    en = len(text) - cn
    return int(cn / 1.5 + en / 4)


def _detect_chunk_type(text: str) -> str:
    """Classify a chunk by its content."""
    lower = text[:200].lower()
    if any(kw in lower for kw in ["定义", "定理", "定律", "公理", "definition", "theorem"]):
        return "def"
    if any(kw in lower for kw in ["推导", "证明", "证", "derivation", "proof"]):
        return "proof"
    if any(kw in lower for kw in ["例题", "例", "example", "解"]):
        return "example"
    return "narrative"


def _extract_concepts(text: str) -> list[str]:
    """Extract key concept names from text (bold markers, LaTeX symbols)."""
    concepts = []
    # Bold markers: **概念名**
    for m in re.finditer(r"\*\*([^*]+)\*\*", text):
        name = m.group(1).strip()
        if len(name) <= 20 and not name.startswith("["):
            concepts.append(name)
    return concepts[:10]


def _extract_formulas(text: str) -> list[str]:
    """Extract LaTeX formulas from text."""
    formulas = []
    # Display math: \[...\]
    for m in re.finditer(r"\\\[(.+?)\\\]", text, re.DOTALL):
        formulas.append(m.group(1).strip())
    # Inline math: \(...\)
    for m in re.finditer(r"\\\((.+?)\\\)", text, re.DOTALL):
        f = m.group(1).strip()
        if len(f) > 3:
            formulas.append(f)
    # Display math: $$...$$
    for m in re.finditer(r"\$\$(.+?)\$\$", text, re.DOTALL):
        formulas.append(m.group(1).strip())
    # Inline math: $...$ (skip single-char like $x$)
    for m in re.finditer(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", text):
        f = m.group(1).strip()
        if len(f) > 3:
            formulas.append(f)
    return formulas[:10]


def chunk_markdown(
    file_seq: str,
    text: str,
    chapter: str = "",
    is_draft: bool = False,
) -> list[dict]:
    """Chunk a Markdown file into semantic pieces.

    Returns list of chunk dicts with keys:
        chunk_id, text, chapter, file_seq, section_id,
        concepts, formulas, chunk_type, is_draft, token_estimate
    """
    text = _prepare_markdown_text(text)

    # Split by ## headings (preserve heading in chunk)
    sections = re.split(r"(?=^##\s)", text, flags=re.MULTILINE)

    chunks = []
    chunk_idx = 0

    for section in sections:
        section = section.strip()
        if not section:
            continue

        # Extract section heading for section_id
        heading_match = re.match(r"^##\s+(.+)", section)
        section_id = heading_match.group(1).strip() if heading_match else "intro"
        # Clean section_id for use as anchor
        section_id = re.sub(r"[^\w一-鿿-]", "-", section_id)[:50]

        # Check for foldable blocks (> [!details])
        foldable_parts = re.split(r"(?=> \[!details\])", section)

        for part in foldable_parts:
            part = part.strip()
            if not part:
                continue
            if _is_noise_chunk(part):
                continue

            # If part starts with foldable marker, it's a proof/derivation
            if part.startswith("> [!details"):
                chunk_type = "proof"
            else:
                chunk_type = _detect_chunk_type(part)

            max_tok = MAX_TOKENS[chunk_type]

            # If within token limit, emit as single chunk
            tok_est = _estimate_tokens(part)
            if tok_est <= max_tok:
                chunks.append(_make_chunk(
                    file_seq, chapter, section_id, chunk_type,
                    part, chunk_idx, is_draft, tok_est,
                ))
                chunk_idx += 1
            else:
                # Split by paragraphs
                paragraphs = re.split(r"\n\n+", part)
                buffer = ""
                buffer_tokens = 0

                for para in paragraphs:
                    para = para.strip()
                    if not para:
                        continue
                    for segment in _split_long_paragraph(para, max_tok):
                        segment_tokens = _estimate_tokens(segment)

                        if buffer_tokens + segment_tokens > max_tok and buffer:
                            chunks.append(_make_chunk(
                                file_seq, chapter, section_id, chunk_type,
                                buffer, chunk_idx, is_draft, buffer_tokens,
                            ))
                            chunk_idx += 1
                            buffer = segment
                            buffer_tokens = segment_tokens
                        else:
                            buffer += "\n\n" + segment if buffer else segment
                            buffer_tokens += segment_tokens

                if buffer:
                    chunks.append(_make_chunk(
                        file_seq, chapter, section_id, chunk_type,
                        buffer, chunk_idx, is_draft, buffer_tokens,
                    ))
                    chunk_idx += 1

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
    return re.sub(
        r"\\\((.+?)\\\)",
        lambda m: f"${m.group(1).strip()}$",
        text,
        flags=re.DOTALL,
    )


def _normalize_html(text: str) -> str:
    text = re.sub(
        r"<table\b.*?</table>",
        lambda m: _html_table_to_markdown(m.group(0)),
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"<img\b[^>]*>",
        lambda m: _image_alt_text(m.group(0)),
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"</?(?:div|span|p|br)\b[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text)


def _html_table_to_markdown(html: str) -> str:
    parser = _TableParser()
    parser.feed(html)
    rows = [
        [cell.strip() for cell in row if cell.strip()]
        for row in parser.rows
    ]
    rows = [row for row in rows if row]
    if not rows:
        return ""

    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    header = padded[0]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
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
    """Split a paragraph when it alone exceeds the embedding-safe token budget."""
    safe_limit = max(1, max_tokens - TOKEN_SAFETY_MARGIN)
    if _estimate_tokens(paragraph) <= safe_limit:
        return [paragraph]

    pieces = [
        piece.strip()
        for piece in re.split(r"(?<=[。！？；;.!?])", paragraph)
        if piece.strip()
    ] or [paragraph]

    chunks = []
    buffer = ""
    for piece in pieces:
        for atom in _split_oversized_text(piece, safe_limit):
            merged = f"{buffer}{atom}" if buffer else atom
            merged_tokens = _estimate_tokens(merged)
            if buffer and merged_tokens > safe_limit:
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
    file_seq: str, chapter: str, section_id: str,
    chunk_type: str, text: str, idx: int,
    is_draft: bool, token_estimate: int,
) -> dict:
    return {
        "chunk_id": f"{file_seq}-{idx:03d}",
        "chunk_index": idx,
        "text": text,
        "chapter": chapter,
        "file_seq": file_seq,
        "section_id": section_id,
        "concepts": _extract_concepts(text),
        "formulas": _extract_formulas(text),
        "chunk_type": chunk_type,
        "is_draft": is_draft,
        "token_estimate": token_estimate,
    }


if __name__ == "__main__":
    sample = """## 质点与参考系

**质点**是在研究物体运动时，忽略物体的形状和大小，把它简化为一个有质量的点。

**参考系**是确定物体位置和运动状态时作为参考的物体或物体系。

### 位置矢量

位置矢量 $\\mathbf{r}$ 描述质点在空间中的位置：

$$\\mathbf{r} = x\\hat{i} + y\\hat{j} + z\\hat{k}$$

> [!details]- 推导过程
> 由坐标定义直接得到。

## 例题

**例1**：一质点沿 x 轴运动，位置 $x = 3t^2 + 2t$，求 2s 时的速度。

> [!details]- 查看解答
> $v = dx/dt = 6t + 2$
> 代入 $t = 2$：$v = 14$ m/s
"""
    chunks = chunk_markdown("01", sample, chapter="01-力学")
    for c in chunks:
        print(f"[{c['chunk_type']:8s}] {c['chunk_id']} | {c['token_estimate']:4d} tok | {c['text'][:40]}...")
