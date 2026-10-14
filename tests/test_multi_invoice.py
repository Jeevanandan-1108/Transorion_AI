"""
Tests for multi_invoice.py -- splitting one combined extraction (several
genuinely distinct invoices bundled in one PDF) into one independently
validated result per invoice.
"""
from __future__ import annotations

from invoice_validator import ValidationCheck, ValidationResult
from multi_invoice import (
    is_combined_document,
    split_header,
    split_items,
    combine_validations,
)


# ── is_combined_document() ──────────────────────────────────────────────

def test_is_combined_document_true_for_two_distinct_numbers():
    assert is_combined_document({"invoice_number": "INV-1 / INV-2"}) is True


def test_is_combined_document_false_for_a_single_invoice():
    assert is_combined_document({"invoice_number": "INV-1"}) is False


def test_is_combined_document_false_for_missing_or_empty():
    assert is_combined_document({"invoice_number": ""}) is False
    assert is_combined_document({}) is False
    assert is_combined_document(None) is False


# ── split_header() ───────────────────────────────────────────────────────

def test_split_header_splits_the_three_joined_fields():
    header = {
        "invoice_number": "INV-1 / INV-2",
        "invoice_date": "2026-01-01 / 2026-01-02",
        "invoice_total": "100.0 / 200.0",
        "supplier_name": "ACME CORP",  # shared, not joined
    }
    split = split_header(header)
    assert len(split) == 2
    assert split[0]["invoice_number"] == "INV-1" and split[0]["invoice_total"] == "100.0"
    assert split[1]["invoice_number"] == "INV-2" and split[1]["invoice_total"] == "200.0"
    # Shared fields copied unchanged onto every split header.
    assert split[0]["supplier_name"] == split[1]["supplier_name"] == "ACME CORP"


def test_split_header_falls_back_to_repeating_an_unaligned_field():
    # Only ONE total was explicitly printed for a 3-invoice bundle -- not
    # split 3 ways (that would invent numbers), repeated on every header
    # instead so nothing is silently dropped or guessed at.
    header = {
        "invoice_number": "INV-1 / INV-2 / INV-3",
        "invoice_date": "2026-01-01 / 2026-01-02 / 2026-01-03",
        "invoice_total": "100.0",
    }
    split = split_header(header)
    assert len(split) == 3
    assert all(h["invoice_total"] == "100.0" for h in split)


# ── split_items() ────────────────────────────────────────────────────────

def test_split_items_groups_by_exact_invoice_ref():
    items = [
        {"item_ser_no": 1, "invoice_ref": "INV-1"},
        {"item_ser_no": 1, "invoice_ref": "INV-2"},
        {"item_ser_no": 2, "invoice_ref": "INV-1"},
    ]
    groups = split_items(items, ["INV-1", "INV-2"])
    assert [i["item_ser_no"] for i in groups[0]] == [1, 2]
    assert [i["item_ser_no"] for i in groups[1]] == [1]


def test_split_items_matches_ocr_variant_spellings():
    # "S13626204603" (digit 1) vs "SI3626204603" (letter I) -- same OCR
    # confusability collapse_duplicate_invoice_refs itself relies on.
    items = [{"item_ser_no": 1, "invoice_ref": "S13626204603"}]
    groups = split_items(items, ["SI3626204603", "OTHER-INV"])
    assert len(groups[0]) == 1 and len(groups[1]) == 0


def test_split_items_with_no_ref_goes_to_the_first_invoice_not_dropped():
    items = [{"item_ser_no": 1}]  # model didn't tag it
    groups = split_items(items, ["INV-1", "INV-2"])
    assert len(groups[0]) == 1 and len(groups[1]) == 0


def test_split_items_with_an_unrecognized_ref_goes_to_the_first_invoice():
    items = [{"item_ser_no": 1, "invoice_ref": "SOMETHING-ELSE-ENTIRELY"}]
    groups = split_items(items, ["INV-1", "INV-2"])
    assert len(groups[0]) == 1


def test_split_items_skips_non_dict_rows():
    groups = split_items(["not a dict"], ["INV-1"])
    assert groups == [[]]


# ── combine_validations() ────────────────────────────────────────────────

def test_combine_validations_valid_only_if_every_sub_invoice_is():
    valid = ValidationResult(valid=True, status="VALID", checks=[ValidationCheck(rule="A", status="PASS")])
    invalid = ValidationResult(valid=False, status="INVALID", checks=[ValidationCheck(rule="B", status="ERROR")])
    combined = combine_validations([valid, invalid])
    assert combined.valid is False
    assert combined.status == "INVALID"
    assert {c.rule for c in combined.checks} == {"A", "B"}


def test_combine_validations_all_valid():
    a = ValidationResult(valid=True, status="VALID", checks=[ValidationCheck(rule="A", status="PASS")])
    b = ValidationResult(valid=True, status="VALID", checks=[ValidationCheck(rule="B", status="PASS")])
    combined = combine_validations([a, b])
    assert combined.valid is True and combined.status == "VALID"
