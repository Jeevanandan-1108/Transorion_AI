from __future__ import annotations

import logging
import os
import re
from typing import Any, TypedDict

from dotenv import load_dotenv

load_dotenv()

from langgraph.graph import StateGraph, END

from invoice_extractor.extraction.llm_extract import (
    extract_invoice_header,
    extract_line_items_chunked as extract_line_items,
    apply_header_guardrails,
    apply_line_item_guardrails,
    item_core_real_fields,
    _calculate_cost,
    _split_text_by_page,
    _split_combined_parts,
)
from invoice_extractor.extraction.chunked_extraction import extract_all_chunks
from invoice_extractor.extraction.reextraction import _carry_extra_template_fields, _is_closer_to_total
from invoice_extractor.validation.validate import validate_and_coerce
from invoice_extractor.validation.invoice_validator import (
    validate_invoice, build_retry_feedback, build_header_failed_part, ValidationResult,
)
from invoice_extractor.schema.templates_config import ITEM_SHEET_NAME
from invoice_extractor.extraction.multi_invoice import is_combined_document, split_header, split_items, combine_validations

logger = logging.getLogger("invoice_extractor")


TIER_MODELS: dict[str, str] = {
    "simple": os.environ.get("MODEL_TIER_SIMPLE", "gpt-5-nano"),
    "medium": os.environ.get("MODEL_TIER_MEDIUM", "gpt-6-luna"),
    "complex": os.environ.get("MODEL_TIER_COMPLEX", "gpt-4o-mini"),
}
TOP_TIER_MODEL = TIER_MODELS["complex"]

_TOKEN_DISCIPLINE_HINT = (
    "IMPORTANT: Minimize output tokens. Return ONLY the required JSON, as "
    "compact as valid JSON allows -- no explanations, no commentary before "
    "or after the JSON, no extra formatting beyond what JSON requires."
)


_ROW_START_RE = re.compile(r"^\s*\d{1,4}\s*[│|]", re.MULTILINE)

_COMPLEX_PAGES = 10
_COMPLEX_ROWS = 40
_COMPLEX_CHARS = 30_000
_COMPLEX_MARKER_DENSITY = 0.60

_MEDIUM_PAGES = 5
_MEDIUM_ROWS = 10
_MEDIUM_CHARS = 8_000
_MEDIUM_MARKER_DENSITY = 0.30

_MARKER_DENSITY_MIN_LINES = 15


def classify_complexity(invoice_text: str, tables_text: str = "") -> str:
    combined = invoice_text + "\n" + (tables_text or "")
    num_pages = len(_split_text_by_page(invoice_text))
    char_count = len(invoice_text)
    row_count = len(_ROW_START_RE.findall(combined))
    n_lines = combined.count("\n") or 1
    marker_density = combined.count("│") / n_lines if n_lines >= _MARKER_DENSITY_MIN_LINES else 0.0

    if (
        num_pages >= _COMPLEX_PAGES or row_count >= _COMPLEX_ROWS
        or char_count >= _COMPLEX_CHARS or marker_density >= _COMPLEX_MARKER_DENSITY
    ):
        return "complex"
    if (
        num_pages >= _MEDIUM_PAGES or row_count >= _MEDIUM_ROWS
        or char_count >= _MEDIUM_CHARS or marker_density >= _MEDIUM_MARKER_DENSITY
    ):
        return "medium"
    return "simple"


def _select_model(tier: str, attempt: int) -> str:
    if attempt == 1:
        return TIER_MODELS.get(tier, TIER_MODELS["simple"])
    return TOP_TIER_MODEL


def _empty_usage() -> dict[str, int]:
    return {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0}


def _add_usage(usage_by_model: dict[str, dict[str, int]], model: str, usage: dict[str, int]) -> None:
    bucket = usage_by_model.setdefault(model, _empty_usage())
    for k in bucket:
        bucket[k] += usage.get(k, 0)


