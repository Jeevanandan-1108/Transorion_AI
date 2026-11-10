from __future__ import annotations

import re

_COMMA_DECIMAL_RE = re.compile(r"(?<![\d.,])\d{1,3}(?:\.\d{3})*,\d{2,6}(?![\d,])")
_DOT_DECIMAL_RE = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3})*\.\d{2,6}(?![\d.]|,\d)")
_THOUSANDS_DOTS_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+),(?=\d)")
_COMMA_BETWEEN_DIGITS_RE = re.compile(r"(?<=\d),(?=\d)")


def uses_decimal_commas(text: str) -> bool:
    """True when amounts are written 742,50 / 1.234,50 and never 742.50."""
    return len(_COMMA_DECIMAL_RE.findall(text)) >= 3 and not _DOT_DECIMAL_RE.search(text)


_PAGE_BANNER_RE = re.compile(r"^=+\s*PAGE\s+\d+\s+OF\s+\d+.*$", re.IGNORECASE | re.MULTILINE)


def _normalize_one(text: str) -> tuple[str, bool]:
    if not uses_decimal_commas(text):
        return text, False
    text = _THOUSANDS_DOTS_RE.sub(lambda m: m.group(1).replace(".", "") + ",", text)
    return _COMMA_BETWEEN_DIGITS_RE.sub(".", text), True


def normalize_decimal_commas(text: str) -> tuple[str, bool]:
    """Rewrite decimal-comma pages with decimal points, so "150,000" is read
    as 150.000 and not as a hundred and fifty thousand. Thousands dots are
    dropped first. Each page is judged on its own, because a bundle can pair
    an invoice written 742,50 with a courier label written 742.50. Pages that
    use decimal points are returned unchanged."""
    marks = list(_PAGE_BANNER_RE.finditer(text))
    if len(marks) < 2:
        return _normalize_one(text)
    bounds = [0] + [m.start() for m in marks[1:]] + [len(text)]
    parts, changed = [], False
    for a, b in zip(bounds, bounds[1:]):
        page, did = _normalize_one(text[a:b])
        parts.append(page)
        changed |= did
    return "".join(parts), changed
