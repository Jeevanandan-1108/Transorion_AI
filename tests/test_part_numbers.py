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


def test_clean_keeps_every_code_and_drops_one_character_values():
    # No ceiling: the backend loops the list against the masters database, so
    # a row contributes every code it prints. A caller can still ask for one.
    assert len(clean_part_numbers([f"P{i:03d}" for i in range(20)])) == 20
    assert len(clean_part_numbers([f"P{i:03d}" for i in range(20)], limit=8)) == 8
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


def test_a_po_number_never_leads_but_stays_as_a_lookup_candidate():
    items = [{"part_number": "4500123", "part_numbers": ["4500123", "AB-1234"], "order_number": "4500123"}]
    llm_extract.apply_line_item_guardrails(items, "PO No: 4500123  P/N: AB-1234")
    assert items[0]["part_number"] == "AB-1234"
    assert items[0]["part_numbers"] == ["AB-1234", "4500123"]


def test_a_row_with_two_real_codes_does_not_get_the_po_appended():
    items = [{"part_numbers": ["4500123", "AB-1234", "CD-9"], "order_number": "4500123"}]
    llm_extract.apply_line_item_guardrails(items, "PO No: 4500123  P/N: AB-1234  MPN CD-9")
    assert items[0]["part_numbers"] == ["AB-1234", "CD-9"]


def test_a_row_with_no_part_code_falls_back_to_the_order_number():
    items = [{"part_numbers": [], "order_number": "4500123", "product_description": "WIDGET"}]
    notes = llm_extract.apply_line_item_guardrails(items, "PO No: 4500123  WIDGET  qty 5")
    assert items[0]["part_numbers"] == ["4500123"]
    assert items[0]["part_number"] == "4500123"
    assert any("extra masters-lookup candidate" in n for n in notes)


def test_rows_of_one_purchase_order_are_not_merged_through_their_shared_po():
    priced = {"part_numbers": ["AB-1", "PO-9"], "order_number": "PO-9",
              "unit_price": 5, "quantity": 3, "product_description": "ITEM"}
    other = {"part_numbers": ["CD-2", "PO-9"], "order_number": "PO-9",
             "unit_price": None, "quantity": 3, "product_description": "OTHER"}
    assert not share_part_number(priced, other)


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


# ── header-label priority (Part Number/P-N, Article No/ART, Internal PN) ──

def test_article_header_outranks_manufacturer_pn():
    text = "Line│Manufacturer PN│ART│Qty│Price\n1│MPN-999│ART-123│10│5.00"
    lookup, _, row_parts = llm_extract._part_table_scan(text)
    assert lookup["MPN999"] == "ART-123"
    assert row_parts["MPN999"] == ["ART-123", "MPN-999"]


def test_explicit_part_number_header_outranks_internal_pn():
    text = "Line│Internal PN│Part Number│Qty│Price\n1│INT-1│PN-9│10│5.00"
    lookup, _, row_parts = llm_extract._part_table_scan(text)
    assert lookup["INT1"] == "PN-9"
    assert row_parts["INT1"] == ["PN-9", "INT-1"]


def test_article_header_outranks_internal_pn_even_when_printed_later():
    text = "Line│Internal PN│ART│Qty│Price\n1│INT-2│ART-1│10│5.00"
    lookup, _, _ = llm_extract._part_table_scan(text)
    assert lookup["INT2"] == "ART-1"


def test_same_priority_tier_keeps_table_left_to_right_order():
    text = "Line│Manufacturer PN│MPN│Qty│Price\n1│MFR-1│MPN-2│10│5.00"
    lookup, _, _ = llm_extract._part_table_scan(text)
    assert lookup["MFR1"] == "MFR-1"


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


def test_output_rows_carry_the_list_and_the_single_primary_string():
    from pathlib import Path
    from invoice_extractor.schema.schema import load_template_schema
    from invoice_extractor.validation.validate import validate_and_coerce
    schema = load_template_schema(Path(__file__).resolve().parent.parent / "templates" / "EXP_TEMPLET.xlsx")
    raw = {"invoice_header": {"invoice_number": "INV-1"},
           "line_items": [{"item_ser_no": 1, "product_description": "X", "quantity": 1, "unit_price": 2.0,
                           "part_numbers": ["A1-100", "B2-200"]}]}
    row = validate_and_coerce(raw, schema, "export")[0]["ITEM"][0]
    assert row["part_numbers"] == ["A1-100", "B2-200"]
    assert row["part_number"] == "A1-100"


