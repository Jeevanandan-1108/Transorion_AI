from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field as dataclass_field
from typing import Any

from openai import (
    AsyncOpenAI,
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from invoice_extractor.extraction.llm_extract import _extract_usage
from invoice_extractor.pipeline_log import print_llm_input_text, print_pydantic_model
from invoice_extractor.schema.schema import field_description_text
from invoice_extractor.schema.pydantic_models import (
    chunk_key as _chunk_key,
    generated_chunk_models as _generated_chunk_models,
    group_rows_model as _group_rows_model,
    row_model_for_chunk as _row_model_for_chunk,
    rows_model_for_chunk as _rows_model_for_chunk,
)

logger = logging.getLogger("invoice_extractor")

CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "12"))

_llm_retry = retry(
    retry=retry_if_exception_type(
        (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError)
    ),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    stop=stop_after_attempt(4),
    reraise=True,
)


@dataclass(frozen=True)
class FieldChunk:
    sheet: str
    link_field: str | None
    fields: tuple[str, ...]
    aliases: dict[str, list[str]]
    types: dict[str, str]
    index: int
    multi_row: bool = False
    descriptions: dict[str, str] = dataclass_field(default_factory=dict)


def build_field_chunks(
    schema: dict[str, dict[str, Any]],
    exclude: dict[str, set[str]] | None = None,
    chunk_size: int = CHUNK_SIZE,
    item_sheet_name: str | None = None,
) -> list[FieldChunk]:
    exclude = exclude or {}
    chunks: list[FieldChunk] = []
    for sheet_name, info in schema.items():
        skip = exclude.get(sheet_name, set())
        link_field = info.get("link_field")
        data_fields = [f for f in info["fields"] if f not in skip and f != link_field]
        if not data_fields:
            continue
        multi_row = link_field is None and sheet_name == item_sheet_name
        for i in range(0, len(data_fields), chunk_size):
            group = tuple(data_fields[i:i + chunk_size])
            chunks.append(FieldChunk(
                sheet=sheet_name,
                link_field=link_field,
                fields=group,
                aliases=info.get("aliases", {}),
                types=info.get("types", {}),
                index=i // chunk_size,
                multi_row=multi_row,
                descriptions=info.get("descriptions", {}),
            ))
    return chunks


_SYSTEM_PROMPT_TEMPLATE = """\
You are an expert commercial invoice / customs-document data extractor.
You receive the complete, layout-preserved text extracted from an invoice.

{intro}

FIELDS TO EXTRACT (sheet: {sheet}):
{field_lines}

{default_answer}

RULES:
1. Fill a field ONLY if ITS OWN value, under a label matching this field \
or one of its listed aliases, is explicitly printed somewhere on the \
document -- not a value you already know from the item table, not a \
value you inferred or computed, and not a value copied from a different \
field or a different row.
2. {rule2}
3. NEVER use a sentence, declaration, or boilerplate paragraph printed \
elsewhere on the invoice (a RoDTEP/reward-scheme declaration, a \
payment-terms line, a grand-total line, terms and conditions) as the \
value for one of these fields just because it was the closest-sounding \
text available. Confirmed real failure: a payment-terms sentence and a \
grand-total line both ended up copied into unrelated fields this way. If \
you cannot find a value that is UNAMBIGUOUSLY about this specific field, \
leave it null.
4. {link_rule}
5. Copy values exactly as printed (same digits, same casing/formatting) -- \
never reformat, never calculate, never normalize.
6. If a row has a value for some but not all of these fields, fill only \
the fields that are actually printed and leave the rest null -- never \
guess the missing ones.
7. {rule7}
"""

