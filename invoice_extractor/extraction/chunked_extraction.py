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

from invoice_extractor.extraction.llm_extract import _extract_usage  # reuse the exact same usage-counting logic as the core calls
from invoice_extractor.pipeline_log import print_llm_input_text, print_pydantic_model
from invoice_extractor.schema.schema import field_description_text
# All Pydantic model-BUILDING for this module now lives in one shared place
# (invoice_extractor/schema/pydantic_models.py), alongside the validation
# models in schema/models.py -- see that module's own docstring for why.
# Imported under their original underscore-prefixed names so every existing
# internal call site (and tests/test_chunked_extraction.py, which imports
# _row_model_for_chunk/_rows_model_for_chunk directly from this module)
# keeps working unchanged.
from invoice_extractor.schema.pydantic_models import (
    chunk_key as _chunk_key,
    generated_chunk_models as _generated_chunk_models,
    group_rows_model as _group_rows_model,
    row_model_for_chunk as _row_model_for_chunk,
    rows_model_for_chunk as _rows_model_for_chunk,
)

logger = logging.getLogger("invoice_extractor")

# Fields per chunk. 10-15 is the sweet spot the whole redesign is built
# around: small enough that the model isn't juggling a hundred unrelated
# column names at once, large enough that a template with ~160 total
# fields doesn't explode into an unreasonable number of calls (161 fields
# / 12 ~= 13-14 calls for the export template, run concurrently).
# Overridable via .env (CHUNK_SIZE) -- see .env.example.
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
    link_field: str | None          # e.g. "Item_No", "Item_Ser_No", "SL_No" -- None for a sheet with no row-link column at all
    fields: tuple[str, ...]         # this chunk's own data columns, <= CHUNK_SIZE
    aliases: dict[str, list[str]]
    types: dict[str, str]
    index: int                      # this chunk's position within its sheet (0-based), for naming/logging only
    multi_row: bool = False         # this IS the item sheet, has no link_field, but still needs one row per line item (see build_field_chunks)
    descriptions: dict[str, str] = dataclass_field(default_factory=dict)  # hand-written meanings (schema.py's _MANUAL_DESCRIPTIONS), "" if none


