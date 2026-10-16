"""
multi_invoice.py
------------------
Splits ONE combined extraction (a single PDF bundling more than one
genuinely distinct invoice -- see llm_extract.py's _HEADER_SYSTEM_PROMPT,
"MORE THAN ONE invoice number") into one independently-validated result
per invoice, each with its own header, its own line items, and its own
total reconciled against only ITS OWN items -- instead of the single
merged header + flat item list this pipeline produces by default.

No extra LLM call for detection: it reuses the EXISTING " / "-joined
invoice_number / invoice_date / invoice_total fields the header pass
already produces for a combined document, AFTER
llm_extract.apply_header_guardrails has run (which calls
collapse_duplicate_invoice_refs to fold OCR-variant spellings of the SAME
invoice number back together) -- so by the time is_combined_document() is
called, every remaining " / "-joined part is confirmed to be a genuinely
distinct invoice, not OCR noise.

Splitting line items uses each row's own "invoice_ref" (see
llm_extract.extract_line_items's `known_invoice_numbers` parameter --
only ever asked for when the header pass already found more than one
invoice number), matched back to the header's own numbers with the same
OCR-variant-tolerant comparison (llm_extract._ocr_key) used to detect the
duplicates in the first place.
"""
from __future__ import annotations
from typing import Any

from invoice_extractor.extraction.llm_extract import _split_combined_parts, _ocr_key
from invoice_extractor.validation.invoice_validator import ValidationResult

# Fields _HEADER_SYSTEM_PROMPT documents as " / "-joined, aligned in order,
# across sub-invoices -- everything else (supplier, buyer, ports,
# currency, ...) is the same company/shipment for every sub-invoice in one
# bundled document, so it's copied unchanged onto each split header.
_SPLIT_HEADER_FIELDS = ("invoice_number", "invoice_date", "invoice_total")


def is_combined_document(header: dict[str, Any] | None) -> bool:
    """True if the header pass found more than one genuinely distinct
    invoice number. Only meaningful AFTER apply_header_guardrails has run
    (collapse_duplicate_invoice_refs) -- calling this on a raw, just-off-
    the-API header risks treating one OCR-misspelled invoice number as
    two."""
    if not isinstance(header, dict):
        return False
    return len(_split_combined_parts(header.get("invoice_number"))) > 1


def split_header(header: dict[str, Any]) -> list[dict[str, Any]]:
    """One header dict per distinct invoice. Each _SPLIT_HEADER_FIELDS
    entry takes its own aligned part; every other field is copied
    unchanged onto every split header. A _SPLIT_HEADER_FIELDS entry whose
    part count doesn't match invoice_number's (e.g. only some sub-invoices
    had their own total explicitly printed) falls back to repeating
    whatever single value was there, on every split header -- never a
    crash, and no part is invented."""
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
    """
    Groups `items_raw` (the generic-vocabulary line items straight from
    llm_extract.extract_line_items, each possibly carrying its own
    "invoice_ref") by which of `invoice_numbers` they belong to, using the
    same OCR-variant-tolerant matching (_ocr_key) that
    collapse_duplicate_invoice_refs uses for header fields -- a row tagged
    with an OCR-variant spelling of a known invoice number still lands in
    the right group. A row with no "invoice_ref" at all, or one that
    doesn't match any known number, is placed in the FIRST invoice's group
    rather than silently dropped.
    """
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
    """One rollup ValidationResult covering every sub-invoice's own checks
    -- valid only if ALL of them are. Lets the existing retry loop
    (orchestrator.py / reextraction.py), which only knows how to look at
    a single ValidationResult, keep deciding whether to retry exactly as
    it does for a single invoice, without needing to know this attempt
    actually covered several."""
    all_checks = [c for r in results for c in r.checks]
    valid = all(r.valid for r in results)
    return ValidationResult(valid=valid, status="VALID" if valid else "INVALID", checks=all_checks)