def test_prompt_covers_part_codes_inside_a_combined_description_header():
    prompt = llm_extract._LINE_ITEMS_SYSTEM_PROMPT
    assert "Description/Customer Part No." in prompt and "trailing code" not in prompt.lower() or "end of the description" in prompt
    assert '"part_numbers"' in prompt and '"part_number"' not in prompt


# ── part_numbers is never empty ──────────────────────────────────────────

def test_an_unlabeled_row_code_is_recovered_when_the_model_found_nothing():
    items = [{"product_description": "WIDGET ASSY P701970D-FF0000", "part_numbers": []}]
    notes = llm_extract.apply_line_item_guardrails(items, "1 WIDGET ASSY P701970D-FF0000 2 NOS 25.00")
    assert items[0]["part_numbers"] == ["P701970D-FF0000"]
    assert items[0]["part_number"] == "P701970D-FF0000"
    assert any("unlabeled code" in n for n in notes)


def test_recovery_never_invents_a_part_number_from_the_hsn_code():
    items = [{"product_description": "WIDGET 84831099", "order_number": "4500123",
              "hsn_code": "84831099", "part_numbers": []}]
    llm_extract.apply_line_item_guardrails(items, "PO No: 4500123  WIDGET 84831099  qty 5")
    # Nothing is recovered from the text -- the only code there is the HSN.
    # The order number is appended as the lookup candidate of last resort.
    assert items[0]["part_numbers"] == ["4500123"]


def test_an_empty_row_is_a_retryable_error_when_another_row_has_a_part_number():
    items = [{"Item_Desc1": "WIDGET", "part_numbers": ["AB-1234"], "model_or_type": "", "order_number": ""},
             {"Item_Desc1": "GADGET", "part_numbers": [], "model_or_type": "", "order_number": ""}]
    check = next(c for c in validate_source_consistency(items, "import", invoice_text="")
                 if c.rule == "PART_NUMBERS_REQUIRED" and c.item == 2)
    assert check.status == "ERROR" and check.retryable


def test_an_empty_row_is_a_retryable_error_when_the_text_prints_a_part_label():
    items = [{"Item_Desc1": "WIDGET", "part_numbers": [], "model_or_type": "", "order_number": ""}]
    check = next(c for c in validate_source_consistency(items, "import", invoice_text="Part No. AB-1234")
                 if c.rule == "PART_NUMBERS_REQUIRED")
    assert check.status == "ERROR" and check.retryable


def test_an_invoice_that_prints_no_code_at_all_is_warned_about_not_failed():
    items = [{"Item_Desc1": "38MP Microscope Set", "part_numbers": [], "model_or_type": "", "order_number": ""}]
    check = next(c for c in validate_source_consistency(
        items, "import", invoice_text="Code Description Qty  1 38MP Microscope Set 3")
        if c.rule == "PART_NUMBERS_REQUIRED")
    assert check.status == "WARNING" and not check.retryable


def test_a_row_with_a_part_number_passes_the_required_check():
    items = [{"Item_Desc1": "WIDGET", "part_numbers": ["AB-1234"], "model_or_type": "", "order_number": ""}]
    check = next(c for c in validate_source_consistency(items, "import", invoice_text="")
                 if c.rule == "PART_NUMBERS_REQUIRED")
    assert check.status == "PASS"


def test_the_prompt_forbids_an_empty_part_numbers_list():
    assert "part_numbers must NEVER be empty" in llm_extract._LINE_ITEMS_SYSTEM_PROMPT


# -- every label alias a part number is printed under ---------------------

import pytest
from invoice_extractor.part_numbers import PART_HEADER_RE, PART_VALUE_RE, HSN_VALUE_RE


@pytest.mark.parametrize("header", [
    "Part No", "Part Number", "Part Code", "P/N", "P.N.", "PN",
    "APN", "KPN", "MPN", "CPT#",
    "ART", "Art No", "Article", "Article No",
    "Material", "Material No", "Customer Material", "Cust Material",
    "Product", "Product Code", "Model", "Model No",
    "MFG", "MFG Part Number", "Manufacture PN", "Mfr Part No",
    "Internal PN", "Customer PN", "Cust P/N", "Your Part Number", "Our Part No",
    "VISTEON PART NO.", "FG Code", "SKU", "Item Code", "Drawing No",
    "Seller Reference Number", "Particulars",
])
def test_every_part_label_alias_is_recognised_as_a_part_column(header):
    assert PART_HEADER_RE.match(header)