def build_field_chunks(
    schema: dict[str, dict[str, Any]],
    exclude: dict[str, set[str]] | None = None,
    chunk_size: int = CHUNK_SIZE,
    item_sheet_name: str | None = None,
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

    `item_sheet_name`: when that sheet has NO link_field (a template
    discovered under templates/ whose schema has no Item_No/Item_Ser_No/
    SL_No-style column -- see templates_config.py), its chunks are still
    marked `multi_row=True` rather than treated like a genuine single-record
    auxiliary sheet (License, Drawback, ...): the item sheet always needs
    one row PER LINE ITEM, link column or not. The caller (extract_all_chunks)
    then asks for, and merges, those rows by POSITION instead of by a link
    value -- see that function and _build_chunk_prompt's own handling of
    `multi_row`.
    """
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


# _row_model_for_chunk / _rows_model_for_chunk / _generated_chunk_models
# are now imported from invoice_extractor.schema.pydantic_models (see the
# import block above) -- the shared Pydantic model-building module.


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


def _multi_invoice_note(known_invoice_numbers: list[str] | None) -> str:
    """Appended to a multi_row chunk's own instructions when this document is
    known (from extract_line_items' own combined-document detection -- see
    orchestrator.py/reextraction.py's known_invoice_numbers) to bundle MORE
    THAN ONE separate invoice, each with its OWN item table.

    Confirmed real failure without this: every multi_row instruction below
    is written assuming exactly one item table ("the invoice's own item
    table", singular) -- a combined document with 3 separate tables gave
    the model no guidance on what to do with more than one, and it answered
    every row empty for EVERY ONE of them rather than guess wrong, even
    though the SEPARATE core line-items call (which IS told about multiple
    invoices via this same known_invoice_numbers) correctly split them.
    """
    if not known_invoice_numbers or len(known_invoice_numbers) < 2:
        return ""
    n = len(known_invoice_numbers)
    numbers = ", ".join(known_invoice_numbers)
    return (
        f" IMPORTANT: this document bundles {n} SEPARATE invoices ({numbers}), each with its OWN "
        f"item table -- not one. Return rows for EVERY line item across ALL {n} of those tables, in "
        f"the SAME order they appear in the document (every item of the first invoice, then every "
        f"item of the next, and so on) -- never return rows for only one invoice, and never merge "
        f"different invoices' items out of order."
    )


def _build_chunk_prompt(chunk: FieldChunk, known_invoice_numbers: list[str] | None = None) -> str:
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
        ) + (_multi_invoice_note(known_invoice_numbers) if chunk.multi_row else "")
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
    known_invoice_numbers: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Runs ONE focused LLM call for ONE field chunk, grounded by OpenAI
    structured outputs against that chunk's own Pydantic schema -- a
    hand-edited static model from invoice_extractor.schema.generated.<shipment_type>
    if one exists for this (sheet, index), else the usual dynamically-built
    one (see _generated_chunk_models). Returns (rows, usage) -- rows are
    real-column-keyed dicts (link field included when the sheet has one),
    with any null/empty field dropped.

    `known_invoice_numbers`: when this document bundles more than one
    separate invoice (see orchestrator.py/reextraction.py's own detection,
    threaded through from there), a multi_row chunk's prompt needs to know
    this explicitly -- see _multi_invoice_note's own docstring for the real
    failure this fixes."""
    system_prompt = _build_chunk_prompt(chunk, known_invoice_numbers)
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
        # by_alias=True: a static model's attributes are Python-safe names
        # (e.g. "inv_no") with the real column name as `alias` ("Inv No") --
        # this recovers the real name regardless of which model built the
        # row. A no-alias dynamic model is unaffected (alias defaults to
        # the field name itself).
        row_dict = {k: v for k, v in row.model_dump(by_alias=True).items() if v not in (None, "")}
        # A multi_row chunk's position WITHIN `rows` is itself meaningful
        # (see extract_all_chunks' positional merge) -- an all-null
        # placeholder row (the model correctly reporting "nothing for THIS
        # item in THIS chunk" per the prompt's own instruction) must still
        # occupy its slot, or every later item would silently shift up by
        # one. A single-record/link-keyed chunk has no such positional
        # meaning, so an empty row there is just noise, same as before.
        if row_dict or chunk.multi_row:
            rows.append(row_dict)
    return rows, usage


# ── Multi-sheet grouped calls (cost-reduction mode) ─────────────────────
#
# Everything above runs ONE chunk (one sheet's own ≤CHUNK_SIZE fields) per
# LLM call -- the design this whole module exists to deliver, and the
# default. A template with many sheets (export_xlsx: 26, icafe: 13,
# import_excel_3: 11) still means one call PER SHEET at minimum, since a
# chunk never spans sheets -- there is no chunk_size that reduces call
# count below the sheet count.
#
# MAX_LLM_CALLS_PER_TEMPLATE below trades accuracy for a hard cost cap: when
# a template would need more calls than this, multiple chunks (frequently
# from several DIFFERENT, unrelated sheets) are bundled into ONE combined
# call instead, each still keeping its own field list/section/row-count
# rule, under its own JSON key. This is explicitly the shape this module's
# own docstring says was the CONFIRMED SOURCE of this project's original
# low-accuracy complaint (cramming unrelated sheets into one prompt dilutes
# the model's attention) -- it is being reintroduced here ONLY because cost
# was judged to matter more than that accuracy risk for these specific
# templates (see the conversation that added this). Do not enable this for
# a template where per-field accuracy on the long tail actually matters.
# Overridable via .env (MAX_LLM_CALLS_PER_TEMPLATE) -- see .env.example.
MAX_LLM_CALLS_PER_TEMPLATE = int(os.environ.get("MAX_LLM_CALLS_PER_TEMPLATE", "5"))


# _chunk_key / _group_rows_model are now imported from
# invoice_extractor.schema.pydantic_models (see the import block above).


def _bin_pack(indices: list[int], chunks: list[FieldChunk], n_groups: int) -> list[list[int]]:
    """Greedy bin-packing of `indices` into at most `n_groups` buckets,
    balancing total FIELD COUNT per bucket (not chunk count) -- the part of
    _group_chunk_indices' old single-pass logic that's still shared by both
    the item-sheet group and the "everything else" groups below."""
    if n_groups <= 0 or len(indices) <= n_groups:
        return [[i] for i in indices]
    order = sorted(indices, key=lambda i: len(chunks[i].fields), reverse=True)
    groups: list[list[int]] = [[] for _ in range(n_groups)]
    sizes = [0] * n_groups
    for i in order:
        g = min(range(n_groups), key=lambda g: sizes[g])
        groups[g].append(i)
        sizes[g] += len(chunks[i].fields)
    return [g for g in groups if g]


def _group_chunk_indices(
    chunks: list[FieldChunk], max_groups: int, item_sheet_name: str | None = None,
) -> list[list[int]]:
    """
    Bin-packs `chunks` (by index, so the caller can zip results straight
    back onto the original `chunks` list) into at most `max_groups` calls,
    balancing total FIELD COUNT per group so one group doesn't end up far
    larger than the others just because it happened to collect several big
    chunks.

    No-op (one chunk per group) whenever `chunks` already fits within
    `max_groups` -- grouping only ever kicks in when it's actually needed
    to hit the budget.

    `item_sheet_name`'s own chunks are ALWAYS bundled into one dedicated
    group of their own, NEVER mixed into a group with any other sheet's
    chunks -- confirmed real failure without this: a grouped call's own
    prompt tells the model "the default, correct answer for MOST sections
    below is an empty list", true for the auxiliary sheets sharing that
    call but NOT for the item sheet (which must return one row per line
    item); the model answered every item-sheet section empty too, across
    every group it was split into, for a real invoice that clearly had line
    items. Isolating the item sheet's chunks into their own group removes
    that competing "mostly empty" framing from the one sheet that can never
    afford it, at the cost of a single call combining all its own chunks'
    fields together (see this function's caller for the remaining budget
    given to every other sheet).
    """
    if max_groups <= 0 or len(chunks) <= max_groups:
        return [[i] for i in range(len(chunks))]

    item_idxs = [i for i, c in enumerate(chunks) if item_sheet_name and c.sheet == item_sheet_name]
    other_idxs = [i for i in range(len(chunks)) if i not in item_idxs]

    if not item_idxs or not other_idxs:
        # Nothing to isolate the item sheet FROM (either it has no chunks
        # here at all, or it's the ONLY sheet with chunks, e.g. a CSV
        # template with no auxiliary sheets) -- the old single-pass
        # bin-pack is already correct, and this path also still covers
        # every call site that doesn't care about item-sheet isolation.
        return _bin_pack(list(range(len(chunks))), chunks, max_groups)

    groups = [item_idxs]  # always exactly one call, however many fields it totals
    remaining_budget = max(max_groups - 1, 1)  # never zero -- `other_idxs` is non-empty here
    groups.extend(_bin_pack(other_idxs, chunks, remaining_budget))
    return groups


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


def _group_section_text(chunk: FieldChunk, known_invoice_numbers: list[str] | None = None) -> str:
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
        ) + _multi_invoice_note(known_invoice_numbers)
    else:
        row_count_rule = (
            "This sheet has no row-link field -- return AT MOST ONE row for it (empty list is the "
            "default, correct answer for most invoices)."
        )

    return _GROUP_SECTION_TEMPLATE.format(
        key=_chunk_key(chunk), sheet=chunk.sheet, n=len(chunk.fields),
        row_count_rule=row_count_rule, field_lines="\n".join(lines),
    )


def _build_group_prompt(chunks: list[FieldChunk], known_invoice_numbers: list[str] | None = None) -> str:
    sections = "\n".join(_group_section_text(c, known_invoice_numbers) for c in chunks)
    return _GROUP_PROMPT_HEADER.format(n_sections=len(chunks), sections=sections)


async def extract_chunk_group(
    client: AsyncOpenAI,
    invoice_text: str,
    tables_text: str,
    chunks: list[FieldChunk],
    model: str,
    shipment_type: str | None = None,
    known_invoice_numbers: list[str] | None = None,
) -> dict[str, tuple[list[dict[str, Any]], dict[str, int]]]:
    """
    Runs ONE LLM call covering ALL of `chunks` (typically several different
    sheets, bundled together to stay within MAX_LLM_CALLS_PER_TEMPLATE --
    see that constant's own docstring on the accuracy tradeoff this makes).
    Returns {chunk_key: (rows, usage)} so the caller (extract_all_chunks)
    can treat a grouped call's results exactly like several individual
    extract_chunk() calls -- usage is the SAME total call's usage repeated
    under every chunk_key in the group (there's only one real API call to
    attribute it to), summed once by the caller, not per key.

    `known_invoice_numbers`: see extract_chunk's own docstring -- same
    multi-invoice note applies to a multi_row chunk's own section here too.
    """
    system_prompt = _build_group_prompt(chunks, known_invoice_numbers)
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
    known_invoice_numbers: list[str] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    """
    Runs every FieldChunk for this template (item sheet's long tail +
    every auxiliary sheet) CONCURRENTLY -- one focused LLM call per chunk
    -- then merges same-sheet chunks' rows back together by their shared
    link field, so a sheet that needed N chunks still comes back as ONE
    row per item/entry.

    `known_invoice_numbers`: same signal orchestrator.py/reextraction.py
    already pass to the CORE extract_line_items() call when this document
    bundles more than one separate invoice -- threaded through to every
    multi_row chunk's own prompt here too (see _multi_invoice_note).
    Confirmed real failure without this: the core call correctly splits a
    combined document's items because it's told how many invoices there
    are; this chunked long-tail call previously had no such signal, so its
    own multi_row prompt (written for exactly one item table) left the
    model with no idea what to do when the document actually had several,
    and it answered every row empty across all of them.

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

    `shipment_type`: looked up against invoice_extractor.schema.generated --
    if a hand-editable static model file exists for this template (see
    tools/generate_template_models.py), each matching chunk uses it instead
    of a dynamically-built one. Omitted, every chunk is dynamic, same as
    before this existed.
    """
    exclude = {item_sheet_name: item_core_real_fields}
    chunks = build_field_chunks(schema, exclude=exclude, chunk_size=chunk_size, item_sheet_name=item_sheet_name)
    if not chunks:
        return {}, _empty_usage()

    client = AsyncOpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))
    total_usage = _empty_usage()
    # rows_by_chunk[i] holds chunks[i]'s own rows, regardless of whether it
    # came from its own individual call or was bundled into a grouped one --
    # the merge loop below is identical either way. `failed` tracks indices
    # whose call genuinely never returned (as opposed to returning zero
    # rows) -- those must NOT create an entry in `sheets` at all (a sheet
    # whose only chunk failed should come back absent, not as an empty
    # list implying "confirmed nothing printed").
    rows_by_chunk: list[list[dict[str, Any]]] = [[] for _ in chunks]
    failed: set[int] = set()

    if len(chunks) > MAX_LLM_CALLS_PER_TEMPLATE:
        groups = _group_chunk_indices(chunks, MAX_LLM_CALLS_PER_TEMPLATE, item_sheet_name=item_sheet_name)
        logger.info(
            "Chunked extraction: %d field chunk(s) across %d sheet(s) exceeds the %d-call budget -- "
            "bundling into %d grouped call(s) instead (MAX_LLM_CALLS_PER_TEMPLATE), model=%s",
            len(chunks), len({c.sheet for c in chunks}), MAX_LLM_CALLS_PER_TEMPLATE, len(groups), model,
        )
        group_results = await asyncio.gather(
            *(extract_chunk_group(client, invoice_text, tables_text, [chunks[i] for i in idxs], model,
                                   shipment_type=shipment_type, known_invoice_numbers=known_invoice_numbers)
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
            # One real API call covers this whole group -- its usage must be
            # counted ONCE, not once per chunk bundled inside it (every
            # chunk_key in group_result carries an identical copy of that
            # one call's own usage, by construction of extract_chunk_group).
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
            *(extract_chunk(client, invoice_text, tables_text, c, model,
                             shipment_type=shipment_type, known_invoice_numbers=known_invoice_numbers) for c in chunks),
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

    merged: dict[str, dict[str, dict[str, Any]]] = {}  # sheet -> {link_value: row}
    for i, (chunk, rows) in enumerate(zip(chunks, rows_by_chunk)):
        if i in failed:
            continue
        bucket = merged.setdefault(chunk.sheet, {})
        for idx, row in enumerate(rows):
            if chunk.link_field:
                link_val = str(row.get(chunk.link_field))
            elif chunk.multi_row:
                # No link column on this sheet at all -- matched back to the
                # right item by POSITION instead (see build_field_chunks'
                # own docstring on `multi_row`), so every chunk of this
                # sheet must be walked in the SAME order, position by
                # position, for this to line up. extract_chunk/
                # extract_chunk_group keep an empty placeholder row for a
                # null position specifically so this index lines up even
                # when one chunk found nothing for that item but another did.
                link_val = f"_pos_{idx}"
            else:
                link_val = "_single_"
            bucket.setdefault(link_val, {}).update(row)

    sheets = {sheet: list(rows_by_link.values()) for sheet, rows_by_link in merged.items()}
    return sheets, total_usage
