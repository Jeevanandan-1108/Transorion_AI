"""
pydantic_models.py
-------------------
Single, central place for every Pydantic model this pipeline builds from a
template's schema. Before this module existed, the same ideas (how a
schema type maps to a Python type, how a field's description gets built,
how a row model gets assembled) were written three times, independently,
in models.py and chunked_extraction.py -- with the type mapping having
already drifted slightly out of sync between the two copies. This module
is the fix: one implementation, three consumers.

The three kinds of Pydantic model in this pipeline, all built from here:

1. VALIDATION models (`build_sheet_models`) -- one model per sheet,
   covering every column that sheet defines. Used AFTER extraction, by
   validate.py's `validate_row_against_model()`, to enforce the complete
   column set ("" for anything not found) on the final output row. Never
   sent to the LLM.

2. PER-CHUNK extraction models (`row_model_for_chunk` / `rows_model_for_chunk`)
   -- one small model per <=12-field chunk (see chunked_extraction.py's
   FieldChunk / CHUNK_SIZE). THIS is what's actually sent to OpenAI via
   `response_format=` for the long-tail extraction calls.

3. GROUPED multi-chunk models (`group_rows_model`) -- when several chunks
   get bundled into one call to stay within a per-template call budget
   (see chunked_extraction.MAX_LLM_CALLS_PER_TEMPLATE), one combined model
   with one key per chunk.

Changing how a Pydantic model gets built from schema data -- type
inference, how a field's description is composed, defaults, extra-field
handling -- means changing it here, once; every one of the three consumers
above picks it up automatically, instead of needing the same edit made (or
missed) in two or three different files.

Also holds `generated_chunk_models()`: the lookup for a hand-editable
static model file under invoice_extractor/schema/generated/<type>.py (see
tools/generate_template_models.py), which overrides #2 per chunk when one
exists for that template.
"""
from __future__ import annotations
import importlib
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel, Field, create_model

from invoice_extractor.schema.schema import field_description_text

if TYPE_CHECKING:
    # FieldChunk lives in chunked_extraction.py, which imports model-building
    # functions FROM this module -- importing it for real here would be
    # circular. `from __future__ import annotations` (above) already makes
    # every annotation in this file a lazy string, so this import only ever
    # runs for a type checker/IDE, never at actual runtime.
    from invoice_extractor.extraction.chunked_extraction import FieldChunk


def is_number_type(schema_type: str) -> bool:
    """The one place "is this schema type a number" is decided -- schema.py's
    own `types` dict only ever says "number" or "string" today, but any
    future type should only need a decision made here, not per-consumer."""
    return schema_type == "number"


def pydantic_type_for(schema_type: str) -> type:
    """
    The ONE place every Pydantic model in this pipeline gets a field's
    Python type from.

    "number" -> Optional[float | str]: the LLM sometimes returns a number
    as a quoted string; validate.py's own coercion handles turning that
    into a real number afterward -- this layer only needs to accept it
    without raising.

    Anything else -> Optional[str | int | float]: lenient for the same
    reason in the other direction -- a text-shaped field (e.g. an item/
    serial number column) routinely comes back as a bare int/float rather
    than a quoted string, and that's a normal, harmless shape mismatch, not
    a real data problem worth a validation warning over. Confirmed real
    regression without this leniency: Item_No: 1 (an int) on an otherwise
    correctly-populated row started raising a pydantic validation error
    here once this was tightened to Optional[str] only.
    """
    return Optional[float | str] if is_number_type(schema_type) else Optional[str | int | float]


LINK_FIELD_DESCRIPTION = (
    "Row/item identifier this data belongs to -- copy EXACTLY as printed, "
    "matching the same identifier used elsewhere on this document for that "
    "row, so this answer can be matched back to the right row afterward."
)


def row_model_for_fields(
    model_name: str,
    fields: list[str],
    types: dict[str, str],
    aliases: dict[str, list[str]],
    descriptions: dict[str, str] | None = None,
    link_field: str | None = None,
    forbid_extra: bool = False,
) -> type[BaseModel]:
    """
    Builds ONE Pydantic model covering `fields` (plus `link_field`, if
    given, with its own fixed description) -- the single row-model
    constructor shared by `build_sheet_models()` (validation, every column
    a sheet defines) and `row_model_for_chunk()` (extraction, one chunk's
    own ~12 columns).

    `forbid_extra=True` rejects any key not in `fields` -- used only by the
    validation models (`build_sheet_models`) as a safety net against a
    mapper drifting from real column names. Extraction-facing models leave
    this False: OpenAI's structured outputs already guarantee the response
    can't contain a key outside the schema it was given, so there is
    nothing for `forbid_extra` to catch there.
    """
    field_defs: dict[str, Any] = {}
    if link_field:
        field_defs[link_field] = (Optional[str], Field(default=None, description=LINK_FIELD_DESCRIPTION))
    for field_name in fields:
        field_defs[field_name] = (
            pydantic_type_for(types.get(field_name, "string")),
            Field(default=None, description=field_description_text(field_name, aliases, descriptions or {})),
        )
    model_class = create_model(model_name, **field_defs)
    if forbid_extra:
        model_class.model_config = {"extra": "forbid"}
    return model_class


