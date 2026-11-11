from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from invoice_extractor.schema.schema import load_template_schema
from invoice_extractor.validation.validate import (
    _clean_generic_item,
    _clean_invoice_header,
    _coerce_number,
    _map_export_item,
    _map_import_item,
    _renumber_items_if_ser_no_resets,
    map_extra_sheets,
    validate_and_coerce,
)

_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def test_currency_string_coercion():
    assert _coerce_number("$1,234.50") == 1234.50
    assert _coerce_number("₹1,000") == 1000
    assert _coerce_number("not a number") is None
    assert _coerce_number(42) == 42


def test_map_export_item_basic_fields():
    item = {
        "item_ser_no": 1, "product_description": "ROTOR SHAFT", "part_number": "",
        "model_or_type": "", "order_number": "", "hsn_code": "84831099",
        "quantity": 10, "unit_of_measurement": "PCS", "unit_price": 5.0,
        "line_total": 50.0, "end_use_code": "", "fta_code": "",
        "country_of_origin": "", "igst_amount": "", "igst_rate": "", "packages": "",
    }
    mapped = _map_export_item(item, {})
    assert mapped["Item_RITC"] == "84831099"
    assert mapped["ItmTaxableVal"] == 50.0


def test_map_export_item_extra_template_fields_fill_in_a_blank_mapped_column():
    item = {
        "item_ser_no": 1, "product_description": "ROTOR SHAFT", "part_number": "",
        "model_or_type": "", "order_number": "", "hsn_code": "84831099",
        "quantity": 10, "unit_of_measurement": "PCS", "unit_price": 5.0,
        "line_total": 50.0, "end_use_code": "", "fta_code": "",
        "country_of_origin": "", "igst_amount": "", "igst_rate": "", "packages": "",
        "_extra_template_fields": {"State Code": "27", "Itm_Manu_Code": "MC-9"},
    }
    mapped = _map_export_item(item, {})
    assert mapped["State Code"] == "27"
    assert mapped["Itm_Manu_Code"] == "MC-9"


def test_map_export_item_extra_template_fields_never_override_a_real_mapped_value():
    item = {
        "item_ser_no": 1, "product_description": "ROTOR SHAFT", "part_number": "",
        "model_or_type": "", "order_number": "", "hsn_code": "84831099",
        "quantity": 10, "unit_of_measurement": "PCS", "unit_price": 5.0,
        "line_total": 50.0, "end_use_code": "", "fta_code": "",
        "country_of_origin": "", "igst_amount": "", "igst_rate": "", "packages": "",
        "_extra_template_fields": {"Item_RITC": "99999999"},
    }
    mapped = _map_export_item(item, {})
    assert mapped["Item_RITC"] == "84831099"


def test_map_export_item_uses_own_packages_over_header_total():
    item = {
        "item_ser_no": 1, "product_description": "ROTOR SHAFT", "part_number": "",
        "model_or_type": "", "order_number": "", "hsn_code": "84831099",
        "quantity": 10, "unit_of_measurement": "PCS", "unit_price": 5.0,
        "line_total": 50.0, "end_use_code": "", "fta_code": "",
        "country_of_origin": "", "igst_amount": "", "igst_rate": "", "packages": 108,
    }
    mapped = _map_export_item(item, {"total_packages": 612})
    assert mapped["ItmTotpkg"] == 108


def test_map_export_item_falls_back_to_header_packages_when_item_has_none():
    item = {
        "item_ser_no": 1, "product_description": "ROTOR SHAFT", "part_number": "",
        "model_or_type": "", "order_number": "", "hsn_code": "84831099",
        "quantity": 10, "unit_of_measurement": "PCS", "unit_price": 5.0,
        "line_total": 50.0, "end_use_code": "", "fta_code": "",
        "country_of_origin": "", "igst_amount": "", "igst_rate": "", "packages": "",
    }
    mapped = _map_export_item(item, {"total_packages": 6})
    assert mapped["ItmTotpkg"] == 6


