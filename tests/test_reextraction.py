from __future__ import annotations

import asyncio

import pytest

import invoice_extractor.extraction.reextraction as reextraction
from invoice_extractor.schema.schema import load_template_schema
from invoice_extractor.schema.templates_config import TEMPLATE_PATH

EXPORT_SCHEMA = load_template_schema(TEMPLATE_PATH["export"])

_USAGE = {"input_tokens": 10, "cached_tokens": 0, "output_tokens": 10}


@pytest.fixture(autouse=True)
def _no_chunked_extras(monkeypatch):
    async def _no_chunks(*args, **kwargs):
        return {}, {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0}
    monkeypatch.setattr(reextraction, "extract_all_chunks", _no_chunks)


def _valid_header():
    return {"invoice_total": 100.0, "invoice_number": "INV-1", "invoice_date": "2026-08-08"}


def _valid_item():
    return {
        "item_ser_no": 1, "product_description": "A REAL DESCRIPTION", "quantity": 10,
        "unit_of_measurement": "PCS", "unit_price": 10.0, "line_total": 100.0,
    }


def _invalid_item():
    return {
        "item_ser_no": 1, "product_description": "", "quantity": 10,
        "unit_of_measurement": "PCS", "unit_price": 10.0, "line_total": 100.0,
    }


class _Sequenced:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, *args, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("called more times than expected")
        resp = self.responses.pop(0)
        return resp, dict(_USAGE)


def _run(coro):
    return asyncio.run(coro)


def test_retry_after_validation_failure_then_succeeds(monkeypatch):
    header_calls = _Sequenced([_valid_header()])
    items_calls = _Sequenced([[_invalid_item()], [_valid_item()]])

    monkeypatch.setattr(reextraction, "extract_invoice_header", header_calls)
    monkeypatch.setattr(reextraction, "extract_line_items", items_calls)

    result = _run(reextraction.extract_and_validate(
        "INVOICE TEXT", EXPORT_SCHEMA, "export", max_retries=2,
    ))

    assert result["extraction_status"] == "validated"
    assert result["attempts"] == 2
    assert len(result["validation_history"]) == 2
    assert result["validation_history"][0]["valid"] is False
    assert result["validation_history"][1]["valid"] is True

    assert len(header_calls.calls) == 1
    assert len(items_calls.calls) == 2
    assert items_calls.calls[1]["feedback"] is not None
    assert items_calls.calls[1]["previous_result"] == [_invalid_item()]


def test_maximum_retry_limit_stops_and_reports_failure(monkeypatch):
    header_calls = _Sequenced([_valid_header()])
    items_calls = _Sequenced([[_invalid_item()], [_invalid_item()], [_invalid_item()]])

    monkeypatch.setattr(reextraction, "extract_invoice_header", header_calls)
    monkeypatch.setattr(reextraction, "extract_line_items", items_calls)

    result = _run(reextraction.extract_and_validate(
        "INVOICE TEXT", EXPORT_SCHEMA, "export", max_retries=2,
    ))

    assert result["extraction_status"] == "validation_failed"
    assert result["attempts"] == 3
    assert len(result["validation_history"]) == 3
    assert all(h["valid"] is False for h in result["validation_history"])
    assert result["validation"]["valid"] is False
    assert len(result["validation"]["errors"]) > 0

    assert len(items_calls.calls) == 3
    assert len(header_calls.calls) == 1


def test_zero_max_retries_makes_exactly_one_attempt(monkeypatch):
    header_calls = _Sequenced([_valid_header()])
    items_calls = _Sequenced([[_invalid_item()]])

    monkeypatch.setattr(reextraction, "extract_invoice_header", header_calls)
    monkeypatch.setattr(reextraction, "extract_line_items", items_calls)

    result = _run(reextraction.extract_and_validate(
        "INVOICE TEXT", EXPORT_SCHEMA, "export", max_retries=0,
    ))

    assert result["attempts"] == 1
    assert result["extraction_status"] == "validation_failed"
    assert len(items_calls.calls) == 1


