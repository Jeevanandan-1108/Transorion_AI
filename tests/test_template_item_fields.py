from __future__ import annotations

from invoice_extractor.extraction.reextraction import _carry_extra_template_fields
from invoice_extractor.schema.schema import load_template_schema
from invoice_extractor.schema.template_item_fields import ITEM_FIELDS_BY_TEMPLATE_FILE
from invoice_extractor.schema.templates_config import ITEM_SHEET_NAME, TEMPLATE_PATH
from invoice_extractor.validation.invoice_validator import (
    validate_invoice, validate_line_items, validate_taxes, validate_totals,
)
from invoice_extractor.validation.validate import validate_and_coerce


def _type_for(stem: str) -> str:
    return next(t for t, p in TEMPLATE_PATH.items() if p.stem == stem)


LOGYSIS_EXPORT = _type_for("ExportXLSXTemplate")
ICAFFE_EXPORT = _type_for("iCaffeExcel_Export")
LOGYSIS_IMPORT = _type_for("Import- LOGYSIS TEMPLATE")
ICAFFE_IMPORT = _type_for("ImportExcel (3)")


def _statuses(checks, rule):
    return [c.status for c in checks if c.rule == rule]


def test_every_mapped_column_exists_on_its_template():
    for stem, fields in ITEM_FIELDS_BY_TEMPLATE_FILE.items():
        t = _type_for(stem)
        cols = set(load_template_schema(str(TEMPLATE_PATH[t]))[ITEM_SHEET_NAME[t]]["fields"])
        missing = [c for c in fields.values() if c and c not in cols]
        assert not missing, (stem, missing)


def test_import_templates_check_qty_x_price_against_printed_amount():
    ok = {"Quantity": 10, "Rate": 2.5, "Amount": 25}
    bad = {"Quantity": 10, "Rate": 2.5, "Amount": 30}
    assert _statuses(validate_line_items([ok], LOGYSIS_IMPORT), "QTY_X_UNIT_PRICE") == ["PASS"]
    assert _statuses(validate_line_items([bad], LOGYSIS_IMPORT), "QTY_X_UNIT_PRICE") == ["ERROR"]
    row = {"QTY": 4, "UNIT_PRICE": 12, "TOTAL": 48}
    assert _statuses(validate_line_items([row], ICAFFE_IMPORT), "QTY_X_UNIT_PRICE") == ["PASS"]


def test_import_template_sums_printed_amounts_to_header_total():
    items = [{"QTY": 4, "UNIT_PRICE": 12, "TOTAL": 48}, {"QTY": 1, "UNIT_PRICE": 2, "TOTAL": 2}]
    assert _statuses(validate_totals({"invoice_total": 50}, items, ICAFFE_IMPORT), "HEADER_LINE_TOTAL") == ["PASS"]


def test_export_templates_never_compare_qty_x_price_to_igst_taxable_value():
    row = {"Quantity": 10, "Unit_Price": 5, "Per_Unit_No.": "", "IGST_Taxable_Amt": 4150}
    assert _statuses(validate_line_items([row], LOGYSIS_EXPORT), "QTY_X_UNIT_PRICE") == ["NOT_CHECKED"]


def test_export_template_totals_use_qty_x_price_over_per_unit():
    items = [
        {"Quantity": 1000, "Rate Value": 25, "No of Unit (Per)": "100", "TAXABLE_VALUE": 21000},
        {"Quantity": 2, "Rate Value": 50, "No of Unit (Per)": "", "TAXABLE_VALUE": 8400},
    ]
    assert _statuses(validate_totals({"invoice_total": 350}, items, ICAFFE_EXPORT), "HEADER_LINE_TOTAL") == ["PASS"]
    short = validate_totals({"invoice_total": 900}, items, ICAFFE_EXPORT)
    assert _statuses(short, "LINE_ITEMS_INCOMPLETE") == ["ERROR"]


def test_export_template_skips_totals_when_rate_unit_differs_from_qty_unit():
    items = [{"Quantity": 10, "Unit of Qty": "NOS", "Rate Value": 3, "Unit of Rate": "KGS"}]
    assert _statuses(validate_totals({"invoice_total": 30}, items, ICAFFE_EXPORT), "HEADER_LINE_TOTAL") == ["NOT_CHECKED"]