def test_clean_generic_item_coerces_packages_field():
    raw_item = {
        "item_ser_no": 1, "product_description": "ROTOR SHAFT",
        "quantity": 10, "unit_price": 5.0, "line_total": 50.0,
        "packages": 108,
    }
    cleaned = _clean_generic_item(raw_item, warnings=[], row_idx=0, ser_no=1)
    assert cleaned["packages"] == 108


def test_clean_generic_item_defaults_missing_packages_to_empty_string():
    raw_item = {
        "item_ser_no": 1, "product_description": "ROTOR SHAFT",
        "quantity": 10, "unit_price": 5.0, "line_total": 50.0,
    }
    cleaned = _clean_generic_item(raw_item, warnings=[], row_idx=0, ser_no=1)
    assert cleaned["packages"] == ""


def test_map_import_item_basic_fields():
    item = {
        "item_ser_no": 1, "product_description": "VALVE", "part_number": "",
        "model_or_type": "X200", "order_number": "", "hsn_code": "84819000",
        "quantity": 5, "unit_of_measurement": "NOS", "unit_price": 20.0,
        "line_total": 100.0, "end_use_code": "", "fta_code": "",
        "country_of_origin": "DE", "igst_amount": "", "igst_rate": "", "packages": "",
    }
    mapped = _map_import_item(item, {})
    assert mapped["Item_Desc1"] == "VALVE"
    assert mapped["Item_Country_Org"] == "DE"
    assert mapped["Model"] == "X200"
    assert mapped["Item_Taxable_Val"] == 100.0


def test_clean_invoice_header_export_still_repairs_gstin():
    warnings = []
    clean = _clean_invoice_header(
        {"supplier_tax_id": "03AABCS7623P1Z3X"}, warnings, "export",
    )
    assert clean["supplier_tax_id"] == "03AABCS7623P1Z3"
    assert not warnings


def test_clean_invoice_header_import_does_not_gstin_check_foreign_vat():
    warnings = []
    clean = _clean_invoice_header(
        {"supplier_tax_id": "DE123456789"}, warnings, "import",
    )
    assert clean["supplier_tax_id"] == "DE123456789"
    assert not warnings


def test_clean_invoice_header_import_state_code_not_overridden_by_foreign_vat():
    warnings = []
    clean = _clean_invoice_header(
        {"supplier_tax_id": "12345678900", "state_code": "27"}, warnings, "import",
    )
    assert clean["state_code"] == "27"
    assert not warnings


def test_validate_and_coerce_export_no_confidence_and_blank_missing():
    schema = load_template_schema(_TEMPLATES / "EXP_TEMPLET.xlsx")
    raw = {
        "invoice_header": {"invoice_number": "INV-1", "supplier_name": "ACME"},
        "line_items": [
            {"item_ser_no": 1, "product_description": "WIDGET", "quantity": 2, "unit_price": 10.0, "line_total": 20.0},
        ],
    }
    sheets, warnings, header = validate_and_coerce(raw, schema, "export")

    assert "ITEM" in sheets and len(sheets["ITEM"]) == 1
    row = sheets["ITEM"][0]

    for field_name in schema["ITEM"]["fields"]:
        assert field_name in row
    assert row["item_Cess"] == ""
    assert row["Printed"] == ""
    assert "_confidence" not in row
    assert not any(k for k in header if "confidence" in k.lower())


def test_missing_line_total_is_left_blank_not_computed():
    warnings = []
    item = _clean_generic_item(
        {"product_description": "WIDGET", "quantity": 5, "unit_price": 10.0}, warnings, 0, 1,
    )
    assert item["line_total"] == ""


def test_free_of_cost_zero_line_total_is_kept_as_zero_with_warning():
    warnings = []
    item = _clean_generic_item(
        {"product_description": "SAMPLE", "quantity": 5, "unit_price": 10.0, "line_total": 0},
        warnings, 0, 1,
    )
    assert item["line_total"] == 0
    assert any("free of cost" in w for w in warnings)


