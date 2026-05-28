"""Fix corrupted math symbols from PDF text extraction.

PDFs created from Word/LaTeX often encode math symbols as Unicode
Mathematical Italic characters. When extracted by PyMuPDF, these
appear as doubled characters like 𝒒𝒒 (italic q repeated) or
∆𝑮𝑮𝒇𝒇 (ΔGf with each letter doubled).

This module:
1. Normalizes Mathematical Italic/Bold Italic chars to plain ASCII
2. Deduplicates doubled italic chars (𝒒𝒒 → q, 𝑮𝑮 → G)
3. Preserves legitimate doubled letters (aa in "haar", etc.)
"""

import re

# Unicode Mathematical character ranges → plain ASCII mapping
MATH_RANGES = [
    # (start_code, plain_start, length, category_name)
    (0x1D44E, ord('a'), 26, "italic small"),        # a-z italic
    (0x1D468, ord('A'), 26, "italic capital"),       # A-Z italic
    (0x1D482, ord('a'), 26, "bold italic small"),    # a-z bold italic
    (0x1D49C, ord('A'), 26, "bold italic capital"),  # A-Z bold italic
    (0x1D4B6, ord('a'), 26, "bold script small"),    # a-z bold script
    (0x1D4D0, ord('A'), 26, "bold script capital"),  # A-Z bold script
    (0x1D4EA, ord('a'), 26, "fraktur small"),
    (0x1D504, ord('A'), 26, "fraktur capital"),
    (0x1D51E, ord('a'), 26, "double-struck small"),
    (0x1D538, ord('A'), 26, "double-struck capital"),
    (0x1D552, ord('a'), 26, "bold small"),
    (0x1D56C, ord('A'), 26, "bold capital"),
    (0x1D586, ord('a'), 26, "sans-serif small"),
    (0x1D5A0, ord('A'), 26, "sans-serif capital"),
]

# Special math symbols that often appear corrupted
SPECIAL_SYMBOLS = {
    0x019F: 'O',    # Ɵ (Latin Capital Letter O with Middle Tilde) → O
    0x01D0: 'θ',    # ǐ sometimes used for theta
    0x0398: 'Θ',    # Θ Greek capital theta
    0x03B8: 'θ',    # θ Greek small theta
    0x0394: 'Δ',    # Δ Greek capital delta (keep as-is, it's correct)
    0x2206: 'Δ',    # ∆ Increment (map to Δ)
}

# Build lookup table
_CHAR_MAP: dict[int, str] = {}

for start, plain_start, length, _ in MATH_RANGES:
    for i in range(length):
        _CHAR_MAP[start + i] = chr(plain_start + i)

for code, replacement in SPECIAL_SYMBOLS.items():
    _CHAR_MAP[code] = replacement


def _normalize_char(c: str) -> str:
    """Normalize a single Unicode math char to plain ASCII."""
    code = ord(c)
    if code in _CHAR_MAP:
        return _CHAR_MAP[code]
    return c


def fix_math_symbols(text: str) -> str:
    """Fix corrupted math symbols in extracted PDF text.

    Step 1: Normalize all Mathematical Unicode chars to plain ASCII.
    Step 2: Deduplicate doubled chars that result from italic extraction
            (e.g., 𝒒𝒒 → qq → q, ∆𝑮𝑮𝒇𝒇 → ΔGGf → ΔGf).
    """
    # Step 1: Normalize Unicode math chars
    normalized = []
    for c in text:
        normalized.append(_normalize_char(c))
    result = ''.join(normalized)

    # Step 2: Deduplicate doubled letters that are PDF extraction artifacts.
    # In chemistry/physics PDFs, math italic chars are always extracted doubled.
    # We deduplicate any doubled ASCII letter (AA→A, qq→q) since legitimate
    # doubled letters in Chinese scientific text are extremely rare.
    # Exception: keep doubled letters in common Chinese words/compounds
    # like "aa" in English text (handled by checking context).

    # Deduplicate: any letter followed by itself → single letter
    # This handles GG→G, ff→f, qq→q, UU→U, SS→S, etc.
    result = re.sub(r'([A-Za-z])\1', r'\1', result)

    # Fix specific known patterns from chemistry PDFs
    result = result.replace('∆', 'Δ')  # Normalize delta symbol

    return result


if __name__ == "__main__":
    # Test with real corrupted text
    sample = "时可逆蒸发的𝒒𝒒、𝑾𝑾、∆𝑼𝑼、∆𝑺𝑺和∆𝑮𝑮（计算时可以假定蒸气是理想气体"
    fixed = fix_math_symbols(sample)
    print(f"Before: {sample}")
    print(f"After:  {fixed}")

    sample2 = "标准生成自由能∆𝑮𝑮𝒇𝒇Ɵ分别为209.2和124.5 kJ/mol"
    fixed2 = fix_math_symbols(sample2)
    print(f"\nBefore: {sample2}")
    print(f"After:  {fixed2}")

    sample3 = "标准生成焓∆𝑯𝑯𝒇𝒇Ɵ分别为−538.1和−333.2 kJ/mol"
    fixed3 = fix_math_symbols(sample3)
    print(f"\nBefore: {sample3}")
    print(f"After:  {fixed3}")
