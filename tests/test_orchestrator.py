"""
tests/test_orchestrator.py
---------------------------
Unit coverage for orchestrator.py's own control-flow logic (complexity
classification, model selection, and the extract -> validate -> retry
graph), with every LLM call mocked out -- same style as
tests/test_chunked_extraction.py.

This file exists specifically because orchestrator.py had NO dedicated
tests at all before this, despite being the most complex state machine in
the pipeline and the exact place two real production bugs were found and
fixed: (1) a grouped chunked-extraction call diluting the item sheet's
"must return one row per item" instruction with other sheets' "default to
empty" framing, and (2) a validation-failure retry silently discarding the
chunked item-sheet long-tail data because it replaced `items_raw` wholesale
without carrying `_extra_template_fields` forward. The regression tests
below guard specifically against (2) recurring -- (1) already has its own
coverage in test_chunked_extraction.py.
"""
from __future__ import annotations

import asyncio

import pytest

from invoice_extractor.extraction import orchestrator as orch
from invoice_extractor.validation.invoice_validator import ValidationCheck, ValidationResult


# ── classify_complexity ──────────────────────────────────────────────────

def test_classify_complexity_short_plain_text_is_simple():
    assert orch.classify_complexity("A short one-page invoice with a few lines.") == "simple"


def test_classify_complexity_many_pages_is_complex():
    text = "\n".join(f"=== PAGE {i} OF 12 ===\nsome line" for i in range(12))
    assert orch.classify_complexity(text) == "complex"


def test_classify_complexity_many_item_rows_is_complex():
    rows = "\n".join(f"{i}│ widget │ 1 │ 2.00" for i in range(1, 45))
    assert orch.classify_complexity(rows) == "complex"


def test_classify_complexity_moderate_row_count_is_medium():
    rows = "\n".join(f"{i}│ widget │ 1 │ 2.00" for i in range(1, 12))
    assert orch.classify_complexity(rows) == "medium"


def test_classify_complexity_high_char_volume_alone_is_complex():
    assert orch.classify_complexity("x" * 30_001) == "complex"


# ── _select_model ────────────────────────────────────────────────────────

def test_select_model_attempt_1_uses_complexity_tier():
    assert orch._select_model("simple", 1) == orch.TIER_MODELS["simple"]
    assert orch._select_model("medium", 1) == orch.TIER_MODELS["medium"]
    assert orch._select_model("complex", 1) == orch.TIER_MODELS["complex"]


def test_select_model_any_retry_escalates_to_top_tier_regardless_of_attempt_1_tier():
    """Per spec: a retry is always the BEST model, never a gradual climb --
    even a "simple"-classified document's retry jumps straight to the top
    tier, same as a "complex" one's would."""
    assert orch._select_model("simple", 2) == orch.TOP_TIER_MODEL
    assert orch._select_model("complex", 3) == orch.TOP_TIER_MODEL


# ── _add_usage / _combine_usage_costs ───────────────────────────────────

def test_add_usage_accumulates_across_multiple_calls_to_the_same_model():
    usage_by_model: dict = {}
    orch._add_usage(usage_by_model, "gpt-4o-mini", {"input_tokens": 100, "cached_tokens": 10, "output_tokens": 20})
    orch._add_usage(usage_by_model, "gpt-4o-mini", {"input_tokens": 50, "cached_tokens": 0, "output_tokens": 5})
    assert usage_by_model["gpt-4o-mini"] == {"input_tokens": 150, "cached_tokens": 10, "output_tokens": 25}


def test_combine_usage_costs_empty_input_is_all_zeros():
    totals = orch._combine_usage_costs({})
    assert totals["total_tokens"] == 0
    assert totals["cost_usd"] == 0.0


# ── Full graph: extract -> validate -> retry, with every LLM call mocked ──

_SCHEMA = {
    "ITEM": {
        "fields": ["Ser_No", "Description", "LONGTAIL_COL"],
        "types": {"Ser_No": "string", "Description": "string", "LONGTAIL_COL": "string"},
        "aliases": {},
        "link_field": None,  # position-based merge, same shape as the real bug (no link column)
    },
}
_SHIPMENT_TYPE = "OrchestratorTestTemplate"  # no hand-tuned mapper -- exercises _map_generic_item,
                                              # the exact path both real bugs lived in


@pytest.fixture(autouse=True)
def _register_test_template(monkeypatch):
    monkeypatch.setitem(orch.ITEM_SHEET_NAME, _SHIPMENT_TYPE, "ITEM")


def _header_usage():
    return {"input_tokens": 10, "cached_tokens": 0, "output_tokens": 5}