def test_zero_line_total_survives_mapping_into_the_export_row():
    schema = load_template_schema(_TEMPLATES / "EXP_TEMPLET.xlsx")
    raw = {
        "invoice_header": {"invoice_number": "INV-1"},
        "line_items": [
            {"item_ser_no": 1, "product_description": "SAMPLE", "quantity": 5, "unit_price": 10.0, "line_total": 0},
            {"item_ser_no": 2, "product_description": "WIDGET", "quantity": 5, "unit_price": 10.0},
        ],
    }
    sheets, _, _ = validate_and_coerce(raw, schema, "export")
    assert [row["ItmTaxableVal"] for row in sheets["ITEM"]] == [0, ""]


def test_validate_and_coerce_import_uses_boe_sheet():
    schema = load_template_schema(_TEMPLATES / "IMP_TEMPLET.xlsx")
    raw = {
        "invoice_header": {"invoice_number": "INV-2"},
        "line_items": [{"item_ser_no": 1, "product_description": "PUMP", "quantity": 1, "unit_price": 100.0}],
    }
    sheets, warnings, header = validate_and_coerce(raw, schema, "import")
    assert "BOE" in sheets and len(sheets["BOE"]) == 1
    assert "ITEM" not in sheets


def test_renumber_items_if_ser_no_resets_detects_and_fixes_mid_list_restart():
    items = [
        {"item_ser_no": 79}, {"item_ser_no": 80},
        {"item_ser_no": 1}, {"item_ser_no": 2}, {"item_ser_no": 3},
        {"item_ser_no": 92},
    ]
    warnings = []
    _renumber_items_if_ser_no_resets(items, warnings)
    assert [it["item_ser_no"] for it in items] == [1, 2, 3, 4, 5, 6]
    assert warnings and "reset or repeated" in warnings[0]


def test_renumber_items_if_ser_no_resets_leaves_clean_ascending_sequence_alone():
    items = [{"item_ser_no": 1}, {"item_ser_no": 2}, {"item_ser_no": None}, {"item_ser_no": 4}]
    warnings = []
    _renumber_items_if_ser_no_resets(items, warnings)
    assert [it["item_ser_no"] for it in items] == [1, 2, None, 4]
    assert not warnings


def test_renumber_items_if_ser_no_resets_leaves_all_null_alone():
    items = [{"item_ser_no": None}, {"item_ser_no": None}, {"item_ser_no": None}]
    warnings = []
    _renumber_items_if_ser_no_resets(items, warnings)
    assert [it["item_ser_no"] for it in items] == [None, None, None]
    assert not warnings


def test_validate_and_coerce_renumbers_end_to_end_on_ser_no_reset():
    schema = load_template_schema(_TEMPLATES / "IMP_TEMPLET.xlsx")
    raw = {
        "invoice_header": {"invoice_number": "INV-3"},
        "line_items": [
            {"item_ser_no": None, "product_description": "A", "quantity": 1, "unit_price": 1.0},
            {"item_ser_no": None, "product_description": "B", "quantity": 1, "unit_price": 1.0},
            {"item_ser_no": 1, "product_description": "C", "quantity": 1, "unit_price": 1.0},
            {"item_ser_no": 2, "product_description": "D", "quantity": 1, "unit_price": 1.0},
        ],
    }
    sheets, warnings, header = validate_and_coerce(raw, schema, "import")
    assert [row["SL_No"] for row in sheets["BOE"]] == [1, 2, 3, 4]
    assert any("reset or repeated" in w for w in warnings)


def test_state_code_not_overridden_by_a_malformed_tax_id_starting_with_digits():
    warnings = []
    clean = _clean_invoice_header(
        {"supplier_tax_id": "0388012345", "state_code": "MH(27)"}, warnings, "export",
    )
    assert clean["state_code"] == "27"
    assert not any("used the GSTIN" in w for w in warnings)


def test_state_code_still_overridden_by_a_valid_gstin_on_mismatch():
    warnings = []
    clean = _clean_invoice_header(
        {"supplier_tax_id": "27AAACG8030H2ZR", "state_code": "29"}, warnings, "export",
    )
    assert clean["state_code"] == "27"
    assert any("used the GSTIN" in w for w in warnings)


def test_state_code_filled_from_a_valid_gstin_when_missing():
    clean = _clean_invoice_header({"supplier_tax_id": "27AAACG8030H2ZR"}, [], "export")
    assert clean["state_code"] == "27"