@pytest.mark.parametrize("header", [
    "HS Code", "HSN Code", "Tariff Code", "Description", "Qty", "Unit Price",
    "Amount", "UOM", "Country of Origin", "Net Weight",
    "PO No", "P/O No", "Purchase Order", "Order No", "Invoice No",
    "Sr No", "Item No", "Serial No", "Batch No",
])
def test_a_column_that_is_not_a_part_number_is_never_taken_for_one(header):
    assert not PART_HEADER_RE.match(header)


def test_a_label_printed_in_free_text_yields_its_value():
    for text, expected in [
        ("Cust P/N: AB-1234", "AB-1234"),
        ("MFG PART NO. 1004128", "1004128"),
        ("Article No 70-3569-403-1", "70-3569-403-1"),
        ("APN: A-1234", "A-1234"),
        ("KPN 77X-9", "77X-9"),
        ("VISTEON PART NO. V-9981", "V-9981"),
    ]:
        assert PART_VALUE_RE.search(text).group(1) == expected, text


def test_a_part_number_label_never_eats_the_number_of_part_number():
    assert PART_VALUE_RE.search("Part Number ABC-123").group(1) == "ABC-123"


def test_a_tariff_code_is_recognised_however_it_is_labelled():
    for text in ("HSN Code: 85144715", "HS Code 8514 4715", "CTH 85144715",
                 "Tariff No. 85144715"):
        assert HSN_VALUE_RE.search(text), text


# -- the aliases drive the item-table scan --------------------------------

def test_an_art_column_outranks_a_manufacturer_column():
    lookup, _, row_parts = llm_extract._part_table_scan(
        "Line│Manufacturer PN│ART│Qty\n1│MPN-999│ART-123│10")
    assert lookup["MPN999"] == "ART-123"
    assert row_parts["MPN999"] == ["ART-123", "MPN-999"]


def test_a_brand_named_part_column_is_still_the_primary_part_number():
    lookup, _, _ = llm_extract._part_table_scan(
        "Line│Customer PN│VISTEON PART NO.│Qty\n1│CP-7│VP-1│10")
    assert lookup["CP7"] == "VP-1"


def test_an_apn_and_a_kpn_column_are_both_read():
    _, _, row_parts = llm_extract._part_table_scan("Line│APN│KPN│Qty\n1│A-1│K-2│10")
    assert row_parts["A1"] == ["A-1", "K-2"]


def test_an_fg_code_column_is_a_part_number_column():
    lookup, _, _ = llm_extract._part_table_scan("Line│FG Code│Qty\n1│FG-7701│10")
    assert lookup["FG7701"] == "FG-7701"


# -- a tariff code is never a part number ---------------------------------

def test_a_tariff_code_labelled_in_the_document_is_dropped_from_the_list():
    items = [{"part_numbers": ["85144715", "KS-06152"]}]
    notes = llm_extract.apply_line_item_guardrails(
        items, "HSN Code: 85144715   Part No. KS-06152   qty 5")
    assert items[0]["part_numbers"] == ["KS-06152"]
    assert items[0]["part_number"] == "KS-06152"
    assert any("HSN" in n for n in notes)


# -- recovery stays off specifications ------------------------------------

def test_a_spec_inside_the_description_is_not_mistaken_for_a_part_number():
    items = [{"product_description": "38MP Microscope Set", "part_numbers": []}]
    llm_extract.apply_line_item_guardrails(items, "1 38MP Microscope Set 3 200.00")
    assert items[0]["part_numbers"] == []


def test_a_line_number_is_never_kept_as_a_part_number():
    items = [{"part_numbers": ["04", "51222123F0"]}]
    notes = llm_extract.apply_line_item_guardrails(items, "0010 04 51222123F0 qty 5")
    assert items[0]["part_numbers"] == ["51222123F0"]
    assert items[0]["part_number"] == "51222123F0"
    assert any("line / serial number" in n for n in notes)


def test_an_all_numeric_part_number_long_enough_to_be_real_is_kept():
    items = [{"part_numbers": ["1004128"]}]
    llm_extract.apply_line_item_guardrails(items, "Part No. 1004128 qty 5")
    assert items[0]["part_numbers"] == ["1004128"]


# ── the product cell carries candidates too ──────────────────────────────

def test_the_product_column_code_is_the_rows_part_number():
    """A packing-list style table whose only identifier column is headed
    "Product" (Schneider Batam): LV908000 is the item's part number."""
    items = [{"product_description": "LV908000 ULP Insulation Module for ML+",
              "quantity": 48, "part_numbers": []}]
    llm_extract.apply_line_item_guardrails(
        items, "Product  Description  Quantity\nLV908000 ULP Insulation Module for ML+ 48 PC")
    assert items[0]["part_numbers"] == ["LV908000"]
    assert items[0]["part_number"] == "LV908000"


