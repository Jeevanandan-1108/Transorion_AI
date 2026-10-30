from __future__ import annotations

import logging
import os
from typing import Any

from invoice_extractor.extraction.llm_extract import (
    extract_invoice_header,
    extract_line_items_chunked as extract_line_items,
    apply_header_guardrails,
    apply_line_item_guardrails,
    item_core_real_fields,
    _calculate_cost,
    _to_float,
    _split_combined_parts,
)
from invoice_extractor.extraction.chunked_extraction import extract_all_chunks
from invoice_extractor.validation.validate import validate_and_coerce
from invoice_extractor.validation.invoice_validator import (
    validate_invoice, build_retry_feedback, build_header_failed_part, ValidationResult,
)
from invoice_extractor.schema.templates_config import ITEM_SHEET_NAME
from invoice_extractor.extraction.multi_invoice import is_combined_document, split_header, split_items, combine_validations

logger = logging.getLogger("invoice_extractor")


def _line_sum_gap(items: list[dict[str, Any]], printed_total: float) -> float | None:
    rows = [
        it for it in items
        if isinstance(it, dict) and any(
            _to_float(it.get(k)) is not None for k in ("quantity", "unit_price", "line_total")
        )
    ]
    amounts = [_to_float(it.get("line_total")) for it in rows]
    if not amounts or any(a is None for a in amounts):
        return None
    return abs(printed_total - sum(amounts))


def _carry_extra_template_fields(
    new_items: list[dict[str, Any]], old_items: list[dict[str, Any]], link_field: str | None,
) -> None:
    if not old_items:
        return
    def _desc(it: dict[str, Any]) -> str:
        return str(it.get("product_description") or "").strip().upper()

    if link_field:
        old_by_key = {str(it.get("item_ser_no")): it for it in old_items}
        pairs = [(it, old_by_key.get(str(it.get("item_ser_no")))) for it in new_items]
    elif len(new_items) == len(old_items):
        pairs = list(zip(new_items, old_items))
    else:
        descs = [_desc(it) for it in old_items]
        old_by_desc = {d: it for d, it in zip(descs, old_items) if d and descs.count(d) == 1}
        pairs = [(it, old_by_desc.get(_desc(it))) for it in new_items]

    for new, old in pairs:
        extra = (old or {}).get("_extra_template_fields")
        if extra and not new.get("_extra_template_fields"):
            new["_extra_template_fields"] = dict(extra)


def _is_closer_to_total(new_items: list[dict[str, Any]], old_items: list[dict[str, Any]],
                        header_raw: dict[str, Any]) -> bool:
    total = _to_float(header_raw.get("invoice_total"))
    if total is None:
        return True
    new_gap, old_gap = _line_sum_gap(new_items, total), _line_sum_gap(old_items, total)
    if old_gap is None:
        return True
    return new_gap is not None and new_gap <= old_gap

MAX_REEXTRACTION_ATTEMPTS = int(os.environ.get("MAX_REEXTRACTION_ATTEMPTS", "1"))


