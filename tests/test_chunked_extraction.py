from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from invoice_extractor.extraction.chunked_extraction import (
    FieldChunk,
    build_field_chunks,
    _row_model_for_chunk,
    extract_all_chunks,
)


_SCHEMA = {
    "ITEM": {
        "fields": ["Item_Ser_No", "Item_Desc", "Item_Scheme_Code", "Item_STR"],
        "types": {"Item_Ser_No": "string", "Item_Desc": "string",
                   "Item_Scheme_Code": "string", "Item_STR": "string"},
        "aliases": {"Item_Scheme_Code": ["Scheme Code"]},
        "link_field": "Item_Ser_No",
    },
    "License": {
        "fields": ["Item No", "Regn. No", "Regn Date"],
        "types": {"Item No": "string", "Regn. No": "string", "Regn Date": "date"},
        "aliases": {},
        "link_field": "Item No",
    },
}


def test_build_field_chunks_excludes_core_item_fields():
    chunks = build_field_chunks(_SCHEMA, exclude={"ITEM": {"Item_Ser_No", "Item_Desc"}}, chunk_size=10)
    item_chunks = [c for c in chunks if c.sheet == "ITEM"]
    assert len(item_chunks) == 1
    assert item_chunks[0].fields == ("Item_Scheme_Code", "Item_STR")
    assert item_chunks[0].link_field == "Item_Ser_No"


def test_build_field_chunks_splits_large_sheets():
    schema = {
        "BIG": {
            "fields": [f"F{i}" for i in range(25)],
            "types": {}, "aliases": {}, "link_field": None,
        }
    }
    chunks = build_field_chunks(schema, chunk_size=10)
    assert [len(c.fields) for c in chunks] == [10, 10, 5]


def test_build_field_chunks_skips_sheet_with_nothing_left():
    chunks = build_field_chunks(_SCHEMA, exclude={"ITEM": {"Item_Ser_No", "Item_Desc", "Item_Scheme_Code", "Item_STR"}})
    assert all(c.sheet != "ITEM" for c in chunks)


def test_group_chunk_indices_never_mixes_item_sheet_with_other_sheets():
    """Confirmed real failure without this: a grouped call's prompt tells
    the model "most sections here default to empty" -- true for auxiliary
    sheets sharing that call, but not the item sheet (which must return one
    row per line item). Bundled together, the model answered every
    item-sheet section empty too, for a real invoice with real line items.
    The item sheet's own chunks must always land in one dedicated group,
    never sharing a call with any other sheet."""
    from invoice_extractor.extraction.chunked_extraction import _group_chunk_indices

    schema = {
        "ITEM": {"fields": [f"Item_F{i}" for i in range(20)], "types": {}, "aliases": {}, "link_field": None},
        "License": {"fields": [f"Lic_F{i}" for i in range(8)], "types": {}, "aliases": {}, "link_field": None},
        "AR4": {"fields": [f"Ar4_F{i}" for i in range(8)], "types": {}, "aliases": {}, "link_field": None},
        "Drawback": {"fields": [f"Dbk_F{i}" for i in range(8)], "types": {}, "aliases": {}, "link_field": None},
    }
    chunks = build_field_chunks(schema, chunk_size=10, item_sheet_name="ITEM")
    groups = _group_chunk_indices(chunks, max_groups=3, item_sheet_name="ITEM")

    for group in groups:
        sheets_in_group = {chunks[i].sheet for i in group}
        assert not ("ITEM" in sheets_in_group and len(sheets_in_group) > 1), (
            f"item sheet got mixed into a group with other sheets: {sheets_in_group}"
        )
    # every ITEM chunk lands in exactly one, single dedicated group
    item_groups = [g for g in groups if any(chunks[i].sheet == "ITEM" for i in g)]
    assert len(item_groups) == 1
    assert {chunks[i].sheet for i in item_groups[0]} == {"ITEM"}


def test_group_chunk_indices_falls_back_to_plain_bin_pack_with_no_item_sheet():
    """No item_sheet_name given (or it has no chunks here) -- behaves exactly
    like the old single-pass bin-pack, nothing special reserved."""
    from invoice_extractor.extraction.chunked_extraction import _group_chunk_indices

    schema = {
        "License": {"fields": [f"Lic_F{i}" for i in range(8)], "types": {}, "aliases": {}, "link_field": None},
        "AR4": {"fields": [f"Ar4_F{i}" for i in range(8)], "types": {}, "aliases": {}, "link_field": None},
    }
    chunks = build_field_chunks(schema, chunk_size=10)
    groups = _group_chunk_indices(chunks, max_groups=1, item_sheet_name=None)
    assert len(groups) == 1
    assert len(groups[0]) == len(chunks)