def test_valid_on_first_attempt_needs_no_retry(monkeypatch):
    header_calls = _Sequenced([_valid_header()])
    items_calls = _Sequenced([[_valid_item()]])

    monkeypatch.setattr(reextraction, "extract_invoice_header", header_calls)
    monkeypatch.setattr(reextraction, "extract_line_items", items_calls)

    result = _run(reextraction.extract_and_validate(
        "INVOICE TEXT", EXPORT_SCHEMA, "export", max_retries=2,
    ))

    assert result["attempts"] == 1
    assert result["extraction_status"] == "validated"
    assert len(header_calls.calls) == 1
    assert len(items_calls.calls) == 1


def test_header_only_failure_does_not_reextract_items(monkeypatch):
    bad_header = {"invoice_total": 100.0, "invoice_number": "", "invoice_date": "2026-08-08"}
    good_header = _valid_header()
    header_calls = _Sequenced([bad_header, good_header])
    items_calls = _Sequenced([[_valid_item()]])

    monkeypatch.setattr(reextraction, "extract_invoice_header", header_calls)
    monkeypatch.setattr(reextraction, "extract_line_items", items_calls)

    result = _run(reextraction.extract_and_validate(
        "INVOICE TEXT", EXPORT_SCHEMA, "export", max_retries=2,
    ))

    assert result["extraction_status"] == "validated"
    assert result["attempts"] == 2
    assert len(header_calls.calls) == 2
    assert len(items_calls.calls) == 1


def test_header_guardrails_are_applied_through_extract_and_validate(monkeypatch):
    bad_header = {
        "invoice_total": 100.0, "invoice_number": "INV-1", "invoice_date": "2026-08-08",
        "net_realisable_amount": 999.0,
    }
    header_calls = _Sequenced([bad_header])
    items_calls = _Sequenced([[_valid_item()]])

    monkeypatch.setattr(reextraction, "extract_invoice_header", header_calls)
    monkeypatch.setattr(reextraction, "extract_line_items", items_calls)

    invoice_text = "INVOICE TEXT with no mention of that concept at all"
    result = _run(reextraction.extract_and_validate(
        invoice_text, EXPORT_SCHEMA, "export", max_retries=2,
    ))

    assert result["header"]["net_realisable_amount"] == ""


def test_country_guardrail_does_not_rewrite_import_origin(monkeypatch):
    import_schema = load_template_schema(TEMPLATE_PATH["import"])
    header = {
        **_valid_header(),
        "supplier_address": "Liaison Office: 12 MG Road, Bengaluru, India",
        "country_of_origin": "CHINA", "country_of_destination": "INDIA",
    }
    monkeypatch.setattr(reextraction, "extract_invoice_header", _Sequenced([header]))
    monkeypatch.setattr(reextraction, "extract_line_items", _Sequenced([[_valid_item()]]))

    result = _run(reextraction.extract_and_validate(
        "INVOICE TEXT", import_schema, "import", max_retries=0,
    ))

    assert result["header"]["country_of_origin"] == "CHINA"


def test_ocr_variant_invoice_number_does_not_double_the_total_end_to_end(monkeypatch):
    header = {
        "invoice_number": "SI3626204603 / S13626204603", "invoice_date": "2026-08-26 / 2026-08-26",
        "invoice_total": "100.00 / 100.00", "currency": "USD",
    }
    monkeypatch.setattr(reextraction, "extract_invoice_header", _Sequenced([header]))
    monkeypatch.setattr(reextraction, "extract_line_items", _Sequenced([[_valid_item()]]))

    result = _run(reextraction.extract_and_validate("INVOICE TEXT", EXPORT_SCHEMA, "export", max_retries=0))

    assert result["extraction_status"] == "validated"
    assert result["header"]["invoice_number"] == "SI3626204603"
    assert any("collapsed" in w for w in result["warnings"])