async def extract_and_validate(
    invoice_text: str,
    schema: dict[str, Any],
    shipment_type: str,
    model: str = "gpt-5-nano",
    tables_text: str = "",
    api_key: str | None = None,
    max_retries: int = MAX_REEXTRACTION_ATTEMPTS,
) -> dict[str, Any]:
    header_raw: dict[str, Any] = {}
    items_raw: list[dict[str, Any]] = []
    warnings: list[str] = []
    total_usage = {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0}
    validation_history: list[dict[str, Any]] = []
    header_notes: list[str] = []
    item_notes: list[str] = []

    header_feedback: str | None = None
    items_feedback: str | None = None
    header_failed_part: dict[str, Any] | None = None
    fine_next = False
    fine_tried = False
    attempt = 0
    result: dict[str, Any] = {"header": {}, "sheets": {}}
    validation: ValidationResult | None = None
    extra_sheets_raw: dict[str, list[dict[str, Any]]] = {}

    while True:
        attempt += 1
        need_header = attempt == 1 or header_feedback is not None
        need_items = attempt == 1 or items_feedback is not None
        item_sheet_name = ITEM_SHEET_NAME[shipment_type]

        if need_header:
            header_raw, h_usage = await extract_invoice_header(
                invoice_text, tables_text=tables_text, model=model, api_key=api_key,
                feedback=header_feedback, previous_result=header_raw,
                failed_part=header_failed_part,
                schema=schema, item_sheet_name=item_sheet_name,
                include_sheets_block=False,
            )
            for k in total_usage:
                total_usage[k] += h_usage.get(k, 0)
            header_raw.pop("extra_sheets", None)

        if need_items:
            previous_items = items_raw
            extra = {"fine_chunks": True} if fine_next else {}
            known_numbers = _split_combined_parts(header_raw.get("invoice_number"))
            known_invoice_numbers = known_numbers if len(known_numbers) > 1 else None
            new_items, i_usage = await extract_line_items(
                invoice_text, tables_text=tables_text, model=model, api_key=api_key,
                feedback=items_feedback, previous_result=previous_items,
                schema=schema, shipment_type=shipment_type, item_sheet_name=item_sheet_name,
                known_invoice_numbers=known_invoice_numbers,
                include_extra_fields=False,
                **extra,
            )
            for k in total_usage:
                total_usage[k] += i_usage.get(k, 0)
            new_item_notes = apply_line_item_guardrails(new_items, invoice_text)
            if attempt > 1 and not _is_closer_to_total(new_items, previous_items, header_raw):
                logger.warning(
                    "Items re-extraction did not bring the line items closer to the printed "
                    "total -- keeping the previous item list."
                )
            else:
                _carry_extra_template_fields(
                    new_items, previous_items, schema.get(item_sheet_name, {}).get("link_field"),
                )
                items_raw, item_notes = new_items, new_item_notes
            fine_next = False

        if attempt == 1:
            chunk_sheets, chunk_usage = await extract_all_chunks(
                invoice_text, tables_text, schema, item_sheet_name,
                item_core_real_fields(shipment_type), model, api_key=api_key,
                shipment_type=shipment_type,
            )
            for k in total_usage:
                total_usage[k] += chunk_usage.get(k, 0)

            item_link_field = schema.get(item_sheet_name, {}).get("link_field")
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
                        "Item sheet '%s' has no link field -- positional merge expected %d row(s) "
                        "(matching items_raw) but chunked extraction returned %d; merging only the "
                        "first %d.",
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
                header_raw, items_raw, invoice_text, shipment_type=shipment_type, notes=new_notes,
            )
            header_notes.extend(n for n in new_notes if n not in header_notes)

        if is_combined_document(header_raw):
            split_headers = split_header(header_raw)
            invoice_numbers = [h["invoice_number"] for h in split_headers]
            item_groups = split_items(items_raw, invoice_numbers)

            invoices: list[dict[str, Any]] = []
            sub_validations: list[ValidationResult] = []
            for h, grp in zip(split_headers, item_groups):
                sub_raw = {"invoice_header": h, "line_items": grp}
                sub_sheets, sub_warnings, sub_header = validate_and_coerce(
                    sub_raw, schema, shipment_type, raw_extra_sheets=extra_sheets_raw,
                )
                sub_result = {"header": sub_header, "sheets": sub_sheets}
                sub_validation = validate_invoice(sub_result, invoice_text, shipment_type)
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
                raw, schema, shipment_type, raw_extra_sheets=extra_sheets_raw,
            )
            warnings = header_notes + item_notes + warnings
            result = {"header": header, "sheets": sheets}
            validation = validate_invoice(result, invoice_text, shipment_type)

        validation_history.append({
            "attempt": attempt,
            "valid": validation.valid,
            "errors": [c.to_dict() for c in validation.errors],
        })

        if validation.valid or attempt > max_retries:
            break

        fb_validation = validation
        if fine_tried:
            fb_validation = ValidationResult(
                valid=validation.valid, status=validation.status,
                checks=[c for c in validation.checks if c.rule != "LINE_ITEMS_INCOMPLETE"],
            )
        header_feedback, items_feedback = build_retry_feedback(fb_validation)
        header_failed_part = build_header_failed_part(fb_validation)
        fine_next = not fine_tried and any(
            c.rule == "LINE_ITEMS_INCOMPLETE" for c in validation.retryable_errors
        )
        if fine_next:
            fine_tried = True
        if header_feedback is None and items_feedback is None:
            break

        logger.warning(
            "Validation failed on attempt %d (%d error(s)) -- re-extracting "
            "(%s).", attempt, len(validation.errors),
            "header+items" if header_feedback and items_feedback
            else "header only" if header_feedback else "items only",
        )

    token_usage = _calculate_cost(
        model, total_usage["input_tokens"], total_usage["cached_tokens"], total_usage["output_tokens"]
    )
    shape = {"invoices": result["invoices"]} if "invoices" in result else {
        "header": result["header"], "sheets": result["sheets"],
    }

    return {
        **shape,
        "warnings": warnings,
        "token_usage": token_usage,
        "validation": validation.to_dict(),
        "validation_history": validation_history,
        "attempts": attempt,
        "extraction_status": "validated" if validation.valid else "validation_failed",
    }