def test_multi_row_chunk_prompt_warns_about_combined_invoices():
    """Confirmed real failure without this: the multi_row prompt is written
    for exactly ONE item table -- a combined document (several invoices
    bundled in one PDF) gave the model no idea what to do with 2+ SEPARATE
    tables, and it answered every row empty for all of them. The prompt
    must explicitly say so when the caller knows there's more than one."""
    from invoice_extractor.extraction.chunked_extraction import _build_chunk_prompt, _group_section_text

    multi_row_chunk = FieldChunk(
        sheet="ITEMS", link_field=None, fields=("Product Desc",),
        aliases={}, types={}, index=0, multi_row=True,
    )
    single_row_chunk = FieldChunk(
        sheet="License", link_field="Item No", fields=("Regn. No",),
        aliases={}, types={}, index=0, multi_row=False,
    )

    # No known_invoice_numbers (or only one) -- no note added, same as before this existed.
    assert "SEPARATE invoices" not in _build_chunk_prompt(multi_row_chunk)
    assert "SEPARATE invoices" not in _build_chunk_prompt(multi_row_chunk, known_invoice_numbers=["INV-1"])
    assert "SEPARATE invoices" not in _group_section_text(multi_row_chunk)

    # 2+ known invoices -- the note appears, naming them, only for the multi_row chunk.
    prompt = _build_chunk_prompt(multi_row_chunk, known_invoice_numbers=["INV-1", "INV-2", "INV-3"])
    assert "3 SEPARATE invoices (INV-1, INV-2, INV-3)" in prompt

    section = _group_section_text(multi_row_chunk, known_invoice_numbers=["INV-1", "INV-2"])
    assert "2 SEPARATE invoices (INV-1, INV-2)" in section

    # A non-multi_row chunk (e.g. a normal auxiliary sheet) never gets this note --
    # it has nothing to do with the item table.
    assert "SEPARATE invoices" not in _build_chunk_prompt(single_row_chunk, known_invoice_numbers=["INV-1", "INV-2"])
    assert "SEPARATE invoices" not in _group_section_text(single_row_chunk, known_invoice_numbers=["INV-1", "INV-2"])


def test_row_model_includes_link_field_and_aliases_as_description():
    chunk = FieldChunk(
        sheet="License", link_field="Item No", fields=("Regn. No",),
        aliases={}, types={"Regn. No": "string"}, index=0,
    )
    model = _row_model_for_chunk(chunk)
    assert "Item No" in model.model_fields
    assert "Regn. No" in model.model_fields


def _mock_response(rows_payload, parsed_model):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(parsed=parsed_model(**rows_payload)))]
    resp.usage = MagicMock(prompt_tokens=100, completion_tokens=20, prompt_tokens_details=None)
    return resp


def test_extract_all_chunks_merges_same_sheet_chunks_by_link_field(monkeypatch):
    from invoice_extractor.extraction.chunked_extraction import _rows_model_for_chunk, FieldChunk

    schema = {
        "License": {
            "fields": ["Item No", "Regn. No", "Regn Date", "License Category", "Exemption Notification"],
            "types": {}, "aliases": {}, "link_field": "Item No",
        }
    }

    async def fake_parse(**kwargs):
        model = kwargs["response_format"]
        if "Regn. No" in model.model_fields["rows"].annotation.__args__[0].model_fields:
            payload = {"rows": [{"Item No": "1", "Regn. No": "REG-1"}]}
        else:
            payload = {"rows": [{"Item No": "1", "License Category": "ADVANCE"}]}
        return _mock_response(payload, model)

    mock_client = MagicMock()
    mock_client.chat.completions.parse = AsyncMock(side_effect=fake_parse)
    monkeypatch.setattr("invoice_extractor.extraction.chunked_extraction.AsyncOpenAI", lambda **kw: mock_client)

    sheets, usage = asyncio.run(extract_all_chunks(
        "dummy invoice text", "", schema, "ITEM", set(), "gpt-4.1-nano",
        api_key="sk-test", chunk_size=2,
    ))

    assert sheets["License"] == [{"Item No": "1", "Regn. No": "REG-1", "License Category": "ADVANCE"}]
    assert usage["input_tokens"] == 200


def test_extract_all_chunks_skips_failed_chunk_without_failing_others(monkeypatch):
    schema = {
        "License": {
            "fields": ["Item No", "Regn. No"],
            "types": {}, "aliases": {}, "link_field": "Item No",
        }
    }

    import invoice_extractor.extraction.chunked_extraction as ce

    mock_client = MagicMock()
    monkeypatch.setattr(ce, "AsyncOpenAI", lambda **kw: mock_client)
    monkeypatch.setattr(ce, "_call_chunk_model", AsyncMock(side_effect=RuntimeError("transient failure")))

    sheets, usage = asyncio.run(ce.extract_all_chunks(
        "dummy invoice text", "", schema, "ITEM", set(), "gpt-4.1-nano", api_key="sk-test",
    ))
    assert sheets == {}
    assert usage == {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0}