def test_gstin_with_a_letter_entity_code_is_valid():
    from invoice_extractor.validation.validate import _GSTIN_RE
    assert _GSTIN_RE.match("27AAACG8030HAZR")
    assert _GSTIN_RE.match("27AAACG8030H2ZR")
    assert not _GSTIN_RE.match("27AAACG8030H2XR")
    assert not _GSTIN_RE.match("0388012345")


def test_euro_is_normalized_to_eur():
    assert _clean_invoice_header({"currency": "EURO"}, [], "export")["currency"] == "EUR"


def test_other_unambiguous_currency_spellings_are_normalized():
    for raw, iso in [("Euros", "EUR"), ("€", "EUR"), ("US$", "USD"), ("U.S. Dollars", "USD"),
                     ("Rs.", "INR"), ("Indian Rupees", "INR"), ("£", "GBP"), ("usd", "USD")]:
        assert _clean_invoice_header({"currency": raw}, [], "export")["currency"] == iso, raw


def test_ambiguous_or_unknown_currency_is_left_alone():
    for raw in ("$", "¥", "DOLLARS", "ZZ-COIN"):
        assert _clean_invoice_header({"currency": raw}, [], "export")["currency"] == raw, raw


def test_missing_currency_stays_blank():
    assert _clean_invoice_header({"currency": None}, [], "export")["currency"] == ""
    assert _clean_invoice_header({}, [], "export")["currency"] == ""


from invoice_extractor.validation.validate import _drop_packing_list_duplicates, _drop_duplicates_that_break_total


def _gi(desc, qty=475, price=65.0, total=30875.0, part=""):
    return {"product_description": desc, "quantity": qty, "unit_price": price,
            "line_total": total, "part_number": part, "item_ser_no": 1}


def test_priceless_packing_list_repeat_is_dropped_by_description_without_a_part_number():
    items = [_gi("OMEPRAZOLE GR PELLETS 13.4% M/M"), _gi("OMEPRAZOLE GR PELLETS 13.4% M/M", price="", total="")]
    warnings = []
    kept = _drop_packing_list_duplicates(items, warnings)
    assert len(kept) == 1 and kept[0]["unit_price"] == 65.0
    assert warnings and "packing-list repeat" in warnings[0]


def test_priceless_repeat_tolerates_small_ocr_differences_in_the_description():
    items = [_gi("ATORVASTATIN CALCIUM TRIHYDRATE USP", qty=26, price=120.0, total=3120.0),
             _gi("ATORVASTATINCALCIUM TRIHYDRATE USP", qty=26, price="", total="")]
    assert len(_drop_packing_list_duplicates(items, [])) == 1


def test_priceless_row_for_a_different_product_is_kept():
    items = [_gi("OMEPRAZOLE GR PELLETS"), _gi("PANTOPRAZOLE SODIUM SESQUIHYDRATE", price="", total="")]
    assert len(_drop_packing_list_duplicates(items, [])) == 2


def test_priceless_row_with_a_different_quantity_is_kept():
    items = [_gi("OMEPRAZOLE GR PELLETS"), _gi("OMEPRAZOLE GR PELLETS", qty=100, price="", total="")]
    assert len(_drop_packing_list_duplicates(items, [])) == 2


def _bundle_items():
    return [
        _gi("AMLODIPINE BESYLATE PH.EUR", 30, 80.0, 2400.0),
        _gi("Amiodipine Besilate-Hetero", 30, 80.0, 2400.0),
        _gi("JAMLODIPIN R93339 BESILATE P1 PH.EUR Batch: AAAL 6", 30.0, 80.0, 2400.0),
    ]


def test_bundle_duplicates_are_dropped_when_that_reconciles_the_printed_total():
    warnings = []
    kept = _drop_duplicates_that_break_total(_bundle_items(), {"invoice_total": 2400}, warnings)
    assert len(kept) == 1 and kept[0]["product_description"] == "AMLODIPINE BESYLATE PH.EUR"
    assert warnings and "bundled PDF" in warnings[0]


def test_genuine_repeated_lines_are_kept_when_they_already_reconcile():
    items = [_gi("OMEPRAZOLE GR PELLETS batch A"), _gi("OMEPRAZOLE GR PELLETS batch B")]
    assert len(_drop_duplicates_that_break_total(items, {"invoice_total": 61750}, [])) == 2