def _combine_usage_costs(usage_by_model: dict[str, dict[str, int]]) -> dict[str, Any]:
    totals = {
        "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
        "total_tokens": 0, "cost_usd": 0.0, "cost_inr": 0.0,
    }
    if not usage_by_model:
        return totals
    for model, usage in usage_by_model.items():
        cost = _calculate_cost(model, usage["input_tokens"], usage["cached_tokens"], usage["output_tokens"])
        for k in totals:
            totals[k] += cost[k]
    totals["cost_usd"] = round(totals["cost_usd"], 6)
    totals["cost_inr"] = round(totals["cost_inr"], 4)
    return totals


class OrchestratorState(TypedDict, total=False):
    invoice_text: str
    tables_text: str
    schema: dict[str, Any]
    shipment_type: str
    api_key: str | None
    max_retries: int

    complexity: str
    attempt: int
    model: str
    models_used: list[str]

    header_raw: dict[str, Any]
    items_raw: list[dict[str, Any]]
    header_notes: list[str]
    item_notes: list[str]
    header_feedback: str | None
    items_feedback: str | None
    header_failed_part: dict[str, Any] | None
    fine_next: bool
    fine_tried: bool

    usage_by_model: dict[str, dict[str, int]]
    validation_history: list[dict[str, Any]]
    result: dict[str, Any]
    validation: ValidationResult
    warnings: list[str]
    extra_sheets_raw: dict[str, list[dict[str, Any]]]


async def _classify_node(state: OrchestratorState) -> dict[str, Any]:
    tier = classify_complexity(state["invoice_text"], state.get("tables_text", ""))
    logger.info("Orchestrator: classified complexity=%s -> model=%s", tier, TIER_MODELS[tier])
    return {
        "complexity": tier,
        "attempt": 0,
        "models_used": [],
        "header_raw": {}, "items_raw": [],
        "header_notes": [], "item_notes": [],
        "header_feedback": None, "items_feedback": None,
        "header_failed_part": None,
        "fine_next": False, "fine_tried": False,
        "usage_by_model": {},
        "validation_history": [],
        "result": {"header": {}, "sheets": {}},
        "warnings": [],
        "extra_sheets_raw": {},
    }


