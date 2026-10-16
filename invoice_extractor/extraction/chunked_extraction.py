"""
chunked_extraction.py
----------------------
Production replacement for the "ask about every remaining template column
in one giant call" approach (the old llm_extract._render_sheets_block /
"ADDITIONAL TEMPLATE FIELDS" append-blocks, and the even larger
prompts/<type>_sheets_prompt.txt custom prompt that -- because it exists on
disk -- was actually the one running in production via
llm_extract.extract_items_and_sheets_custom, asking the model about ALL
~160 template columns across every sheet in ONE call).

That single-call-does-everything shape is the confirmed source of this
project's low-accuracy complaint: cramming a hundred-plus rarely-populated
customs-filing fields (License/AR4/Drawback/Job-Work/...) alongside the
item sheet's own long tail into one prompt dilutes the model's attention
across far more fields than any one call should carry. That custom-prompt
mechanism (prompts/<type>_sheets_prompt.txt, llm_extract.py's old
has_custom_sheets_prompt/extract_items_and_sheets_custom) has since been
retired entirely -- this module is its full replacement, not an
alternative alongside it.

This module keeps the two CORE, hand-tuned calls in llm_extract.py exactly
as they are (extract_invoice_header's ~30 header fields,
extract_line_items's ~11-15 mapped item fields -- both have years of
domain-specific extraction rules that must not be disturbed) and replaces
ONLY the "everything else" extraction with many small, focused,
Pydantic-grounded calls run concurrently:

  1. Every template sheet's remaining columns (the item sheet's own long
     tail beyond its ~11-15 core mapped fields, plus every auxiliary sheet
     -- License, AR4, Drawback, Job-Work, ...) are split into FieldChunk
     groups of at most CHUNK_SIZE columns each (see build_field_chunks).
  2. Each chunk gets its OWN dynamically-built Pydantic row model (same
     typing convention as models.build_sheet_models, so the shape matches
     what validate.py will validate against later) wrapped in a small
     "rows: [...]" container model.
  3. One LLM call per chunk, using OpenAI's structured-outputs
     `response_format=<PydanticModel>` so the response is schema-guaranteed
     -- no json_repair guesswork needed for this path, unlike the
     free-text JSON the two core calls still have to tolerate.
  4. All chunks for one invoice run concurrently via asyncio.gather
     (extract_all_chunks), not sequentially -- N small calls in parallel
     costs comparable wall-clock time to one big call, for a large
     accuracy win on the long-tail fields.
  5. Chunks belonging to the SAME sheet (a sheet with more columns than
     CHUNK_SIZE needs more than one chunk) are merged back together by
     that sheet's own link field (Item_No / Item_Ser_No / SL_No) so the
     caller still gets ONE row per item/entry, not fragments.

Output shape: {sheet_name: [row_dict, ...]} -- for every sheet EXCEPT it
also includes the item sheet's own long-tail rows under the item sheet's
own name (orchestrator.py is responsible for folding those into each
item's `_extra_template_fields`, matched by item_ser_no, before calling
validate.validate_and_coerce -- see its own merge in _extract_node).
Every OTHER sheet's rows are already in exactly the shape
validate.map_extra_sheets expects for its `raw_extra_sheets` argument, no
further transformation needed.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from openai import (
    AsyncOpenAI,
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)
from pydantic import BaseModel, Field, create_model
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from invoice_extractor.extraction.llm_extract import _extract_usage  # reuse the exact same usage-counting logic as the core calls
from invoice_extractor.pipeline_log import print_llm_input_text, print_pydantic_model

logger = logging.getLogger("invoice_extractor")

# Fields per chunk. 10-15 is the sweet spot the whole redesign is built
# around: small enough that the model isn't juggling a hundred unrelated
# column names at once, large enough that a template with ~160 total
# fields doesn't explode into an unreasonable number of calls (161 fields
# / 12 ~= 13-14 calls for the export template, run concurrently).
CHUNK_SIZE = 12

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
    link_field: str | None          # e.g. "Item_No", "Item_Ser_No", "SL_No" -- None for a sheet with no row-link column at all
    fields: tuple[str, ...]         # this chunk's own data columns, <= CHUNK_SIZE
    aliases: dict[str, list[str]]
    types: dict[str, str]
    index: int                      # this chunk's position within its sheet (0-based), for naming/logging only


def build_field_chunks(
    schema: dict[str, dict[str, Any]],
    exclude: dict[str, set[str]] | None = None,
    chunk_size: int = CHUNK_SIZE,
) -> list[FieldChunk]:
    """
    Splits every sheet's columns into groups of at most `chunk_size` DATA
    fields each. A sheet's link_field (if it has one) always rides along
    in every chunk of that sheet -- so a chunk's returned rows can be
    matched back to the right item/entry afterward -- without counting
    against the chunk_size budget itself.

    `exclude`: {sheet_name: {field, ...}} -- fields already covered by an
    existing hand-tuned call. In practice this is only ever
    {item_sheet_name: <the ~11-15 real column names
    llm_extract._GENERIC_TO_REAL_ITEM_FIELDS already maps for this
    shipment_type>} (see llm_extract.item_core_real_fields), so this
    module's chunks cover the item sheet's remaining long-tail columns,
    not the ones the core line-items call already owns. A sheet left with
    zero fields after exclusion contributes no chunks at all.
    """
    exclude = exclude or {}
    chunks: list[FieldChunk] = []
    for sheet_name, info in schema.items():
        skip = exclude.get(sheet_name, set())
        link_field = info.get("link_field")
        data_fields = [f for f in info["fields"] if f not in skip and f != link_field]
        if not data_fields:
            continue
        for i in range(0, len(data_fields), chunk_size):
            group = tuple(data_fields[i:i + chunk_size])
            chunks.append(FieldChunk(
                sheet=sheet_name,
                link_field=link_field,
                fields=group,
                aliases=info.get("aliases", {}),
                types=info.get("types", {}),
                index=i // chunk_size,
            ))
    return chunks


def _row_model_for_chunk(chunk: FieldChunk) -> type[BaseModel]:
    """One row of this chunk's sheet, carrying only this chunk's own
    columns plus the sheet's link field (if any) -- same Optional
    str/number + alias-as-description convention as
    models.build_sheet_models, so this stays consistent with what
    validate.py checks the final merged row against later."""
    field_defs: dict[str, Any] = {}
    if chunk.link_field:
        field_defs[chunk.link_field] = (
            Optional[str],
            Field(
                default=None,
                description=(
                    "Row/item identifier this data belongs to -- copy EXACTLY "
                    "as printed, matching the same identifier used elsewhere "
                    "on this document for that row, so this answer can be "
                    "matched back to the right row afterward."
                ),
            ),
        )
    for field in chunk.fields:
        aliases = chunk.aliases.get(field) or []
        is_number = chunk.types.get(field) == "number"
        py_type = Optional[float | str] if is_number else Optional[str]
        description = f"Also printed as: {', '.join(aliases)}" if aliases else None
        field_defs[field] = (py_type, Field(default=None, description=description))
    return create_model(f"{chunk.sheet}Chunk{chunk.index}Row", **field_defs)


def _rows_model_for_chunk(chunk: FieldChunk) -> type[BaseModel]:
    row_model = _row_model_for_chunk(chunk)
    return create_model(
        f"{chunk.sheet}Chunk{chunk.index}Rows",
        rows=(list[row_model], Field(default_factory=list)),
    )


_SYSTEM_PROMPT_TEMPLATE = """\
You are an expert commercial invoice / customs-document data extractor.
You receive the complete, layout-preserved text extracted from an invoice.