def test_duplicates_are_kept_if_dropping_them_would_not_reconcile_either():
    assert len(_drop_duplicates_that_break_total(_bundle_items(), {"invoice_total": 3000}, [])) == 3


def test_no_numeric_total_means_nothing_is_dropped():
    assert len(_drop_duplicates_that_break_total(_bundle_items(), {"invoice_total": ""}, [])) == 3
    assert len(_drop_duplicates_that_break_total(_bundle_items(), {"invoice_total": "2400.00 / 2400.00"}, [])) == 3


def test_identical_numbers_on_unrelated_products_are_never_treated_as_duplicates():
    items = [_gi("OMEPRAZOLE GR PELLETS", 30, 80.0, 2400.0), _gi("ZZZZ QQQQ XXXX", 30, 80.0, 2400.0)]
    assert len(_drop_duplicates_that_break_total(items, {"invoice_total": 2400}, [])) == 2


def test_validate_and_coerce_applies_the_bundle_dedupe_end_to_end():
    schema = load_template_schema(_TEMPLATES / "EXP_TEMPLET.xlsx")
    raw = {
        "invoice_header": {"invoice_number": "SI3626102057", "invoice_total": 2400},
        "line_items": [
            {"item_ser_no": 1, "product_description": "AMLODIPINE BESYLATE PH.EUR", "quantity": 30, "unit_price": 80.0, "line_total": 2400.0},
            {"item_ser_no": 2, "product_description": "Amiodipine Besilate-Hetero", "quantity": 30, "unit_price": 80.0, "line_total": 2400.0},
            {"item_ser_no": 3, "product_description": "AMLODIPINE BESILATE PH EUR", "quantity": 30, "unit_price": 80.0, "line_total": 2400.0},
        ],
    }
    sheets, warnings, _ = validate_and_coerce(raw, schema, "export")
    assert len(sheets["ITEM"]) == 1
    assert [row["Item_Ser_No"] for row in sheets["ITEM"]] == [1]


def test_bundle_duplicate_with_a_long_description_is_still_recognised():
    items = _bundle_items()
    items[2]["product_description"] = (
        "JAMLODIPIN R93339 BESILATE P1 PH.EUR Batch: AAAL 6070071 Mfg.Date: 62026 "
        "Expiry/Retest Date: 62031 Packing Details: 1X30.00 KGS"
    )
    kept = _drop_duplicates_that_break_total(items, {"invoice_total": 2400}, [])
    assert len(kept) == 1


def test_word_overlap_does_not_match_unrelated_products():
    from invoice_extractor.validation.validate import _desc_similarity
    assert _desc_similarity("OMEPRAZOLE GR PELLETS 13.4% M/M", "PANTOPRAZOLE SODIUM SESQUIHYDRATE") < 0.6
    assert _desc_similarity("BOLT", "NUT WASHER ASSEMBLY KIT") < 0.6


def test_bundle_duplicates_with_different_extra_text_are_recognised_by_shared_words():
    items = [
        _gi("AMLODIPINE BESYLATE PH.EUR [1X30 KGS]", 30.0, 80.0, 2400.0),
        _gi("Amiodipine Besilate-Hetero Mfg Part No:11101355-HETERO", 30, 80.0, 2400.0),
        _gi("JAMLODIPIN R93339 BESILATE", 30.0, 80.0, 2400.0),
    ]
    warnings = []
    kept = _drop_duplicates_that_break_total(items, {"invoice_total": 2400}, warnings)
    assert len(kept) == 1 and kept[0]["product_description"].startswith("AMLODIPINE")
    assert warnings and "rows 2, 3" in warnings[0]


def test_shared_words_alone_never_drop_anything_that_already_reconciles():
    items = [_gi("AMLODIPINE BESYLATE batch A", 30, 80.0, 2400.0), _gi("AMLODIPINE BESYLATE batch B", 30, 80.0, 2400.0)]
    assert len(_drop_duplicates_that_break_total(items, {"invoice_total": 4800}, [])) == 2