async def _extract_node(state: OrchestratorState) -> dict[str, Any]:
    attempt = state["attempt"] + 1
    model = _select_model(state["complexity"], attempt)
    header_feedback = state.get("header_feedback")
    items_feedback = state.get("items_feedback")
    need_header = attempt == 1 or header_feedback is not None
    need_items = attempt == 1 or items_feedback is not None

    header_raw = state.get("header_raw", {})
    items_raw = state.get("items_raw", [])
    header_notes = state.get("header_notes", [])
    item_notes = state.get("item_notes", [])
    usage_by_model = {k: dict(v) for k, v in state.get("usage_by_model", {}).items()}
    extra_sheets_raw = state.get("extra_sheets_raw", {})
    item_sheet_name = ITEM_SHEET_NAME[state["shipment_type"]]
    item_link_field = state["schema"].get(item_sheet_name, {}).get("link_field")

    if need_header:
        header_raw, h_usage = await extract_invoice_header(
            state["invoice_text"], tables_text=state.get("tables_text", ""),
            model=model, api_key=state.get("api_key"),
            feedback=header_feedback, previous_result=header_raw,
            failed_part=state.get("header_failed_part"),
            extra_instruction=_TOKEN_DISCIPLINE_HINT,
            schema=state["schema"], item_sheet_name=item_sheet_name,
            include_sheets_block=False,
        )
        _add_usage(usage_by_model, model, h_usage)
        header_raw.pop("extra_sheets", None)

    if need_items:
        previous_items = items_raw
        extra = {"fine_chunks": True} if state.get("fine_next") else {}
        known_numbers = _split_combined_parts(header_raw.get("invoice_number"))
        known_invoice_numbers = known_numbers if len(known_numbers) > 1 else None
        new_items, i_usage = await extract_line_items(
            state["invoice_text"], tables_text=state.get("tables_text", ""),
            model=model, api_key=state.get("api_key"),
            feedback=items_feedback, previous_result=previous_items,
            extra_instruction=_TOKEN_DISCIPLINE_HINT,
            schema=state["schema"], shipment_type=state["shipment_type"],
            item_sheet_name=item_sheet_name,
            known_invoice_numbers=known_invoice_numbers,
            include_extra_fields=False,
            **extra,
        )
        _add_usage(usage_by_model, model, i_usage)
        new_item_notes = apply_line_item_guardrails(new_items, state["invoice_text"])
        if attempt > 1 and not _is_closer_to_total(new_items, previous_items, header_raw):
            logger.warning(
                "Orchestrator: items re-extraction did not bring the line items "
                "closer to the printed total -- keeping the previous item list."
            )
        else:
            _carry_extra_template_fields(new_items, previous_items, item_link_field)
            items_raw, item_notes = new_items, new_item_notes

    if attempt == 1:
        chunk_sheets, chunk_usage = await extract_all_chunks(
            state["invoice_text"], state.get("tables_text", ""),
            state["schema"], item_sheet_name,
            item_core_real_fields(state["shipment_type"]),
            model, api_key=state.get("api_key"),
            shipment_type=state["shipment_type"],
        )
        _add_usage(usage_by_model, model, chunk_usage)

        item_chunk_rows = chunk_sheets.pop(item_sheet_name, [])
        if item_link_field and item_chunk_rows:
            by_link = {str(r.get(item_link_field)): r for r in item_chunk_rows if r.get(item_link_field) is not None}
            for it in items_raw:
                extra_row = by_link.get(str(it.get("item_ser_no")))
                if not extra_row:
                    continue
                bucket = it.setdefault("_extra_template_fields", {})
                bucket.update({k: v for k, v in extra_row.items() if k != item_link_field})
        elif item_chunk_rows:
            if len(item_chunk_rows) != len(items_raw):
                logger.warning(
                    "Orchestrator: item sheet '%s' has no link field -- positional merge expected "
                    "%d row(s) (matching items_raw) but chunked extraction returned %d; merging "
                    "only the first %d.",
                    item_sheet_name, len(items_raw), len(item_chunk_rows),
                    min(len(items_raw), len(item_chunk_rows)),
                )
            for it, extra_row in zip(items_raw, item_chunk_rows):
                bucket = it.setdefault("_extra_template_fields", {})
                bucket.update(extra_row)

        extra_sheets_raw = {**extra_sheets_raw, **chunk_sheets}

    if need_header:
        header_notes = []
    if need_header or need_items:
        new_notes: list[str] = []
        apply_header_guardrails(
            header_raw, items_raw, state["invoice_text"],
            shipment_type=state["shipment_type"], notes=new_notes,
        )
        header_notes = header_notes + [n for n in new_notes if n not in header_notes]

    if is_combined_document(header_raw):
        split_headers = split_header(header_raw)
        invoice_numbers = [h["invoice_number"] for h in split_headers]
        item_groups = split_items(items_raw, invoice_numbers)

        invoices: list[dict[str, Any]] = []
        sub_validations: list[ValidationResult] = []
        for h, grp in zip(split_headers, item_groups):
            sub_raw = {"invoice_header": h, "line_items": grp}
            sub_sheets, sub_warnings, sub_header = validate_and_coerce(
                sub_raw, state["schema"], state["shipment_type"], raw_extra_sheets=extra_sheets_raw,
            )
            sub_result = {"header": sub_header, "sheets": sub_sheets}
            sub_validation = validate_invoice(sub_result, state["invoice_text"], state["shipment_type"])
            sub_validations.append(sub_validation)
            invoices.append({
                "header": sub_header, "sheets": sub_sheets,
                "warnings": header_notes + item_notes + sub_warnings,
                "validation": sub_validation.to_dict(),
            })
        result = {"invoices": invoices}
        warnings = header_notes + item_notes
        validation = combine_validations(sub_validations)
    else:
        raw = {"invoice_header": header_raw, "line_items": items_raw}
        sheets, warnings, header = validate_and_coerce(
            raw, state["schema"], state["shipment_type"], raw_extra_sheets=extra_sheets_raw,
        )
        warnings = header_notes + item_notes + warnings
        result = {"header": header, "sheets": sheets}
        validation = validate_invoice(result, state["invoice_text"], state["shipment_type"])

    validation_history = state.get("validation_history", []) + [{
        "attempt": attempt, "model": model, "valid": validation.valid,
        "errors": [c.to_dict() for c in validation.errors],
    }]
    models_used = state.get("models_used", []) + [model]

    return {
        "attempt": attempt, "model": model, "models_used": models_used,
        "header_raw": header_raw, "items_raw": items_raw,
        "header_notes": header_notes, "item_notes": item_notes,
        "usage_by_model": usage_by_model,
        "result": result, "validation": validation,
        "validation_history": validation_history,
        "warnings": warnings,
        "fine_next": False,
        "extra_sheets_raw": extra_sheets_raw,
    }