def test_misread_line_total_is_repaired_from_the_source_text_end_to_end(monkeypatch):
    item = dict(_valid_item(), quantity=10, unit_price=10.0, line_total=90.0)
    monkeypatch.setattr(reextraction, "extract_invoice_header", _Sequenced([_valid_header()]))
    monkeypatch.setattr(reextraction, "extract_line_items", _Sequenced([[item]]))

    result = _run(reextraction.extract_and_validate(
        "10 PCS   10.00   100.00\nTotal 100.00", EXPORT_SCHEMA, "export", max_retries=0,
    ))

    assert result["extraction_status"] == "validated"
    assert result["sheets"]["ITEM"][0]["ItmTaxableVal"] == 100.0
    assert any("replaced with 100.00" in w for w in result["warnings"])


def _two_item_total_header():
    return dict(_valid_header(), invoice_total=200.0)


def test_incomplete_item_list_triggers_one_page_by_page_retry_that_fixes_it(monkeypatch):
    header_calls = _Sequenced([_two_item_total_header()])
    items_calls = _Sequenced([[_valid_item()], [_valid_item(), dict(_valid_item(), item_ser_no=2)]])
    monkeypatch.setattr(reextraction, "extract_invoice_header", header_calls)
    monkeypatch.setattr(reextraction, "extract_line_items", items_calls)

    result = _run(reextraction.extract_and_validate("TEXT", EXPORT_SCHEMA, "export", max_retries=2))

    assert result["extraction_status"] == "validated" and result["attempts"] == 2
    assert len(result["sheets"]["ITEM"]) == 2
    assert items_calls.calls[0].get("fine_chunks") is None
    assert items_calls.calls[1].get("fine_chunks") is True
    assert len(header_calls.calls) == 1


def test_completeness_retry_is_spent_only_once_and_never_replaces_a_better_list(monkeypatch):
    monkeypatch.setattr(reextraction, "extract_invoice_header", _Sequenced([_two_item_total_header()]))
    items_calls = _Sequenced([[_valid_item()], []])
    monkeypatch.setattr(reextraction, "extract_line_items", items_calls)

    result = _run(reextraction.extract_and_validate("TEXT", EXPORT_SCHEMA, "export", max_retries=2))

    assert result["extraction_status"] == "validation_failed"
    assert len(result["sheets"]["ITEM"]) == 1
    assert len(items_calls.calls) == 2
    assert any(e["rule"] == "LINE_ITEMS_INCOMPLETE" for e in result["validation"]["errors"])


def test_any_items_retry_never_replaces_a_list_with_one_further_from_the_printed_total(monkeypatch):
    wrong_qty = dict(_valid_item(), item_ser_no=2, quantity=3)
    first = [_valid_item(), wrong_qty]
    worse = [_valid_item()]
    items_calls = _Sequenced([first, worse])
    monkeypatch.setattr(reextraction, "extract_invoice_header", _Sequenced([_two_item_total_header()]))
    monkeypatch.setattr(reextraction, "extract_line_items", items_calls)

    result = _run(reextraction.extract_and_validate("TEXT", EXPORT_SCHEMA, "export", max_retries=1))

    assert len(items_calls.calls) == 2
    assert len(result["sheets"]["ITEM"]) == 2


def test_lines_over_the_printed_total_are_never_retried(monkeypatch):
    header_calls = _Sequenced([dict(_valid_header(), invoice_total=100.0)])
    other = dict(_valid_item(), item_ser_no=2, product_description="A DIFFERENT PRODUCT", quantity=5,
                 unit_price=20.0, line_total=100.0)
    items_calls = _Sequenced([[_valid_item(), other]])
    monkeypatch.setattr(reextraction, "extract_invoice_header", header_calls)
    monkeypatch.setattr(reextraction, "extract_line_items", items_calls)

    result = _run(reextraction.extract_and_validate("TEXT", EXPORT_SCHEMA, "export", max_retries=2))

    assert result["extraction_status"] == "validation_failed" and result["attempts"] == 1
    assert len(items_calls.calls) == 1 and len(header_calls.calls) == 1