def test_shared_long_words_ignores_short_and_unrelated_words():
    from invoice_extractor.validation.validate import _shared_long_words
    assert _shared_long_words("PH EUR KGS", "PH EUR KGS") == 0
    assert _shared_long_words("OMEPRAZOLE GR PELLETS", "PANTOPRAZOLE SODIUM SESQUIHYDRATE") == 0
    assert _shared_long_words("AMLODIPINE BESYLATE PH.EUR", "Amiodipine Besilate-Hetero") == 2


def test_a_row_with_no_numbers_at_all_is_discarded_with_a_warning():
    warnings = []
    assert _clean_generic_item({"product_description": "STRAY FRAGMENT", "hsn_code": ""}, warnings, 4, 5) is None
    assert warnings and "discarded a row with no quantity, unit price or amount" in warnings[0]


def test_a_row_with_any_one_number_is_kept():
    for field, value in [("quantity", 5), ("unit_price", 2.5), ("line_total", 12.5)]:
        item = _clean_generic_item({"product_description": "WIDGET", field: value}, [], 0, 1)
        assert item is not None, field


def test_map_extra_sheets_fills_every_column_of_the_named_sheet():
    schema = load_template_schema(_TEMPLATES / "EXP_TEMPLET.xlsx")
    raw_extra = {"AR4": [{"Item_No": 1, "AR4_Number": "AR4-123"}]}
    warnings = []
    mapped = map_extra_sheets(raw_extra, schema, "ITEM", warnings)
    assert list(mapped.keys()) == ["AR4"]
    row = mapped["AR4"][0]
    assert set(row.keys()) == set(schema["AR4"]["fields"])
    assert row["AR4_Number"] == "AR4-123"
    assert row["AR4_Date"] == ""
    assert not warnings


def test_map_extra_sheets_drops_an_unknown_sheet_name_with_a_warning():
    schema = load_template_schema(_TEMPLATES / "EXP_TEMPLET.xlsx")
    warnings = []
    mapped = map_extra_sheets({"NotARealSheet": [{"X": 1}]}, schema, "ITEM", warnings)
    assert mapped == {}
    assert any("NotARealSheet" in w for w in warnings)


def test_map_extra_sheets_never_touches_the_item_sheet_itself():
    schema = load_template_schema(_TEMPLATES / "EXP_TEMPLET.xlsx")
    mapped = map_extra_sheets({"ITEM": [{"Item_Ser_No": 1}]}, schema, "ITEM", [])
    assert mapped == {}


def test_map_extra_sheets_skips_non_dict_rows():
    schema = load_template_schema(_TEMPLATES / "EXP_TEMPLET.xlsx")
    mapped = map_extra_sheets({"STR": ["not a dict", {"Item_No": 1, "STR_Code": "X"}]}, schema, "ITEM", [])
    assert len(mapped["STR"]) == 1
    assert mapped["STR"][0]["STR_Code"] == "X"


def test_validate_and_coerce_merges_extra_sheets_into_the_output():
    schema = load_template_schema(_TEMPLATES / "EXP_TEMPLET.xlsx")
    raw = {
        "invoice_header": {"invoice_number": "INV-9"},
        "line_items": [{"item_ser_no": 1, "product_description": "A", "quantity": 1, "unit_price": 1.0}],
    }
    raw_extra_sheets = {"License": [{"Item No": 1, "License Category": "ADVANCE"}]}
    sheets, warnings, _ = validate_and_coerce(raw, schema, "export", raw_extra_sheets=raw_extra_sheets)
    assert sheets["License"][0]["License Category"] == "ADVANCE"
    assert sheets["DRAWBACK"] == []


def test_validate_and_coerce_with_no_extra_sheets_behaves_exactly_as_before():
    schema = load_template_schema(_TEMPLATES / "EXP_TEMPLET.xlsx")
    raw = {
        "invoice_header": {"invoice_number": "INV-10"},
        "line_items": [{"item_ser_no": 1, "product_description": "A", "quantity": 1, "unit_price": 1.0}],
    }
    sheets, _, _ = validate_and_coerce(raw, schema, "export")
    assert sheets["License"] == []