def _fb_validation_dropping_fine(validation: ValidationResult, fine_tried: bool) -> ValidationResult:
    if not fine_tried:
        return validation
    return ValidationResult(
        valid=validation.valid, status=validation.status,
        checks=[c for c in validation.checks if c.rule != "LINE_ITEMS_INCOMPLETE"],
    )


def _route_after_extract(state: OrchestratorState) -> str:
    validation = state["validation"]
    if validation.valid or state["attempt"] > state["max_retries"]:
        return "end"
    fb_validation = _fb_validation_dropping_fine(validation, state.get("fine_tried", False))
    header_feedback, items_feedback = build_retry_feedback(fb_validation)
    if header_feedback is None and items_feedback is None:
        return "end"
    return "retry"


async def _prepare_retry_node(state: OrchestratorState) -> dict[str, Any]:
    validation = state["validation"]
    fine_tried = state.get("fine_tried", False)
    fb_validation = _fb_validation_dropping_fine(validation, fine_tried)
    header_feedback, items_feedback = build_retry_feedback(fb_validation)
    fine_next = not fine_tried and any(
        c.rule == "LINE_ITEMS_INCOMPLETE" for c in validation.retryable_errors
    )
    logger.warning(
        "Orchestrator: validation failed on attempt %d (%d error(s)) -- escalating to "
        "%s for retry (%s).",
        state["attempt"], len(validation.errors), TOP_TIER_MODEL,
        "header+items" if header_feedback and items_feedback
        else "header only" if header_feedback else "items only",
    )
    return {
        "header_feedback": header_feedback,
        "items_feedback": items_feedback,
        "header_failed_part": build_header_failed_part(fb_validation),
        "fine_next": fine_next,
        "fine_tried": fine_tried or fine_next,
    }


def _build_graph():
    graph = StateGraph(OrchestratorState)
    graph.add_node("classify", _classify_node)
    graph.add_node("extract", _extract_node)
    graph.add_node("prepare_retry", _prepare_retry_node)

    graph.set_entry_point("classify")
    graph.add_edge("classify", "extract")
    graph.add_conditional_edges("extract", _route_after_extract, {"end": END, "retry": "prepare_retry"})
    graph.add_edge("prepare_retry", "extract")
    return graph.compile()


_GRAPH = _build_graph()


async def extract_and_validate_orchestrated(
    invoice_text: str,
    schema: dict[str, Any],
    shipment_type: str,
    tables_text: str = "",
    api_key: str | None = None,
    max_retries: int = 1,
) -> dict[str, Any]:
    init_state: OrchestratorState = {
        "invoice_text": invoice_text, "tables_text": tables_text,
        "schema": schema, "shipment_type": shipment_type,
        "api_key": api_key, "max_retries": max_retries,
    }
    final_state: OrchestratorState = await _GRAPH.ainvoke(init_state)

    validation: ValidationResult = final_state["validation"]
    token_usage = _combine_usage_costs(final_state["usage_by_model"])
    result = final_state["result"]
    shape = {"invoices": result["invoices"]} if "invoices" in result else {
        "header": result["header"], "sheets": result["sheets"],
    }

    return {
        **shape,
        "warnings": final_state.get("warnings", []),
        "token_usage": token_usage,
        "validation": validation.to_dict(),
        "validation_history": final_state["validation_history"],
        "attempts": final_state["attempt"],
        "extraction_status": "validated" if validation.valid else "validation_failed",
        "complexity": final_state["complexity"],
        "models_used": final_state["models_used"],
    }
