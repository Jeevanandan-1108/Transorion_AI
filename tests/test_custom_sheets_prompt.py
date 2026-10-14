"""
Tests for the custom, user-authored sheets prompt (prompts/<shipment_type>
_sheets_prompt.txt) -- an optional override that replaces
llm_extract._build_line_items_prompt's dynamically-generated prompt
entirely for a shipment type, covering the item sheet AND every
supplementary sheet in ONE call. See llm_extract.has_custom_sheets_prompt /
extract_items_and_sheets_custom, and reextraction.py's/orchestrator.py's
use_custom_sheets branch.
"""
from __future__ import annotations

import asyncio

import pytest

import llm_extract
import reextraction
from schema import load_template_schema
from templates_config import TEMPLATE_PATH

EXPORT_SCHEMA = load_template_schema(TEMPLATE_PATH["export"])
_USAGE = {"input_tokens": 10, "cached_tokens": 0, "output_tokens": 10}


def _run(coro):
    return asyncio.run(coro)


# ── has_custom_sheets_prompt() / _load_custom_sheets_prompt() ───────────

def test_has_custom_sheets_prompt_true_for_export_and_import():
    # These files exist for real in prompts/ -- confirms the actual files
    # on disk are found, not just a mocked scenario.
    assert llm_extract.has_custom_sheets_prompt("export") is True
    assert llm_extract.has_custom_sheets_prompt("import") is True


def test_has_custom_sheets_prompt_false_for_an_unknown_shipment_type():
    assert llm_extract.has_custom_sheets_prompt("not_a_real_type") is False


def test_custom_prompt_contains_the_real_sheet_names():
    prompt = llm_extract._load_custom_sheets_prompt("export")
    assert "Sheet: ITEM" in prompt and "Sheet: License" in prompt and "Sheet: AR4" in prompt


# ── extract_items_and_sheets_custom() parsing ───────────────────────────

def test_extract_items_and_sheets_custom_splits_item_sheet_from_extra_sheets(monkeypatch):
    async def fake_call(client, request_kwargs):
        class _Choice:
            message = type("_M", (), {"content": (
                '{"ITEM": [{"Item_Ser_No": "1", "Item_Desc": "WIDGET", "Item_Qty": "5"}], '
                '"License": [{"Item No": "1", "License Category": "ADVANCE"}], '
                '"DRAWBACK": []}'
            )})()
        class _Resp:
            choices = [_Choice()]
            usage = None
        return _Resp()

    monkeypatch.setattr(llm_extract, "_call_line_items_model", fake_call)

    items, extra_sheets, usage = _run(llm_extract.extract_items_and_sheets_custom(
        "TEXT", "export", "ITEM",
    ))
    assert items == [{"item_ser_no": "1", "product_description": "WIDGET", "quantity": "5"}]
    assert extra_sheets["License"] == [{"Item No": "1", "License Category": "ADVANCE"}]
    # An empty sheet riding along as [] is harmless either way -- validate.py's
    # map_extra_sheets only keeps a sheet with at least one real row.
    assert extra_sheets.get("DRAWBACK", []) == []


def test_extract_items_and_sheets_custom_raises_for_a_type_with_no_custom_prompt():
    with pytest.raises(ValueError):
        _run(llm_extract.extract_items_and_sheets_custom("TEXT", "no_such_type", "ITEM"))


# ── wiring into reextraction.py ─────────────────────────────────────────

def _valid_header():
    return {"invoice_total": 100.0, "invoice_number": "INV-1", "invoice_date": "2026-08-08"}


def test_reextraction_uses_the_custom_path_when_available(monkeypatch):
    monkeypatch.setattr(reextraction, "has_custom_sheets_prompt", lambda shipment_type: True)

    header_called_with = {}

    async def fake_header(*args, **kwargs):
        header_called_with.update(kwargs)
        return _valid_header(), dict(_USAGE)

    custom_called_with = {}

    async def fake_custom(invoice_text, shipment_type, item_sheet_name, **kwargs):
        custom_called_with["shipment_type"] = shipment_type
        custom_called_with["item_sheet_name"] = item_sheet_name
        items = [{
            "item_ser_no": 1, "product_description": "WIDGET", "quantity": 10,
            "unit_of_measurement": "PCS", "unit_price": 10.0, "line_total": 100.0,
        }]
        extra_sheets = {"License": [{"Item No": 1, "License Category": "ADVANCE"}]}
        return items, extra_sheets, dict(_USAGE)

    monkeypatch.setattr(reextraction, "extract_invoice_header", fake_header)
    monkeypatch.setattr(reextraction, "extract_items_and_sheets_custom", fake_custom)

    def _boom(*args, **kwargs):
        raise AssertionError("extract_line_items should not be called in custom-prompt mode")
    monkeypatch.setattr(reextraction, "extract_line_items", _boom)

    result = _run(reextraction.extract_and_validate("TEXT", EXPORT_SCHEMA, "export", max_retries=0))

    # The header call did NOT get schema/item_sheet_name -- the custom
    # prompt's own call covers the extra sheets instead, so asking the
    # header pass about them too would just duplicate the question.
    assert "schema" not in header_called_with and "item_sheet_name" not in header_called_with
    assert custom_called_with == {"shipment_type": "export", "item_sheet_name": "ITEM"}
    assert result["sheets"]["License"][0]["License Category"] == "ADVANCE"