def test_export_templates_reconcile_igst_on_the_taxable_value_column():
    ok = {"IGST_Taxable_Amt": 1000, "IGST_Rate": "18", "IGST_Amount": 180}
    bad = {"TAXABLE_VALUE": 1000, "IGST_RATE": 18, "IGST_AMOUNT": 120}
    assert _statuses(validate_taxes([ok], LOGYSIS_EXPORT), "TAX_RECONCILIATION") == ["PASS"]
    assert _statuses(validate_taxes([bad], ICAFFE_EXPORT), "TAX_RECONCILIATION") == ["ERROR"]


def test_original_export_template_line_math_is_unchanged():
    item = {"Item_Qty": 2, "Item_Unit_Price": 5, "ItmTaxableVal": 10, "ItmIGSTPer": 18, "ItmIGSTamt": 1.8}
    assert _statuses(validate_line_items([item], "export"), "QTY_X_UNIT_PRICE") == ["PASS"]
    assert _statuses(validate_taxes([item], "export"), "TAX_RECONCILIATION") == ["PASS"]


def _coerce(shipment_type: str, line_items: list[dict]):
    schema = load_template_schema(str(TEMPLATE_PATH[shipment_type]))
    raw = {"invoice_header": {"invoice_number": "A1", "invoice_total": 100}, "line_items": line_items}
    sheets, _, header = validate_and_coerce(raw, schema, shipment_type)
    return sheets[ITEM_SHEET_NAME[shipment_type]], header


def test_retry_shaped_items_without_chunk_columns_are_kept():
    items = [
        {"product_description": "Bolt", "quantity": 10, "unit_price": 5, "line_total": 50},
        {"product_description": "Nut", "quantity": 10, "unit_price": 5, "line_total": 50},
    ]
    rows, header = _coerce(ICAFFE_IMPORT, items)
    assert [r["TOTAL"] for r in rows] == [50, 50]
    validation = validate_invoice({"header": header, "sheets": {"ITEM": rows}}, "", ICAFFE_IMPORT)
    assert _statuses(validation.checks, "HEADER_LINE_TOTAL") == ["PASS"]


def test_line_items_pass_wins_over_chunk_values_for_core_columns():
    items = [{
        "product_description": "Bolt", "quantity": 10, "unit_price": 5, "line_total": 50,
        "_extra_template_fields": {"QTY": 99, "UNIT_PRICE": 1, "TOTAL": 99, "BRAND": "ACME"},
    }]
    (row,), _ = _coerce(ICAFFE_IMPORT, items)
    assert (row["QTY"], row["UNIT_PRICE"], row["TOTAL"], row["BRAND"]) == (10, 5, 50, "ACME")


def test_export_template_line_total_is_not_written_into_igst_taxable_value():
    items = [{"product_description": "Pump", "quantity": 2, "unit_price": 50, "line_total": 100}]
    (row,), _ = _coerce(LOGYSIS_EXPORT, items)
    assert row["IGST_Taxable_Amt"] == "" and row["Quantity"] == 2


def test_long_tail_columns_are_carried_across_a_retry():
    old = [{"product_description": "A", "_extra_template_fields": {"BRAND": "X"}},
           {"product_description": "B", "_extra_template_fields": {"BRAND": "Y"}}]
    same_count = [{"product_description": "A"}, {"product_description": "B"}]
    _carry_extra_template_fields(same_count, old, None)
    assert [it["_extra_template_fields"]["BRAND"] for it in same_count] == ["X", "Y"]

    grown = [{"product_description": "A"}, {"product_description": "NEW"}, {"product_description": "B"}]
    _carry_extra_template_fields(grown, old, None)
    assert [it.get("_extra_template_fields", {}).get("BRAND") for it in grown] == ["X", None, "Y"]

    linked = [{"item_ser_no": 2}, {"item_ser_no": 1}]
    _carry_extra_template_fields(linked, [{"item_ser_no": 1, "_extra_template_fields": {"k": 1}}], "Item_Ser_No")
    assert linked[1]["_extra_template_fields"] == {"k": 1} and "_extra_template_fields" not in linked[0]
