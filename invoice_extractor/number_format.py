from __future__ import annotations

import re

# --- what counts as evidence of the number style on a page --------------------
#
# Only values that can be read ONE way are counted. "1.000" and "2,592" are
# left out of both tallies: "1.000 EA" is a quantity of one on a German SAP
# invoice and a thousand on a US one, and "2,592" the other way round.
#
# A decimal-comma value is: thousands dots then a comma ("10.478,22"), a
# plain two-decimal amount ("702,78"), a 4-6 decimal rate ("3,6974"), or a
# leading-zero small number ("0,001").
_COMMA_EVIDENCE_RE = re.compile(
    r"(?<![\d.,])(?:\d{1,3}(?:\.\d{3})+,\d{2,6}|\d+,\d{2}|\d+,\d{4,6}|0,\d{3})(?!\d)(?![.,]\d)"
)
# A decimal-point value: thousands commas then a point ("10,478.22", also the
# Indian "1,23,456.00"), a two-decimal amount ("702.78") or a 4-6 decimal rate.
# Dates such as 08.07.2026 are excluded by the trailing/leading checks.
_DOT_EVIDENCE_RE = re.compile(
    r"(?<![\d.,])(?:\d{1,3}(?:,\d{3})+\.\d{1,6}|\d{1,2}(?:,\d{2})+,\d{3}\.\d{1,6}|\d+\.\d{2}|\d+\.\d{4,6})(?!\d)(?![.,]\d)"
)

_MIN_COMMA_AMOUNTS = 3
# A page is decimal-comma when its comma amounts outnumber the point amounts by
# this factor. A stray "7.00 Kgs" or a "1.000 EA" next to a column of
# "USD 702,78" amounts must not veto the page, which a plain "no point
# decimals at all" rule did.
_DOMINANCE = 2

_THOUSANDS_DOTS_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+),(?=\d)")
# French / Swiss grouping: "1 234,56", "12 345,60" (plain, no-break or thin space).
_THOUSANDS_SPACES_RE = re.compile(r"(?<![\d.,/-])(\d{1,3}(?:[   ]\d{3})+),(?=\d{2}(?!\d))")
# A comma between digits becomes a point -- except inside a number that is
# already point-decimal ("1,234.56") or Indian-grouped ("51,81,785"), which
# must keep its commas.
_COMMA_TO_POINT_RE = re.compile(
    r"(?P<keep>(?<![\d.,])(?:\d{1,3}(?:,\d{3})+\.\d+|\d{1,2}(?:,\d{2})+,\d{3}(?!\d)))"
    r"|(?<=\d),(?=\d)"
)


def _tally(text: str) -> tuple[int, int]:
    return len(_COMMA_EVIDENCE_RE.findall(text)), len(_DOT_EVIDENCE_RE.findall(text))


def uses_decimal_commas(text: str) -> bool:
    """True when amounts are written 742,50 / 1.234,50 rather than 742.50.
    A few point-style values (weights, a "1.000 EA" quantity) do not change that;
    a page whose point values rival its comma values is left alone."""
    commas, dots = _tally(text)
    return commas >= _MIN_COMMA_AMOUNTS and commas >= _DOMINANCE * dots


_PAGE_BANNER_RE = re.compile(r"^=+\s*PAGE\s+\d+\s+OF\s+\d+.*$", re.IGNORECASE | re.MULTILINE)


def _normalize_one(text: str, document_is_decimal_comma: bool = False) -> tuple[str, bool]:
    commas, dots = _tally(text)
    own = commas >= _MIN_COMMA_AMOUNTS and commas >= _DOMINANCE * dots
    # A continuation page of a decimal-comma document may carry a single
    # amount ("527,20") -- too little to judge alone, but the rest of the
    # document has already said how its numbers are written.
    from_document = document_is_decimal_comma and commas >= 1 and commas >= dots
    if not (own or from_document):
        return text, False
    text = _THOUSANDS_DOTS_RE.sub(lambda m: m.group(1).replace(".", "") + ",", text)
    text = _THOUSANDS_SPACES_RE.sub(lambda m: re.sub(r"[   ]", "", m.group(1)) + ",", text)
    return _COMMA_TO_POINT_RE.sub(lambda m: m.group("keep") or ".", text), True


def normalize_decimal_commas(text: str) -> tuple[str, bool]:
    """Rewrite decimal-comma pages with decimal points, so "150,000" is read
    as 150.000 and not as a hundred and fifty thousand. Thousands dots are
    dropped first. Each page is judged on its own, because a bundle can pair
    an invoice written 742,50 with a courier label written 742.50; a page too
    thin to judge alone follows the document when the document as a whole
    clearly uses commas. Pages that use decimal points are returned unchanged,
    and values already written with a point ("1.000 EA", "7.00") are never
    touched."""
    marks = list(_PAGE_BANNER_RE.finditer(text))
    if len(marks) < 2:
        return _normalize_one(text)
    bounds = [0] + [m.start() for m in marks[1:]] + [len(text)]
    pages = [text[a:b] for a, b in zip(bounds, bounds[1:])]
    tallies = [_tally(p) for p in pages]
    total_commas = sum(c for c, _ in tallies)
    total_dots = sum(d for _, d in tallies)
    document = total_commas >= _MIN_COMMA_AMOUNTS and total_commas >= _DOMINANCE * total_dots
    parts, changed = [], False
    for page in pages:
        out, did = _normalize_one(page, document)
        parts.append(out)
        changed |= did
    return "".join(parts), changed