_ROW_TEXT = "1 │ 160 │ 8481 80 90 │ 402/Y3499 A REAL DESCRIPTION │ 10 │ NOS │ 10.00 │ 100.00\n"


def test_item_number_in_the_hsn_field_is_repaired_from_the_source_row(monkeypatch):
    item = dict(_valid_item(), hsn_code="160", part_number="402/Y3499")
    monkeypatch.setattr(reextraction, "extract_invoice_header", _Sequenced([_valid_header()]))
    monkeypatch.setattr(reextraction, "extract_line_items", _Sequenced([[item]]))

    result = _run(reextraction.extract_and_validate(_ROW_TEXT, EXPORT_SCHEMA, "export", max_retries=0))

    assert result["extraction_status"] == "validated"
    assert result["sheets"]["ITEM"][0]["Item_RITC"] == "8481 80 90"
    assert any("hsn_code '160'" in w for w in result["warnings"])


def test_unrepairable_item_number_in_hsn_goes_back_to_the_model_with_a_clear_message(monkeypatch):
    bad = dict(_valid_item(), hsn_code="160")
    good = dict(_valid_item(), hsn_code="8481 80 90")
    items_calls = _Sequenced([[bad], [good]])
    monkeypatch.setattr(reextraction, "extract_invoice_header", _Sequenced([_valid_header()]))
    monkeypatch.setattr(reextraction, "extract_line_items", items_calls)

    result = _run(reextraction.extract_and_validate("NO ROW TEXT HERE", EXPORT_SCHEMA, "export", max_retries=2))

    assert result["extraction_status"] == "validated" and result["attempts"] == 2
    assert result["sheets"]["ITEM"][0]["Item_RITC"] == "8481 80 90"
    assert "HSN Code" in items_calls.calls[1]["feedback"]


def test_a_complete_list_with_a_repairable_wrong_column_amount_beats_a_short_list(monkeypatch):
    row1 = "1 │ 10 │ 8481 80 90 │ AAA-1111 WIDGET ASSEMBLY │ 2 │ NOS │ 25.00 │ 50.00 │ 50.00\n"
    row2 = "2 │ 20 │ 8481 80 90 │ BBB-2222 GADGET ASSEMBLY │ 4 │ NOS │ 12.50 │ 50.00 │ 50.00\n"
    text = row1 + row2
    first = [dict(_valid_item(), product_description="WIDGET ASSEMBLY", part_number="AAA-1111",
                  quantity=2, unit_price=25.0, line_total=50.0)]
    retry = [
        dict(first[0]),
        dict(_valid_item(), item_ser_no=2, product_description="GADGET ASSEMBLY", part_number="BBB-2222",
             quantity=4, unit_price=50.0, line_total=200.0),
    ]
    header = dict(_valid_header(), invoice_total=100.0)
    items_calls = _Sequenced([first, retry])
    monkeypatch.setattr(reextraction, "extract_invoice_header", _Sequenced([header]))
    monkeypatch.setattr(reextraction, "extract_line_items", items_calls)

    result = _run(reextraction.extract_and_validate(text, EXPORT_SCHEMA, "export", max_retries=2))

    assert result["extraction_status"] == "validated"
    rows = result["sheets"]["ITEM"]
    assert [(r["Item_Unit_Price"], r["ItmTaxableVal"]) for r in rows] == [(25.0, 50.0), (12.5, 50.0)]
    assert any("corrected to unit_price 12.5" in w for w in result["warnings"])