def rows_wrapper_model(model_name: str, row_model: type[BaseModel]) -> type[BaseModel]:
    """Wraps a row model into the {"rows": [...]} container a per-chunk
    extraction call actually sends as its `response_format=`."""
    return create_model(model_name, rows=(list[row_model], Field(default_factory=list)))


def build_sheet_models(schema: dict[str, dict[str, Any]]) -> dict[str, type[BaseModel]]:
    """
    For each sheet in the template schema, a VALIDATION-only Pydantic model
    (never sent to the LLM) covering every column that sheet defines. Used
    by validate.py's `validate_row_against_model()` to enforce the complete
    column set / "" defaults on the final output row, and to catch (with a
    warning) any key a mapper returns that isn't a real column.

    Returns {sheet_name: ModelClass}.
    """
    return {
        sheet_name: row_model_for_fields(
            f"{sheet_name}Row",
            sheet_info["fields"],
            sheet_info["types"],
            sheet_info.get("aliases", {}),
            sheet_info.get("descriptions", {}),
            forbid_extra=True,
        )
        for sheet_name, sheet_info in schema.items()
    }


def chunk_key(chunk: "FieldChunk") -> str:
    """Stable, unique-per-template identifier for one chunk -- used as the
    JSON key for that chunk's own rows inside a grouped call's combined
    response. Doesn't need to be a valid Python identifier (create_model
    accepts arbitrary string keys via dict unpacking, same as every other
    dynamically-built model in this module)."""
    return f"{chunk.sheet}__chunk{chunk.index}"


def row_model_for_chunk(chunk: "FieldChunk") -> type[BaseModel]:
    """One row of `chunk`'s own sheet. This is the model actually sent to
    OpenAI (`response_format=`) for the long-tail extraction calls, when no
    hand-editable static override exists for this chunk (see
    `generated_chunk_models`)."""
    return row_model_for_fields(
        f"{chunk.sheet}Chunk{chunk.index}Row",
        list(chunk.fields),
        chunk.types,
        chunk.aliases,
        chunk.descriptions,
        link_field=chunk.link_field,
    )


def rows_model_for_chunk(chunk: "FieldChunk", row_model: type[BaseModel] | None = None) -> type[BaseModel]:
    """`row_model`, if given, overrides the normal dynamically-built one --
    see `generated_chunk_models` below for where that comes from."""
    if row_model is None:
        row_model = row_model_for_chunk(chunk)
    return rows_wrapper_model(f"{chunk.sheet}Chunk{chunk.index}Rows", row_model)


def group_rows_model(chunks: list["FieldChunk"], shipment_type: str | None) -> type[BaseModel]:
    """One combined response model for a GROUP of chunks (possibly
    spanning several different sheets) -- one key per chunk (see
    `chunk_key`), each a list of that chunk's own row model, same
    static-override lookup as the single-chunk path."""
    field_defs: dict[str, Any] = {}
    for chunk in chunks:
        static_row_model = generated_chunk_models(shipment_type).get((chunk.sheet, chunk.index))
        row_model = static_row_model or row_model_for_chunk(chunk)
        field_defs[chunk_key(chunk)] = (list[row_model], Field(default_factory=list))
    return create_model("ChunkGroupRows", **field_defs)


@lru_cache(maxsize=None)
def generated_chunk_models(shipment_type: str | None) -> dict[tuple[str, int], type[BaseModel]]:
    """
    {(sheet_name, chunk_index): RowModel} from
    invoice_extractor.schema.generated.<shipment_type>, if that module
    exists -- {} otherwise, so a template with no generated file (the
    overwhelming majority -- see tools/generate_template_models.py) behaves
    exactly as before: every chunk's Pydantic model is built dynamically by
    `row_model_for_chunk`.

    A generated module's classes use a Python-safe attribute name per field
    with the template's own real column name as that field's `alias` (see
    tools/generate_template_models.py) -- callers always read a row back
    out with `model_dump(by_alias=True)` so the real column name comes back
    regardless of which path built the model.
    """
    if not shipment_type:
        return {}
    try:
        module = importlib.import_module(f"invoice_extractor.schema.generated.{shipment_type}")
    except ModuleNotFoundError:
        return {}
    return getattr(module, "CHUNK_MODELS", {})
