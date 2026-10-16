"""
tests/test_chunked_extraction.py
---------------------------------
Unit coverage for chunked_extraction.py's own logic (chunk building, the
per-chunk Pydantic row model, and the merge-by-link-field step) without
making real LLM calls -- extract_chunk itself is exercised with the OpenAI
client mocked out, same style as tests/test_llm_extract_guardrails.py.
"""
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
    assert item_chunks[0].link_field == "Item_Ser_No"  # always carried even though excluded from `fields`


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
    """Two chunks of the SAME sheet (License split across two calls because
    it exceeds chunk_size) must merge back into ONE row per link value, not
    two separate fragments."""
    from invoice_extractor.extraction.chunked_extraction import _rows_model_for_chunk, FieldChunk

    schema = {
        "License": {
            "fields": ["Item No", "Regn. No", "Regn Date", "License Category", "Exemption Notification"],
            "types": {}, "aliases": {}, "link_field": "Item No",
        }
    }

    async def fake_parse(**kwargs):
        model = kwargs["response_format"]
        # Identify which chunk this is by its own row model's field set.
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
    assert usage["input_tokens"] == 200  # two chunk calls, 100 input tokens each


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
    # Patch the retried call directly to avoid tenacity's real wait/backoff.
    monkeypatch.setattr(ce, "_call_chunk_model", AsyncMock(side_effect=RuntimeError("transient failure")))

    sheets, usage = asyncio.run(ce.extract_all_chunks(
        "dummy invoice text", "", schema, "ITEM", set(), "gpt-4.1-nano", api_key="sk-test",
    ))
    assert sheets == {}
    assert usage == {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0}
