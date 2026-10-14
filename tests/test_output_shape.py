"""Tests for output_shape.py's apply_output_shape() -- controls which
top-level keys, header fields, and sheet columns make it into the final
result JSON."""
from __future__ import annotations

from output_shape import apply_output_shape

_RESULT = {
    "source_file": "inv.pdf",
    "shipment_type": "export",
    "detection": {"export_score": 5, "import_score": 0, "signal": "keyword"},
    "token_usage": {"total_tokens": 100, "cost_usd": 0.01},
    "header": {"invoice_number": "INV-1", "supplier_name": "ACME", "buyer_name": "BUYER CO"},
    "sheets": {
        "ITEM": [{"Item_Ser_No": 1, "Item_Desc": "WIDGET", "Item_Qty": 5, "Item_Unit_Price": 2.0}],
        "DRAWBACK": [],
    },
    "warnings": [],
}


def test_default_config_is_a_no_op():
    shaped = apply_output_shape(_RESULT, {"top_level_keys": None, "header_fields": None, "sheet_fields": None})
    assert shaped == _RESULT


def test_top_level_keys_filters_to_only_the_named_keys():
    shaped = apply_output_shape(_RESULT, {"top_level_keys": ["source_file", "header", "sheets"]})
    assert set(shaped.keys()) == {"source_file", "header", "sheets"}
    assert "token_usage" not in shaped and "detection" not in shaped


def test_header_fields_filters_to_only_the_named_fields():
    shaped = apply_output_shape(_RESULT, {"header_fields": ["invoice_number"]})
    assert shaped["header"] == {"invoice_number": "INV-1"}
    # top-level keys untouched since that config key wasn't set
    assert "token_usage" in shaped


def test_sheet_fields_filters_only_the_named_sheet_and_columns():
    shaped = apply_output_shape(_RESULT, {"sheet_fields": {"ITEM": ["Item_Desc", "Item_Qty"]}})
    assert shaped["sheets"]["ITEM"] == [{"Item_Desc": "WIDGET", "Item_Qty": 5}]
    assert shaped["sheets"]["DRAWBACK"] == []  # untouched sheet stays as-is


def test_a_field_name_that_does_not_exist_is_silently_skipped():
    shaped = apply_output_shape(_RESULT, {"header_fields": ["invoice_number", "nonexistent_field"]})
    assert shaped["header"] == {"invoice_number": "INV-1"}


def test_all_three_controls_combine():
    shaped = apply_output_shape(_RESULT, {
        "top_level_keys": ["header", "sheets"],
        "header_fields": ["supplier_name"],
        "sheet_fields": {"ITEM": ["Item_Ser_No"]},
    })
    assert shaped == {"header": {"supplier_name": "ACME"}, "sheets": {"ITEM": [{"Item_Ser_No": 1}], "DRAWBACK": []}}


def test_original_result_dict_is_never_mutated():
    import copy
    original = copy.deepcopy(_RESULT)
    apply_output_shape(_RESULT, {"top_level_keys": ["source_file"], "header_fields": ["invoice_number"]})
    assert _RESULT == original