def test_a_stray_row_with_no_numbers_does_not_make_the_retry_look_worse(monkeypatch):
    first = [_valid_item()]
    stray = {"item_ser_no": None, "product_description": "STRAY FRAGMENT", "quantity": None, "unit_price": None, "line_total": None}
    retry = [_valid_item(), dict(_valid_item(), item_ser_no=2), stray]
    header = dict(_valid_header(), invoice_total=200.0)
    monkeypatch.setattr(reextraction, "extract_invoice_header", _Sequenced([header]))
    monkeypatch.setattr(reextraction, "extract_line_items", _Sequenced([first, retry]))

    result = _run(reextraction.extract_and_validate("TEXT", EXPORT_SCHEMA, "export", max_retries=2))

    assert result["extraction_status"] == "validated"
    assert len(result["sheets"]["ITEM"]) == 2
    assert any("discarded a row with no quantity, unit price or amount" in w for w in result["warnings"])


def test_notes_of_the_kept_list_survive_when_a_worse_retry_is_rejected(monkeypatch):
    row = "1 │ 10 │ 8481 80 90 │ AAA-1111 WIDGET ASSEMBLY │ 2 │ NOS │ 25.00 │ 50.00 │ 50.00\n"
    first = [dict(_valid_item(), product_description="WIDGET ASSEMBLY", part_number="AAA-1111",
                  quantity=2, unit_price=50.0, line_total=100.0, hsn_code="10")]
    header = dict(_valid_header(), invoice_total=300.0)
    monkeypatch.setattr(reextraction, "extract_invoice_header", _Sequenced([header]))
    monkeypatch.setattr(reextraction, "extract_line_items", _Sequenced([first, []]))

    result = _run(reextraction.extract_and_validate(row, EXPORT_SCHEMA, "export", max_retries=2))

    assert result["extraction_status"] == "validation_failed"
    assert any("hsn_code '10'" in w for w in result["warnings"])


def test_a_combined_pdf_splits_into_independently_validated_invoices(monkeypatch):
    combined_header = {
        "invoice_number": "INV-1 / INV-2",
        "invoice_date": "2026-01-01 / 2026-01-02",
        "invoice_total": "100.0 / 200.0",
        "supplier_name": "ACME CORP",
    }
    items = [
        dict(_valid_item(), invoice_ref="INV-1", line_total=100.0),
        dict(_valid_item(), invoice_ref="INV-2", quantity=10, unit_price=20.0, line_total=200.0),
    ]
    monkeypatch.setattr(reextraction, "extract_invoice_header", _Sequenced([combined_header]))
    monkeypatch.setattr(reextraction, "extract_line_items", _Sequenced([items]))

    result = _run(reextraction.extract_and_validate("TEXT", EXPORT_SCHEMA, "export", max_retries=2))

    assert "invoices" in result and "header" not in result and "sheets" not in result
    invoices = result["invoices"]
    assert len(invoices) == 2
    assert invoices[0]["header"]["invoice_number"] == "INV-1"
    assert invoices[1]["header"]["invoice_number"] == "INV-2"
    assert len(invoices[0]["sheets"]["ITEM"]) == 1 and len(invoices[1]["sheets"]["ITEM"]) == 1
    assert invoices[0]["sheets"]["ITEM"][0]["ItmTaxableVal"] == 100.0
    assert invoices[1]["sheets"]["ITEM"][0]["ItmTaxableVal"] == 200.0
    assert invoices[0]["validation"]["valid"] is True
    assert invoices[1]["validation"]["valid"] is True
    assert result["extraction_status"] == "validated"


def test_a_single_invoice_pdf_is_not_wrapped_in_an_invoices_list(monkeypatch):
    monkeypatch.setattr(reextraction, "extract_invoice_header", _Sequenced([_valid_header()]))
    monkeypatch.setattr(reextraction, "extract_line_items", _Sequenced([[_valid_item()]]))

    result = _run(reextraction.extract_and_validate("TEXT", EXPORT_SCHEMA, "export", max_retries=2))

    assert "invoices" not in result
    assert "header" in result and "sheets" in result