_SINGLE_ROW_INTRO = (
    'You are being asked about ONLY {n} specific field(s) belonging to the "{sheet}" sheet of a '
    "customs filing template -- nothing else. A SEPARATE call already extracts the invoice's own "
    "item table (description, HSN, part number, quantity, unit, rate, amount) -- do not try to "
    "extract any of that here, and never use it to fill these fields either (see rule 2)."
)
_MULTI_ROW_INTRO = (
    'You are being asked about ONLY {n} specific field(s) belonging to the "{sheet}" sheet of a '
    "customs filing template -- this is the invoice's OWN item/line-items sheet, so you must "
    "return ONE ROW PER LINE ITEM on the invoice's own item table, not at most one row -- but "
    "ONLY these {n} field(s) for each item, nothing else (a SEPARATE call already extracts that "
    "same table's description/HSN/part number/quantity/unit/rate/amount; do not repeat those "
    "here, see rule 2)."
)

_SINGLE_ROW_DEFAULT_ANSWER = (
    'THE DEFAULT, CORRECT ANSWER FOR MOST INVOICES IS AN EMPTY "rows" LIST. '
    "These fields are customs-filing-stage data (license/drawback/AR4/job-work references, scheme "
    "codes, and similar) that a commercial invoice usually does NOT print at all. Returning a row "
    "that isn't genuinely backed by this document is a WORSE mistake than returning an empty list "
    "and missing one that was -- a wrong value silently corrupts the output with no way to tell it "
    "apart from a real one; a missed value can still be caught later."
)
_MULTI_ROW_DEFAULT_ANSWER = (
    "Unlike a one-off auxiliary sheet, this IS core per-item data -- do not default to an empty "
    'list here just because these specific fields are less central than the main item table\'s own '
    "description/quantity/price. Return one row per line item, in the SAME top-to-bottom order the "
    "items appear in the invoice's own item table -- even an item with NOTHING printed for any of "
    "these specific fields still gets its own (all-null) row, so row position/count exactly matches "
    "the item table's own order. Still never invent a value: a field stays null when genuinely not "
    "printed for that item, only the ROW itself must not be skipped."
)

_SINGLE_ROW_RULE2 = (
    "NEVER copy the item table's own data (description, part number, HSN, quantity, unit, rate, "
    "amount) into one of these fields just because it relates to the same item. These fields "
    "describe something ADDITIONAL the document separately states (e.g. a license registration "
    "number, an AR4 claim number, a production-batch quantity) -- not a restatement of what the "
    "item table already says. Confirmed real failure: asked for a production-batch quantity field, "
    "a past run returned the item's own ordered quantity instead, because no separate "
    "production-batch figure was actually printed anywhere -- the correct answer there was to OMIT "
    "that row entirely, not substitute the item's quantity for it."
)
_MULTI_ROW_RULE2 = (
    "NEVER copy or re-derive a value for one of THESE SPECIFIC fields from the item table's own "
    "description/part number/HSN/quantity/unit/rate/amount columns (a separate call already "
    "extracts those) -- only use what is explicitly printed under a label matching one of THESE "
    "fields' own names/aliases for that row. A field stays null when nothing is printed for it, "
    "even though you must still return the row itself (see the note above)."
)

_SINGLE_ROW_RULE7 = (
    "When in doubt, omit the field or the whole row. Every one of these fields is optional data; "
    "there is no requirement to produce a row for every item, or a value for every field in a row "
    "you do return."
)
_MULTI_ROW_RULE7 = (
    "When in doubt about one FIELD's value, leave it null -- but never omit the ROW itself (see the "
    "note above): one row per line item is required regardless of how many of these specific "
    "fields turn out null for it."
)


def _build_chunk_prompt(chunk: FieldChunk) -> str:
    lines = []
    for f in chunk.fields:
        desc = field_description_text(f, chunk.aliases, chunk.descriptions)
        lines.append(f"- {f} ({desc})" if desc else f"- {f}")
    if chunk.link_field:
        link_rule = (
            f'Every row MUST include "{chunk.link_field}" set to the row/item identifier that '
            f"row's data belongs to (the same identifier used elsewhere on this document for that "
            f"item/row)."
        )
    elif chunk.multi_row:
        link_rule = (
            "This sheet has no separate row-link field -- rows are matched back to the right item "
            "by POSITION instead, so the order/count rule above (one row per item, same order, "
            "never skip a row) is what keeps this correct, not an identifier field."
        )
    else:
        link_rule = "This sheet has no row-link field -- return at most one row."

    return _SYSTEM_PROMPT_TEMPLATE.format(
        n=len(chunk.fields), sheet=chunk.sheet,
        field_lines="\n".join(lines), link_rule=link_rule,
        intro=(_MULTI_ROW_INTRO if chunk.multi_row else _SINGLE_ROW_INTRO).format(n=len(chunk.fields), sheet=chunk.sheet),
        default_answer=_MULTI_ROW_DEFAULT_ANSWER if chunk.multi_row else _SINGLE_ROW_DEFAULT_ANSWER,
        rule2=_MULTI_ROW_RULE2 if chunk.multi_row else _SINGLE_ROW_RULE2,
        rule7=_MULTI_ROW_RULE7 if chunk.multi_row else _SINGLE_ROW_RULE7,
    )


