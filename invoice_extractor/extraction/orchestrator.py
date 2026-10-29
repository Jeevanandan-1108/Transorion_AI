"""
orchestrator.py
----------------
LangGraph-based orchestrator wrapping the extract -> validate -> retry loop.

reextraction.py already owns that retry POLICY (targeted per-pass retry,
the "rows went missing" fine_chunks retry, guardrails, non-retryable rules
never looping) against a single, caller-supplied model. This module ports
the same control flow into a LangGraph StateGraph (classify -> extract ->
[decide: done | escalate+retry]) and adds one more axis on top: WHICH model
runs each attempt, chosen automatically instead of fixed by the caller.

What this adds over reextraction.extract_and_validate:
  - Model selection by PDF complexity (see classify_complexity): a short,
    clean 1-2 page invoice is cheap to get right on the smallest tier; a
    dense multi-page table with heavy layout noise is worth paying for a
    stronger model up front rather than discovering the cheap tier got it
    wrong only after spending a whole retry round-trip on it.
  - Escalation on retry: a validation failure always re-runs on
    TOP_TIER_MODEL, never the tier that just failed -- carrying forward
    both the human-readable retry feedback (build_retry_feedback, unchanged
    from reextraction.py) and a structured `failed_part` (the specific
    header fields that failed; see invoice_validator.build_header_failed_part).
  - Every call this module makes appends a token-discipline instruction
    (see _TOKEN_DISCIPLINE_HINT) asking for compact JSON output with no
    commentary -- complexity tiering already keeps the common case on the
    cheapest model; this keeps every tier's own output lean too.
  - Both core calls this module makes are template-driven (see
    llm_extract.py's _build_header_prompt / _build_line_items_prompt): the
    items call asks for the template's own real column names directly
    instead of a fixed generic vocabulary.
  - Every other template column -- the item sheet's own long tail beyond
    its ~11-15 core mapped fields, plus every auxiliary sheet (License,
    AR4, Drawback, Job-Work, ...) that used to always come back []
    regardless of what the source document actually printed -- is handled
    by chunked_extraction.extract_all_chunks on attempt 1: many small,
    focused, Pydantic-grounded calls run concurrently (one per <=12-field
    chunk) instead of one overloaded call asking about ~150 fields at
    once. See that module's docstring for why the old one-call-covers-
    everything shape (both the dynamic append-block version and the even
    larger prompts/<type>_sheets_prompt.txt custom-prompt version) was the
    confirmed source of low accuracy.

retry-attempt budget (`max_retries`) and every other policy detail are
unchanged from reextraction.py -- this is a parallel entry point with the
SAME return shape (see extract_and_validate_orchestrated), not a
replacement; callers choose which one to use.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, TypedDict

from dotenv import load_dotenv

# This module reads MODEL_TIER_*/CHUNK_SIZE/etc. from os.environ at import
# time (see TIER_MODELS below) -- load .env here directly rather than
# relying on some other module (llm_extract.py also calls this) having
# already done it earlier in whatever import chain happened to run first.
# Idempotent: safe to call even if .env was already loaded.
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
from invoice_extractor.extraction.reextraction import _is_closer_to_total
from invoice_extractor.validation.validate import validate_and_coerce
from invoice_extractor.validation.invoice_validator import (
    validate_invoice, build_retry_feedback, build_header_failed_part, ValidationResult,
)
from invoice_extractor.schema.templates_config import ITEM_SHEET_NAME
from invoice_extractor.extraction.multi_invoice import is_combined_document, split_header, split_items, combine_validations

logger = logging.getLogger("invoice_extractor")


# ── Model tiers ─────────────────────────────────────────────────────────
#
# Attempt 1 uses the tier classify_complexity() picks for this document;
# every retry escalates straight to TOP_TIER_MODEL regardless of which
# tier attempt 1 used (per spec: "attempt 2 is the best model, with the
# previous mistakes included" -- not a gradual one-tier-at-a-time climb).
#
# Overridable via .env (MODEL_TIER_SIMPLE / MODEL_TIER_MEDIUM /
# MODEL_TIER_COMPLEX) -- see .env.example. Change a tier's model there
# instead of editing this file; no restart-the-code, just restart-the-
# process. "complex" doubles as TOP_TIER_MODEL (every retry escalates
# straight there, see classify_complexity's own docstring).
#
# One thing env-overriding does NOT update: llm_extract.py's
# _MODEL_PRICING dict, which is keyed by these exact model name strings
# for cost-estimate purposes. Point a tier at a model _MODEL_PRICING
# doesn't recognize and extraction still works fine -- only the
# token_usage cost figures silently fall back to a different model's
# pricing as an estimate (see _calculate_cost's own fallback). Add a
# matching entry there if you need accurate cost tracking for the new model.
TIER_MODELS: dict[str, str] = {
    "simple": os.environ.get("MODEL_TIER_SIMPLE", "gpt-5-nano"),
    "medium": os.environ.get("MODEL_TIER_MEDIUM", "gpt-6-luna"),
    "complex": os.environ.get("MODEL_TIER_COMPLEX", "gpt-4o-mini"),
}
TOP_TIER_MODEL = TIER_MODELS["complex"]

# Appended to every user turn THIS module sends. reextraction.py's own
# direct callers never pass this, so the orchestrator's cost-discipline
# goal doesn't change behavior for anything routed outside it.
_TOKEN_DISCIPLINE_HINT = (
    "IMPORTANT: Minimize output tokens. Return ONLY the required JSON, as "
    "compact as valid JSON allows -- no explanations, no commentary before "
    "or after the JSON, no extra formatting beyond what JSON requires."
)


# ── Complexity classification ──────────────────────────────────────────
# Deterministic, no LLM call -- same philosophy as detect_type.py: gate on
# structure actually present in the text, never guess.

# A line starting with a short numeric serial followed by a column marker
# -- the same shape detect_type.py's own _DATA_ROW_RE looks for -- used
# here purely as a rough "how many item rows does this table have" count,
# not to parse the rows themselves.
_ROW_START_RE = re.compile(r"^\s*\d{1,4}\s*[│|]", re.MULTILINE)

_COMPLEX_PAGES = 10
_COMPLEX_ROWS = 40
_COMPLEX_CHARS = 30_000
_COMPLEX_MARKER_DENSITY = 0.60  # "│" column markers per line of text

_MEDIUM_PAGES = 5
_MEDIUM_ROWS = 10
_MEDIUM_CHARS = 8_000
_MEDIUM_MARKER_DENSITY = 0.30

# Marker density is only meaningful as an AVERAGE over a real document's mix
# of table rows and plain label/address lines -- on a short text (a handful
# of lines, most or all of them table rows) a single wide row can push the
# average arbitrarily high with no real document-wide complexity behind it.
# Below this many lines, marker density is skipped entirely and only
# page/row/char-count decide the tier.
_MARKER_DENSITY_MIN_LINES = 15


def classify_complexity(invoice_text: str, tables_text: str = "") -> str:
    """
    "simple" | "medium" | "complex", from signals that correlate with how
    hard THIS document is to extract correctly: page count, an estimated
    item-row count, total character volume, and column-marker density (a
    proxy for how wide/multi-column the layout is -- pdf_reader.py's "│"
    column-boundary marker gets denser the more columns a page packs in,
    which is exactly the layout shape that makes extraction harder).

    Any ONE signal clearing a tier's threshold is enough to classify into
    that tier (an "or", not requiring every signal to agree) -- a document
    can be hard for any one of these reasons alone (e.g. a single page
    that's extremely dense, or a 10-page document whose pages are each
    short), and this errs toward spending a stronger model rather than
    under-classifying a genuinely hard document as simple.
    """
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
    """Attempt 1 uses the complexity-picked tier; every retry escalates
    straight to the top tier, regardless of which tier attempt 1 used."""
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
    """
    Per-model token usage -> one combined cost breakdown. Costed per model
    actually used, not a single fixed rate: an escalated retry on
    TOP_TIER_MODEL must be billed at ITS OWN (likely higher) per-token
    rate, not attempt 1's cheaper tier's rate.
    """
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


# ── Graph state ─────────────────────────────────────────────────────────

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
    # Same targeted-retry rule as reextraction.py: on attempt 1 both passes
    # always run; on a retry, only the pass(es) whose output actually
    # failed validation are re-run.
    need_header = attempt == 1 or header_feedback is not None
    need_items = attempt == 1 or items_feedback is not None

    header_raw = state.get("header_raw", {})
    items_raw = state.get("items_raw", [])
    header_notes = state.get("header_notes", [])
    item_notes = state.get("item_notes", [])
    usage_by_model = {k: dict(v) for k, v in state.get("usage_by_model", {}).items()}
    extra_sheets_raw = state.get("extra_sheets_raw", {})
    item_sheet_name = ITEM_SHEET_NAME[state["shipment_type"]]
    # Whether the item sheet has a row-link column (Item_No/Item_Ser_No/
    # SL_No) decides HOW chunked_extraction's rows for it get matched back
    # to the right item below: by that link value when there is one, or by
    # POSITION (same top-to-bottom order as items_raw) when there isn't --
    # see chunked_extraction.build_field_chunks' own `multi_row` handling,
    # which is what makes a link-less item sheet still return one row per
    # item instead of collapsing to at most one row total.
    item_link_field = state["schema"].get(item_sheet_name, {}).get("link_field")

    if need_header:
        header_raw, h_usage = await extract_invoice_header(
            state["invoice_text"], tables_text=state.get("tables_text", ""),
            model=model, api_key=state.get("api_key"),
            feedback=header_feedback, previous_result=header_raw,
            failed_part=state.get("header_failed_part"),
            extra_instruction=_TOKEN_DISCIPLINE_HINT,
            schema=state["schema"], item_sheet_name=item_sheet_name,
            # The template's OTHER sheets no longer ride along in this call
            # (see chunked_extraction.py) -- cramming ~150 long-tail
            # columns into one call was the confirmed source of low
            # accuracy on both those fields AND this call's own ~30 core
            # header fields. Those sheets are now covered by their own
            # small, focused, parallel calls below instead.
            include_sheets_block=False,
        )
        _add_usage(usage_by_model, model, h_usage)
        header_raw.pop("extra_sheets", None)

    if need_items:
        previous_items = items_raw
        extra = {"fine_chunks": True} if state.get("fine_next") else {}
        # Hint only -- header_raw hasn't been through apply_header_guardrails
        # yet at this point, so an OCR-misread duplicate could still look
        # like 2 numbers here; split_items' OCR-tolerant matching (below)
        # still groups rows correctly even if this hint isn't perfectly
        # collapsed.
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
            # Same reasoning as include_sheets_block above: the item
            # sheet's own long-tail columns (everything beyond the ~11-15
            # core mapped fields this call's hand-tuned rules are written
            # for) are now extracted by chunked_extraction.py's own
            # focused calls, not appended here -- link field or not (see
            # item_link_field's own comment above).
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
            # On a retry, new_items are brand-new dicts from this attempt's
            # extract_line_items() call -- they don't carry the
            # `_extra_template_fields` bucket that chunked_extraction.py's
            # item-sheet long tail was merged into back on attempt 1 (that
            # merge only ever runs once, below, `if attempt == 1`). Without
            # carrying it forward here, a retry silently drops the item
            # sheet's own long-tail columns even though attempt 1 found them
            # correctly -- confirmed: item sheet showed populated rows right
            # after extract_all_chunks on attempt 1, but ended up [] in the
            # final result once a later attempt replaced items_raw.
            if item_link_field:
                by_link = {
                    str(it.get("item_ser_no")): it.get("_extra_template_fields")
                    for it in previous_items if it.get("_extra_template_fields")
                }
                for it in new_items:
                    extra = by_link.get(str(it.get("item_ser_no")))
                    if extra:
                        it["_extra_template_fields"] = dict(extra)
            else:
                for it, prev in zip(new_items, previous_items):
                    extra = prev.get("_extra_template_fields")
                    if extra:
                        it["_extra_template_fields"] = dict(extra)
            items_raw, item_notes = new_items, new_item_notes

    if attempt == 1:
        # The long tail (item sheet's own extra columns + every auxiliary
        # sheet -- License/AR4/Drawback/Job-Work/...) runs ONCE, on the
        # first attempt only, as many small parallel calls instead of one
        # overloaded call -- see chunked_extraction.py's docstring for why.
        # It isn't re-run on a retry: there's no per-field validation
        # feedback loop for this data today, same as the old custom-prompt
        # path it replaces.
        chunk_sheets, chunk_usage = await extract_all_chunks(
            state["invoice_text"], state.get("tables_text", ""),
            state["schema"], item_sheet_name,
            item_core_real_fields(state["shipment_type"]),
            model, api_key=state.get("api_key"),
            shipment_type=state["shipment_type"],
            # attempt==1 implies need_items was True this attempt (see
            # need_items' own definition above), so known_invoice_numbers
            # was already computed just above -- same multi-invoice signal
            # the core extract_line_items() call above just used, now also
            # reaching the chunked long-tail call's own multi_row prompt.
            known_invoice_numbers=known_invoice_numbers,
        )
        _add_usage(usage_by_model, model, chunk_usage)

        print("=" * 70)
        print(f"[chunked-debug] shipment_type={state['shipment_type']!r}  item_sheet_name={item_sheet_name!r}  "
              f"item_link_field={item_link_field!r}  known_invoice_numbers={known_invoice_numbers!r}")
        print(f"[chunked-debug] items_raw (from core line-items call): {len(items_raw)} item(s)")
        print(f"[chunked-debug] chunk_sheets returned by extract_all_chunks -- keys and row counts:")
        for sheet_name, rows in chunk_sheets.items():
            print(f"    {sheet_name!r}: {len(rows)} row(s)" + (f"  {rows}" if sheet_name == item_sheet_name else ""))
        if item_sheet_name not in chunk_sheets:
            print(f"    (no key at all for item_sheet_name={item_sheet_name!r} -- every one of its chunks failed)")
        print("=" * 70)

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
            # No link field on this sheet -- chunked_extraction.py asked
            # for (and merged) these rows positionally instead (see
            # FieldChunk.multi_row), so match them back to items_raw the
            # same way: by index, in the same top-to-bottom order both
            # calls were asked to preserve. A count mismatch means the two
            # independent LLM calls disagreed on how many line items there
            # are -- merge only as many as line up rather than risk
            # assigning a chunk row to the wrong item.
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
        # This PDF bundles more than one genuinely distinct invoice (OCR-
        # variant duplicates already collapsed by apply_header_guardrails
        # above) -- split and validate each one independently instead of
        # merging them into one header + one flat item list.
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
    """The "rows went missing" completeness retry (fine_chunks) only ever
    runs once -- once spent, LINE_ITEMS_INCOMPLETE is excluded from the
    feedback build so it's never asked for again, though other retryable
    errors still count (same rule as reextraction.py)."""
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
        # Every ERROR present was non-retryable -- nothing a retry could
        # act on, so stop instead of looping pointlessly.
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
    """
    LangGraph-driven counterpart to reextraction.extract_and_validate, with
    the SAME return shape (header/sheets/warnings/token_usage/validation/
    validation_history/attempts/extraction_status), plus two extra keys:
      complexity   -- the classified tier ("simple" / "medium" / "complex")
      models_used  -- the model that ran each attempt, in order

    If the source PDF bundles more than one genuinely distinct invoice
    (see multi_invoice.py), "header"/"sheets"/"validation" are replaced by
    a single "invoices" key instead: a list, one entry per invoice, each
    shaped {"header", "sheets", "warnings", "validation"} and independently
    validated against only its OWN total. "validation"/"extraction_status"
    at the top level then reflect the AND of every sub-invoice's own
    validity (see multi_invoice.combine_validations) -- still a single
    ValidationResult, so the retry loop above needed no changes to
    understand it.

    No `model` parameter: which model runs attempt 1 is decided by
    classify_complexity(); a validation-failure retry always escalates to
    TOP_TIER_MODEL. See this module's docstring for the full policy.
    """
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