You are being asked about ONLY {n} specific field(s) belonging to the \
"{sheet}" sheet of a customs filing template -- nothing else. A SEPARATE \
call already extracts the invoice's own item table (description, HSN, \
part number, quantity, unit, rate, amount) -- do not try to extract any \
of that here, and never use it to fill these fields either (see rule 2).

FIELDS TO EXTRACT (sheet: {sheet}):
{field_lines}

THE DEFAULT, CORRECT ANSWER FOR MOST INVOICES IS AN EMPTY "rows" LIST. \
These fields are customs-filing-stage data (license/drawback/AR4/job-work \
references, scheme codes, and similar) that a commercial invoice usually \
does NOT print at all. Returning a row that isn't genuinely backed by this \
document is a WORSE mistake than returning an empty list and missing one \
that was -- a wrong value silently corrupts the output with no way to \
tell it apart from a real one; a missed value can still be caught later.

RULES:
1. Fill a field ONLY if ITS OWN value, under a label matching this field \
or one of its listed aliases, is explicitly printed somewhere on the \
document -- not a value you already know from the item table, not a \
value you inferred or computed, and not a value copied from a different \
field or a different row.
2. NEVER copy the item table's own data (description, part number, HSN, \
quantity, unit, rate, amount) into one of these fields just because it \
relates to the same item. These fields describe something ADDITIONAL the \
document separately states (e.g. a license registration number, an AR4 \
claim number, a production-batch quantity) -- not a restatement of what \
the item table already says. Confirmed real failure: asked for a \
production-batch quantity field, a past run returned the item's own \
ordered quantity instead, because no separate production-batch figure was \
actually printed anywhere -- the correct answer there was to OMIT that \
row entirely, not substitute the item's quantity for it.
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
7. When in doubt, omit the field or the whole row. Every one of these \
fields is optional data; there is no requirement to produce a row for \
every item, or a value for every field in a row you do return.
"""


def _build_chunk_prompt(chunk: FieldChunk) -> str:
    lines = []
    for f in chunk.fields:
        aliases = chunk.aliases.get(f) or []
        lines.append(f"- {f} (also printed as: {', '.join(aliases)})" if aliases else f"- {f}")
    link_rule = (
        f'Every row MUST include "{chunk.link_field}" set to the row/item '
        f"identifier that row's data belongs to (the same identifier used "
        f"elsewhere on this document for that item/row)."
        if chunk.link_field else
        "This sheet has no row-link field -- return at most one row."
    )
    return _SYSTEM_PROMPT_TEMPLATE.format(
        n=len(chunk.fields), sheet=chunk.sheet,
        field_lines="\n".join(lines), link_rule=link_rule,
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
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Runs ONE focused LLM call for ONE field chunk, grounded by OpenAI
    structured outputs against that chunk's own Pydantic schema. Returns
    (rows, usage) -- rows are real-column-keyed dicts (link field included
    when the sheet has one), with any null/empty field dropped."""
    system_prompt = _build_chunk_prompt(chunk)
    user_content = "INVOICE TEXT:\n" + invoice_text
    if tables_text:
        user_content = "EXTRACTED TABLES:\n" + tables_text + "\n\n" + user_content

    rows_model = _rows_model_for_chunk(chunk)
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
        row_dict = {k: v for k, v in row.model_dump().items() if v not in (None, "")}
        if row_dict:
            rows.append(row_dict)
    return rows, usage


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
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    """
    Runs every FieldChunk for this template (item sheet's long tail +
    every auxiliary sheet) CONCURRENTLY -- one focused LLM call per chunk
    -- then merges same-sheet chunks' rows back together by their shared
    link field, so a sheet that needed N chunks still comes back as ONE
    row per item/entry.

    A chunk call that raises (after its own retries) is logged and
    skipped rather than failing the whole invoice -- the long tail is, by
    construction, optional data; losing one chunk's fields is far better
    than losing the entire extraction over a single transient failure on
    a rarely-populated sheet.

    Returns (sheets, usage) where `sheets` is {sheet_name: [row, ...]}.
    `item_sheet_name`'s own entry (if any) still needs folding into each
    item row's `_extra_template_fields` by the caller (see
    orchestrator.py's _extract_node) -- every OTHER key is already in the
    exact shape validate.map_extra_sheets expects.
    """
    exclude = {item_sheet_name: item_core_real_fields}
    chunks = build_field_chunks(schema, exclude=exclude, chunk_size=chunk_size)
    if not chunks:
        return {}, _empty_usage()

    client = AsyncOpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))
    logger.info(
        "Chunked extraction: %d field chunk(s) across %d sheet(s), model=%s",
        len(chunks), len({c.sheet for c in chunks}), model,
    )
    results = await asyncio.gather(
        *(extract_chunk(client, invoice_text, tables_text, c, model) for c in chunks),
        return_exceptions=True,
    )

    merged: dict[str, dict[str, dict[str, Any]]] = {}  # sheet -> {link_value: row}
    total_usage = _empty_usage()
    for chunk, result in zip(chunks, results):
        if isinstance(result, BaseException):
            logger.warning("Chunked extraction: chunk %s[%d] failed: %s", chunk.sheet, chunk.index, result)
            continue
        rows, usage = result
        for k in total_usage:
            total_usage[k] += usage.get(k, 0)
        bucket = merged.setdefault(chunk.sheet, {})
        for row in rows:
            link_val = str(row.get(chunk.link_field)) if chunk.link_field else "_single_"
            bucket.setdefault(link_val, {}).update(row)

    sheets = {sheet: list(rows_by_link.values()) for sheet, rows_by_link in merged.items()}
    return sheets, total_usage
