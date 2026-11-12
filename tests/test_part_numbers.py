from __future__ import annotations

import invoice_extractor.extraction.llm_extract as llm_extract
from invoice_extractor.part_numbers import all_part_numbers, clean_part_numbers, share_part_number
from invoice_extractor.validation.invoice_validator import validate_source_consistency
from invoice_extractor.validation.validate import _clean_generic_item, _drop_packing_list_duplicates


# ── the helper ───────────────────────────────────────────────────────────

def test_clean_merges_dedupes_and_keeps_order():
    assert clean_part_numbers("ABC-1", ["abc 1", "XYZ9", None, "N/A", "", "  786-786 "]) == ["ABC-1", "XYZ9", "786-786"]


def test_clean_splits_a_joined_string_but_not_a_dashed_code():
    assert clean_part_numbers("HUA44295; RTO020F27001JTE3") == ["HUA44295", "RTO020F27001JTE3"]
    assert clean_part_numbers("70-3569-403-1") == ["70-3569-403-1"]


def test_clean_caps_the_list_and_drops_one_character_values():
    assert len(clean_part_numbers([f"P{i:03d}" for i in range(20)])) == 8
    assert clean_part_numbers(["A", "-"]) == []


def test_all_part_numbers_covers_rows_that_only_have_the_single_value():
    assert all_part_numbers({"part_number": "HUA44295"}) == ["HUA44295"]
    assert all_part_numbers({"part_number": "B2", "part_numbers": ["A1", "B2"]}) == ["B2", "A1"]


# ── guardrails ───────────────────────────────────────────────────────────

_TABLE = (
    "Sr │ Manufacturer PN │ Internal PN │ Qty │ Price\n"
    "1 │ RTO020F27001JTE3 │ HUA44295 │ 10 │ 5.00\n"
)


def test_every_part_column_of_the_row_is_kept_internal_first():
    items = [{"part_number": "RTO020F27001JTE3", "part_numbers": ["RTO020F27001JTE3"]}]
    llm_extract.apply_line_item_guardrails(items, _TABLE)
    assert items[0]["part_numbers"] == ["HUA44295", "RTO020F27001JTE3"]
    assert items[0]["part_number"] == "HUA44295"


def test_a_po_number_is_dropped_from_the_list_and_the_next_candidate_leads():
    items = [{"part_number": "4500123", "part_numbers": ["4500123", "AB-1234"], "order_number": "4500123"}]
    llm_extract.apply_line_item_guardrails(items, "PO No: 4500123  P/N: AB-1234")
    assert items[0]["part_numbers"] == ["AB-1234"] and items[0]["part_number"] == "AB-1234"


def test_a_row_with_only_the_single_value_gains_the_list():
    items = [{"part_number": "SA006180180"}]
    llm_extract.apply_line_item_guardrails(items, "Part no. SA006180180")
    assert items[0]["part_numbers"] == ["SA006180180"]


def test_an_item_without_any_part_number_is_left_untouched():
    items = [{"product_description": "WIDGET"}]
    llm_extract.apply_line_item_guardrails(items, "no codes here")
    assert "part_numbers" not in items[0] and "part_number" not in items[0]


# ── item builder, duplicates, source check ───────────────────────────────

def test_builder_keeps_the_list_and_derives_the_primary():
    row = _clean_generic_item(
        {"item_ser_no": 1, "product_description": "X", "quantity": 1, "unit_price": 2, "line_total": 2,
         "part_numbers": ["A1-100", "B2-200"]}, [], 0, 1)
    assert row["part_numbers"] == ["A1-100", "B2-200"] and row["part_number"] == "A1-100"


def test_priceless_repeat_is_found_through_a_shared_part_number_of_any_position():
    priced = {"part_numbers": ["A1-100", "B2-200"], "unit_price": 5, "quantity": 3, "product_description": "ITEM"}
    repeat = {"part_numbers": ["B2-200"], "unit_price": None, "quantity": 3, "product_description": "OTHER TEXT"}
    assert share_part_number(priced, repeat)
    assert _drop_packing_list_duplicates([priced, repeat], []) == [priced]


def test_description_part_number_matching_any_candidate_passes():
    items = [{"Item_Desc1": "ITEM P/N: 100-046567-001", "part_number": "HUA44295",
              "part_numbers": ["HUA44295", "100-046567-001"], "model_or_type": "", "order_number": ""}]
    check = next(c for c in validate_source_consistency(items, "import", invoice_text="") if c.rule == "PART_NUMBER_SOURCE_MATCH")
    assert check.status == "PASS"


def test_description_part_number_matching_none_still_errors():
    items = [{"Item_Desc1": "ITEM P/N: 100-046567-001", "part_number": "HUA44295",
              "part_numbers": ["HUA44295", "ZZ-1"], "model_or_type": "", "order_number": ""}]
    check = next(c for c in validate_source_consistency(items, "import", invoice_text="") if c.rule == "PART_NUMBER_SOURCE_MATCH")
    assert check.status == "ERROR" and check.retryable


# ── list-only output and source check ────────────────────────────────────

def test_a_candidate_not_printed_anywhere_is_dropped():
    items = [{"part_numbers": ["REAL-123", "MADE-UP-9"]}]
    notes = llm_extract.apply_line_item_guardrails(items, "Part No. REAL-123  qty 5")
    assert items[0]["part_numbers"] == ["REAL-123"]
    assert any("MADE-UP-9" in n and "not printed" in n for n in notes)


def test_source_check_tolerates_ocr_letter_digit_confusion_and_punctuation():
    items = [{"part_numbers": ["CRCW1210-68K0FKEA"]}]
    llm_extract.apply_line_item_guardrails(items, "Part CRCWl2l0 68KOFKEA resistor")
    assert items[0]["part_numbers"] == ["CRCW1210-68K0FKEA"]


def test_output_rows_carry_only_the_list():
    from pathlib import Path
    from invoice_extractor.schema.schema import load_template_schema
    from invoice_extractor.validation.validate import validate_and_coerce
    schema = load_template_schema(Path(__file__).resolve().parent.parent / "templates" / "EXP_TEMPLET.xlsx")
    raw = {"invoice_header": {"invoice_number": "INV-1"},
           "line_items": [{"item_ser_no": 1, "product_description": "X", "quantity": 1, "unit_price": 2.0,
                           "part_numbers": ["A1-100", "B2-200"]}]}
    row = validate_and_coerce(raw, schema, "export")[0]["ITEM"][0]
    assert row["part_numbers"] == ["A1-100", "B2-200"] and "part_number" not in row


def test_prompt_covers_part_codes_inside_a_combined_description_header():
    prompt = llm_extract._LINE_ITEMS_SYSTEM_PROMPT
    assert "Description/Customer Part No." in prompt and "trailing code" not in prompt.lower() or "end of the description" in prompt
    assert '"part_numbers"' in prompt and '"part_number"' not in prompt