def test_a_code_in_the_product_cell_is_added_beside_the_labeled_part_number():
    """The labeled part number still leads; the code printed inside the
    product / description cell follows it as another masters candidate."""
    items = [{"part_numbers": ["AB-1234"],
              "product_description": "LV908000 ULP Insulation Module"}]
    llm_extract.apply_line_item_guardrails(
        items, "Part No. AB-1234  LV908000 ULP Insulation Module 48 PC")
    assert items[0]["part_numbers"] == ["AB-1234", "LV908000"]
    assert items[0]["part_number"] == "AB-1234"


def test_a_code_in_the_model_cell_is_a_candidate_too():
    items = [{"part_numbers": ["AB-1234"], "model_or_type": "RT-EF-605"}]
    llm_extract.apply_line_item_guardrails(items, "Part No. AB-1234 Model RT-EF-605")
    assert items[0]["part_numbers"] == ["AB-1234", "RT-EF-605"]


def test_a_measurement_in_the_description_is_not_taken_as_a_candidate():
    items = [{"part_numbers": ["AB-1234"],
              "product_description": "Cable 2.5mm2 100M 240V 38MP"}]
    llm_extract.apply_line_item_guardrails(
        items, "Part No. AB-1234 Cable 2.5mm2 100M 240V 38MP")
    assert items[0]["part_numbers"] == ["AB-1234"]


def test_a_code_not_printed_in_the_document_is_not_added_from_the_description():
    items = [{"part_numbers": ["AB-1234"], "product_description": "INVENTED-9999 widget"}]
    llm_extract.apply_line_item_guardrails(items, "Part No. AB-1234 widget 5")
    assert items[0]["part_numbers"] == ["AB-1234"]


def test_the_order_number_still_comes_after_the_product_cell_code():
    items = [{"part_numbers": ["AB-1234"], "order_number": "4500123",
              "product_description": "LV908000 Module"}]
    llm_extract.apply_line_item_guardrails(
        items, "PO No: 4500123  Part No. AB-1234  LV908000 Module")
    assert items[0]["part_numbers"] == ["AB-1234", "LV908000"]


# ── a date is never part of an identifier ────────────────────────────────

import pytest  # noqa: E402

from invoice_extractor.part_numbers import strip_dates  # noqa: E402


def test_the_order_numbers_own_date_is_cut_out_of_the_candidate():
    """Real case: a PO cell carrying its date between two reference numbers.
    The date goes; both numbers stay."""
    assert clean_part_numbers(
        ["2CDS206760P0012", "249876187 000170 dated 10.08.2026 / 5191747503"]
    ) == ["2CDS206760P0012", "249876187 000170 / 5191747503"]


@pytest.mark.parametrize("value,expected", [
    ("249876187 000170 dated 10.08.2026 / 5191747503", "249876187 000170 / 5191747503"),
    ("PO-4500123 dt. 10/08/26", "PO-4500123"),
    ("4500123 (dated 10-Aug-2026)", "4500123"),
    ("AB-1234 date: 2026-08-10", "AB-1234"),
])
def test_a_date_printed_inside_a_value_is_removed(value, expected):
    assert strip_dates(value) == expected


@pytest.mark.parametrize("value", [
    "10.08.2026", "2026-08-10", "10-Aug-2026", "10/08/2026", "dated 10.08.2026",
])
def test_a_value_that_is_only_a_date_drops_out_entirely(value):
    assert strip_dates(value) == ""
    assert clean_part_numbers([value]) == []


@pytest.mark.parametrize("value", [
    "AB-1234", "2CDS206760P0012", "70-3569-403-1", "2605 - 403", "1004128",
    "P701970D-FF0000", "5191724473-00030", "1SDR001825A1817",
    "1107515361/000020/1107515361/", "LV908000",
    # No date word and no four-digit year: as likely a part number as a date,
    # and part_numbers is a candidate set, so it is kept.
    "12-05-99", "123-45-6789",
])
def test_a_real_part_number_is_never_touched(value):
    assert strip_dates(value) == value
    assert clean_part_numbers([value]) == [value]


def test_a_date_only_order_number_is_not_appended_as_a_candidate():
    items = [{"part_numbers": ["AB-1234"], "order_number": "10.08.2026"}]
    llm_extract.apply_line_item_guardrails(items, "Part No. AB-1234 dated 10.08.2026")
    assert items[0]["part_numbers"] == ["AB-1234"]
