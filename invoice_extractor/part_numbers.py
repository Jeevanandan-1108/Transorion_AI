"""Part-number candidates for one line item.

An invoice row can print several identifiers for the same item (Manufacturer
PN, Internal PN, Customer PN, Article No, Model No ...). The master-data
mapping needs every one of them, so each item carries `part_numbers`, an
ordered list with the best candidate first, and `part_number` stays as that
first entry for anything that still reads a single value.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

MAX_PART_NUMBERS = 8

_EMPTY = {"", "NA", "N/A", "NONE", "NULL", "NIL", "-", "--"}
_SPLIT_RE = re.compile(r"\s*(?:[;\n\r]|,(?=\s)|\s\|\s|\s{3,})\s*")


def part_key(value: Any) -> str:
    """Comparison key: upper-case letters and digits only."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _pieces(value: Any) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, (list, tuple, set)):
        for v in value:
            yield from _pieces(v)
        return
    if isinstance(value, dict):
        yield from _pieces(value.get("value"))
        return
    text = str(value).strip()
    if not text:
        return
    for part in _SPLIT_RE.split(text):
        part = part.strip().strip("\"'")
        if part:
            yield part


def clean_part_numbers(*sources: Any, limit: int = MAX_PART_NUMBERS) -> list[str]:
    """Merge candidates from strings and lists into one ordered, de-duplicated
    list. Earlier sources win the earlier slots; values are kept as printed."""
    out: list[str] = []
    seen: set[str] = set()
    for source in sources:
        for piece in _pieces(source):
            key = part_key(piece)
            if len(key) < 2 or piece.strip().upper() in _EMPTY or key in seen:
                continue
            seen.add(key)
            out.append(piece)
            if len(out) >= limit:
                return out
    return out


def primary_part_number(item: dict[str, Any]) -> str:
    """The single best part number of an item: `part_number` when set (the
    guardrails keep it in step with the list), else the first listed."""
    found = all_part_numbers(item)
    return found[0] if found else ""


def all_part_numbers(item: dict[str, Any]) -> list[str]:
    """Every candidate of an item, primary first (also for rows that only have
    the single `part_number`)."""
    return clean_part_numbers(item.get("part_number"), item.get("part_numbers"))


def share_part_number(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ka = {part_key(v) for v in all_part_numbers(a)}
    kb = {part_key(v) for v in all_part_numbers(b)}
    return bool(ka & kb)