@_llm_retry
async def _call_chunk_model(client: AsyncOpenAI, **kwargs):
    return await client.chat.completions.parse(**kwargs)


async def extract_chunk(
    client: AsyncOpenAI,
    invoice_text: str,
    tables_text: str,
    chunk: FieldChunk,
    model: str,
    shipment_type: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    system_prompt = _build_chunk_prompt(chunk)
    user_content = "INVOICE TEXT:\n" + invoice_text
    if tables_text:
        user_content = "EXTRACTED TABLES:\n" + tables_text + "\n\n" + user_content

    static_row_model = _generated_chunk_models(shipment_type).get((chunk.sheet, chunk.index))
    rows_model = _rows_model_for_chunk(chunk, row_model=static_row_model)
    label = f"{chunk.sheet} chunk {chunk.index}"
    print_llm_input_text(label, user_content)
    print_pydantic_model(label, rows_model)
    resp = await _call_chunk_model(
        client,
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        response_format=rows_model,
    )
    usage = _extract_usage(resp)
    parsed = resp.choices[0].message.parsed
    if parsed is None:
        return [], usage

    rows: list[dict[str, Any]] = []
    for row in parsed.rows:
        row_dict = {k: v for k, v in row.model_dump(by_alias=True).items() if v not in (None, "")}
        if row_dict or chunk.multi_row:
            rows.append(row_dict)
    return rows, usage


MAX_LLM_CALLS_PER_TEMPLATE = int(os.environ.get("MAX_LLM_CALLS_PER_TEMPLATE", "5"))


def _group_chunk_indices(chunks: list[FieldChunk], max_groups: int) -> list[list[int]]:
    if max_groups <= 0 or len(chunks) <= max_groups:
        return [[i] for i in range(len(chunks))]
    order = sorted(range(len(chunks)), key=lambda i: len(chunks[i].fields), reverse=True)
    groups: list[list[int]] = [[] for _ in range(max_groups)]
    sizes = [0] * max_groups
    for i in order:
        g = min(range(max_groups), key=lambda g: sizes[g])
        groups[g].append(i)
        sizes[g] += len(chunks[i].fields)
    return [g for g in groups if g]


_GROUP_PROMPT_HEADER = """\
You are an expert commercial invoice / customs-document data extractor.
You receive the complete, layout-preserved text extracted from an invoice.

This ONE call covers {n_sections} DIFFERENT, UNRELATED section(s) of a customs filing template, \
each with its own separate set of fields -- treat every section below completely independently; \
a field in one section has NOTHING to do with a same-named or similar-sounding field in another \
section, and a value belonging to one section must NEVER be copied into another just because they \
seem related. A SEPARATE call already extracts the invoice's own item table (description, HSN, \
part number, quantity, unit, rate, amount) -- do not try to extract any of that here, and never \
use it to fill any of these fields either.

THE DEFAULT, CORRECT ANSWER FOR MOST SECTIONS BELOW IS AN EMPTY LIST for that section's own key. \
Most of these fields are customs-filing-stage data (license/drawback/AR4/job-work references, \
scheme codes, and similar) that a commercial invoice usually does NOT print at all. Returning a \
row that isn't genuinely backed by this document is a WORSE mistake than returning an empty list \
and missing one that was. (A section explicitly marked "one row per line item" below is the \
exception to this default -- see that section's own rule.)

GENERAL RULES (apply to every section):
1. Fill a field ONLY if ITS OWN value, under a label matching that field's name or one of its \
listed aliases, is explicitly printed somewhere on the document for THAT section's own row -- not \
a value you already know from elsewhere, not inferred or computed, and not copied from a \
different field, a different row, or a different section.
2. NEVER use a sentence, declaration, or boilerplate paragraph printed elsewhere on the invoice \
(a reward-scheme declaration, a payment-terms line, a grand-total line, terms and conditions) as \
the value for one of these fields just because it was the closest-sounding text available.
3. Copy values exactly as printed (same digits, same casing/formatting) -- never reformat, never \
calculate, never normalize.
4. If a row has a value for some but not all of a section's fields, fill only the fields that are \
actually printed and leave the rest null -- never guess the missing ones.
5. Return your answer as one JSON object with EXACTLY the top-level keys named in each section \
below -- each key's value is that section's own list of row objects, following that section's own \
row-count rule, nothing merged or renamed.

SECTIONS:
{sections}
"""

_GROUP_SECTION_TEMPLATE = """\
--- SECTION KEY "{key}" -- sheet "{sheet}" ({n} field(s)) ---
{row_count_rule}
Fields:
{field_lines}
"""


def _group_section_text(chunk: FieldChunk) -> str:
    lines = []
    for f in chunk.fields:
        desc = field_description_text(f, chunk.aliases, chunk.descriptions)
        lines.append(f"- {f} ({desc})" if desc else f"- {f}")

    if chunk.link_field:
        row_count_rule = (
            f'One row per "{chunk.link_field}" value found for this sheet; every row MUST include '
            f'"{chunk.link_field}" set to that row/item\'s identifier (the same identifier used '
            f"elsewhere on this document for it), so it can be matched back afterward. Omit this "
            f"section's key entirely (empty list) if nothing is printed for this sheet at all."
        )
    elif chunk.multi_row:
        row_count_rule = (
            "ONE ROW PER LINE ITEM on the invoice's own item table is REQUIRED here (this is the "
            "item sheet itself, not an optional auxiliary sheet) -- same top-to-bottom order as "
            "that table, never skip a row even if every field in this section is null for that "
            "item (include an all-null row rather than omitting it, so row position/count exactly "
            "matches the item table). This section's default is NOT an empty list."
        )
    else:
        row_count_rule = (
            "This sheet has no row-link field -- return AT MOST ONE row for it (empty list is the "
            "default, correct answer for most invoices)."
        )

    return _GROUP_SECTION_TEMPLATE.format(
        key=_chunk_key(chunk), sheet=chunk.sheet, n=len(chunk.fields),
        row_count_rule=row_count_rule, field_lines="\n".join(lines),
    )


def _build_group_prompt(chunks: list[FieldChunk]) -> str:
    sections = "\n".join(_group_section_text(c) for c in chunks)
    return _GROUP_PROMPT_HEADER.format(n_sections=len(chunks), sections=sections)


async def extract_chunk_group(
    client: AsyncOpenAI,
    invoice_text: str,
    tables_text: str,
    chunks: list[FieldChunk],
    model: str,
    shipment_type: str | None = None,
) -> dict[str, tuple[list[dict[str, Any]], dict[str, int]]]:
    system_prompt = _build_group_prompt(chunks)
    user_content = "INVOICE TEXT:\n" + invoice_text
    if tables_text:
        user_content = "EXTRACTED TABLES:\n" + tables_text + "\n\n" + user_content

    rows_model = _group_rows_model(chunks, shipment_type)
    label = f"group of {len(chunks)} chunk(s) ({', '.join(c.sheet for c in chunks)})"
    print_llm_input_text(label, user_content)
    print_pydantic_model(label, rows_model)
    resp = await _call_chunk_model(
        client,
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        response_format=rows_model,
    )
    usage = _extract_usage(resp)
    parsed = resp.choices[0].message.parsed
    if parsed is None:
        return {_chunk_key(c): ([], usage) for c in chunks}

    result: dict[str, tuple[list[dict[str, Any]], dict[str, int]]] = {}
    for chunk in chunks:
        key = _chunk_key(chunk)
        rows: list[dict[str, Any]] = []
        for row in getattr(parsed, key, []):
            row_dict = {k: v for k, v in row.model_dump(by_alias=True).items() if v not in (None, "")}
            if row_dict or chunk.multi_row:
                rows.append(row_dict)
        result[key] = (rows, usage)
    return result


def _empty_usage() -> dict[str, int]:
    return {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0}


async def extract_all_chunks(
    invoice_text: str,
    tables_text: str,
    schema: dict[str, dict[str, Any]],
    item_sheet_name: str,
    item_core_real_fields: set[str],
    model: str,
    api_key: str | None = None,
    chunk_size: int = CHUNK_SIZE,
    shipment_type: str | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    exclude = {item_sheet_name: item_core_real_fields}
    chunks = build_field_chunks(schema, exclude=exclude, chunk_size=chunk_size, item_sheet_name=item_sheet_name)
    if not chunks:
        return {}, _empty_usage()

    client = AsyncOpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))
    total_usage = _empty_usage()
    rows_by_chunk: list[list[dict[str, Any]]] = [[] for _ in chunks]
    failed: set[int] = set()

    if len(chunks) > MAX_LLM_CALLS_PER_TEMPLATE:
        groups = _group_chunk_indices(chunks, MAX_LLM_CALLS_PER_TEMPLATE)
        logger.info(
            "Chunked extraction: %d field chunk(s) across %d sheet(s) exceeds the %d-call budget -- "
            "bundling into %d grouped call(s) instead (MAX_LLM_CALLS_PER_TEMPLATE), model=%s",
            len(chunks), len({c.sheet for c in chunks}), MAX_LLM_CALLS_PER_TEMPLATE, len(groups), model,
        )
        group_results = await asyncio.gather(
            *(extract_chunk_group(client, invoice_text, tables_text, [chunks[i] for i in idxs], model, shipment_type=shipment_type)
              for idxs in groups),
            return_exceptions=True,
        )
        for idxs, group_result in zip(groups, group_results):
            if isinstance(group_result, BaseException):
                logger.warning(
                    "Chunked extraction: grouped call for sheet(s) %s failed: %s",
                    sorted({chunks[i].sheet for i in idxs}), group_result,
                )
                failed.update(idxs)
                continue
            any_usage = next(iter(group_result.values()))[1]
            for k in total_usage:
                total_usage[k] += any_usage.get(k, 0)
            for i in idxs:
                rows_by_chunk[i] = group_result[_chunk_key(chunks[i])][0]
    else:
        logger.info(
            "Chunked extraction: %d field chunk(s) across %d sheet(s), model=%s",
            len(chunks), len({c.sheet for c in chunks}), model,
        )
        results = await asyncio.gather(
            *(extract_chunk(client, invoice_text, tables_text, c, model, shipment_type=shipment_type) for c in chunks),
            return_exceptions=True,
        )
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                logger.warning("Chunked extraction: chunk %s[%d] failed: %s", chunks[i].sheet, chunks[i].index, result)
                failed.add(i)
                continue
            rows, usage = result
            for k in total_usage:
                total_usage[k] += usage.get(k, 0)
            rows_by_chunk[i] = rows

    merged: dict[str, dict[str, dict[str, Any]]] = {}
    for i, (chunk, rows) in enumerate(zip(chunks, rows_by_chunk)):
        if i in failed:
            continue
        bucket = merged.setdefault(chunk.sheet, {})
        for idx, row in enumerate(rows):
            if chunk.link_field:
                link_val = str(row.get(chunk.link_field))
            elif chunk.multi_row:
                link_val = f"_pos_{idx}"
            else:
                link_val = "_single_"
            bucket.setdefault(link_val, {}).update(row)

    sheets = {sheet: list(rows_by_link.values()) for sheet, rows_by_link in merged.items()}
    return sheets, total_usage