def _run_orchestrated(monkeypatch, validations, chunk_sheets_by_call, items_by_call):
    """Wires up mocked header/items/chunk extraction calls and a scripted
    sequence of validate_invoice results (one per attempt), then runs the
    real graph end to end."""
    header_calls = {"n": 0}
    items_calls = {"n": 0}
    chunk_calls = {"n": 0}
    validate_calls = {"n": 0}

    async def fake_header(*args, **kwargs):
        header_calls["n"] += 1
        return {"invoice_number": "INV-1", "invoice_total": 100.0}, _header_usage()

    async def fake_items(*args, **kwargs):
        idx = items_calls["n"]
        items_calls["n"] += 1
        return [dict(it) for it in items_by_call[min(idx, len(items_by_call) - 1)]], _header_usage()

    async def fake_chunks(*args, **kwargs):
        idx = chunk_calls["n"]
        chunk_calls["n"] += 1
        return {k: [dict(r) for r in v] for k, v in chunk_sheets_by_call[idx].items()}, _header_usage()

    def fake_validate_invoice(result, invoice_text, shipment_type):
        idx = min(validate_calls["n"], len(validations) - 1)
        validate_calls["n"] += 1
        return validations[idx]

    monkeypatch.setattr(orch, "extract_invoice_header", fake_header)
    monkeypatch.setattr(orch, "extract_line_items", fake_items)
    monkeypatch.setattr(orch, "extract_all_chunks", fake_chunks)
    monkeypatch.setattr(orch, "validate_invoice", fake_validate_invoice)

    result = asyncio.run(orch.extract_and_validate_orchestrated(
        "dummy invoice text", _SCHEMA, _SHIPMENT_TYPE, max_retries=1,
    ))
    return result, header_calls, items_calls, chunk_calls, validate_calls


def _valid_result():
    return ValidationResult(valid=True, status="VALID", checks=[])


def _invalid_items_result():
    """One retryable, item-level error -- same shape as a real
    QTY_X_UNIT_PRICE failure: routes to items_feedback only (header_feedback
    stays None), so the orchestrator escalates to an "items only" retry,
    exactly like the real run that exposed the carry-forward bug."""
    return ValidationResult(valid=False, status="INVALID", checks=[
        ValidationCheck(rule="QTY_X_UNIT_PRICE", status="ERROR", item=1, retryable=True,
                         message="qty x price mismatch"),
    ])


def test_single_valid_attempt_chunk_data_reaches_final_sheet(monkeypatch):
    """Baseline, no retry: chunked long-tail ITEM data must land in the
    final output sheet on the first attempt."""
    result, header_calls, items_calls, chunk_calls, _ = _run_orchestrated(
        monkeypatch,
        validations=[_valid_result()],
        chunk_sheets_by_call=[{"ITEM": [{"LONGTAIL_COL": "from-chunking"}]}],
        items_by_call=[[{"item_ser_no": "1", "product_description": "WIDGET"}]],
    )
    assert header_calls["n"] == 1
    assert items_calls["n"] == 1
    assert chunk_calls["n"] == 1
    assert result["attempts"] == 1
    assert result["sheets"]["ITEM"][0]["LONGTAIL_COL"] == "from-chunking"


def test_retry_carries_forward_chunked_item_data_instead_of_dropping_it(monkeypatch):
    """Regression test for the real bug fixed this session: attempt 1's
    chunked extraction found the item sheet's long-tail column correctly,
    but the validation-failure retry on attempt 2 replaced items_raw with a
    brand-new list from a fresh extract_line_items() call -- which has no
    _extra_template_fields at all, since chunked extraction only ever runs
    `if attempt == 1`. Without carrying that data forward, the final
    output's ITEM sheet came back with the long-tail column empty even
    though it had been found correctly just one attempt earlier."""
    result, header_calls, items_calls, chunk_calls, validate_calls = _run_orchestrated(
        monkeypatch,
        validations=[_invalid_items_result(), _valid_result()],
        # extract_all_chunks only ever runs on attempt 1 -- only one entry needed.
        chunk_sheets_by_call=[{"ITEM": [{"LONGTAIL_COL": "from-chunking"}]}],
        items_by_call=[
            [{"item_ser_no": "1", "product_description": "WIDGET"}],          # attempt 1
            [{"item_ser_no": "1", "product_description": "WIDGET-CORRECTED"}],  # attempt 2 (fresh dicts)
        ],
    )
    assert header_calls["n"] == 1  # items-only retry (header_feedback stays None) -- header pass not re-run
    assert items_calls["n"] == 2
    assert chunk_calls["n"] == 1  # never re-runs on retry
    assert validate_calls["n"] == 2
    assert result["attempts"] == 2
    # The whole point: attempt 2's brand-new item list still carries the
    # long-tail column that only attempt 1's chunked call ever found.
    assert result["sheets"]["ITEM"][0]["LONGTAIL_COL"] == "from-chunking"


def test_non_retryable_failure_stops_without_a_second_attempt(monkeypatch):
    """A validation failure where build_retry_feedback has nothing to act
    on (no retryable errors) must stop immediately, not loop forever."""
    non_retryable = ValidationResult(valid=False, status="INVALID", checks=[
        ValidationCheck(rule="SOME_NON_RETRYABLE_RULE", status="ERROR", item=1, retryable=False,
                         message="not actionable"),
    ])
    result, header_calls, items_calls, chunk_calls, validate_calls = _run_orchestrated(
        monkeypatch,
        validations=[non_retryable],
        chunk_sheets_by_call=[{"ITEM": [{"LONGTAIL_COL": "from-chunking"}]}],
        items_by_call=[[{"item_ser_no": "1", "product_description": "WIDGET"}]],
    )
    assert items_calls["n"] == 1
    assert chunk_calls["n"] == 1
    assert validate_calls["n"] == 1
    assert result["attempts"] == 1
    assert result["extraction_status"] == "validation_failed"
