from __future__ import annotations
from typing import Any

from invoice_extractor.extraction.llm_extract import _split_combined_parts, _ocr_key
from invoice_extractor.validation.invoice_validator import ValidationResult

_SPLIT_HEADER_FIELDS = ("invoice_number", "invoice_date", "invoice_total")


def is_combined_document(header: dict[str, Any] | None) -> bool:
    if not isinstance(header, dict):
        return False
    return len(_split_combined_parts(header.get("invoice_number"))) > 1


def split_header(header: dict[str, Any]) -> list[dict[str, Any]]:
    numbers = _split_combined_parts(header.get("invoice_number"))
    n = len(numbers)

    per_field: dict[str, list[Any]] = {}
    for field in _SPLIT_HEADER_FIELDS:
        parts = _split_combined_parts(header.get(field))
        per_field[field] = parts if len(parts) == n else [header.get(field)] * n

    split: list[dict[str, Any]] = []
    for i in range(n):
        h = dict(header)
        for field in _SPLIT_HEADER_FIELDS:
            h[field] = per_field[field][i]
        split.append(h)
    return split


def split_items(
    items_raw: list[dict[str, Any]], invoice_numbers: list[str],
) -> list[list[dict[str, Any]]]:
    keys = [_ocr_key(n) for n in invoice_numbers]
    groups: list[list[dict[str, Any]]] = [[] for _ in invoice_numbers]
    for item in items_raw:
        if not isinstance(item, dict):
            continue
        ref = item.get("invoice_ref")
        idx = 0
        if ref:
            ref_key = _ocr_key(str(ref))
            for i, k in enumerate(keys):
                if k == ref_key:
                    idx = i
                    break
        groups[idx].append(item)
    return groups


def combine_validations(results: list[ValidationResult]) -> ValidationResult:
    all_checks = [c for r in results for c in r.checks]
    valid = all(r.valid for r in results)
    return ValidationResult(valid=valid, status="VALID" if valid else "INVALID", checks=all_checks)
