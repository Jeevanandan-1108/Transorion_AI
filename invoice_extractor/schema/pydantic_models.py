from __future__ import annotations
import importlib
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel, Field, create_model

from invoice_extractor.schema.schema import field_description_text

if TYPE_CHECKING:
    from invoice_extractor.extraction.chunked_extraction import FieldChunk


def is_number_type(schema_type: str) -> bool:
    return schema_type == "number"


def pydantic_type_for(schema_type: str) -> type:
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
    return create_model(model_name, rows=(list[row_model], Field(default_factory=list)))


def build_sheet_models(schema: dict[str, dict[str, Any]]) -> dict[str, type[BaseModel]]:
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
    return f"{chunk.sheet}__chunk{chunk.index}"


def row_model_for_chunk(chunk: "FieldChunk") -> type[BaseModel]:
    return row_model_for_fields(
        f"{chunk.sheet}Chunk{chunk.index}Row",
        list(chunk.fields),
        chunk.types,
        chunk.aliases,
        chunk.descriptions,
        link_field=chunk.link_field,
    )


def rows_model_for_chunk(chunk: "FieldChunk", row_model: type[BaseModel] | None = None) -> type[BaseModel]:
    if row_model is None:
        row_model = row_model_for_chunk(chunk)
    return rows_wrapper_model(f"{chunk.sheet}Chunk{chunk.index}Rows", row_model)


def group_rows_model(chunks: list["FieldChunk"], shipment_type: str | None) -> type[BaseModel]:
    field_defs: dict[str, Any] = {}
    for chunk in chunks:
        static_row_model = generated_chunk_models(shipment_type).get((chunk.sheet, chunk.index))
        row_model = static_row_model or row_model_for_chunk(chunk)
        field_defs[chunk_key(chunk)] = (list[row_model], Field(default_factory=list))
    return create_model("ChunkGroupRows", **field_defs)


@lru_cache(maxsize=None)
def generated_chunk_models(shipment_type: str | None) -> dict[tuple[str, int], type[BaseModel]]:
    if not shipment_type:
        return {}
    try:
        module = importlib.import_module(f"invoice_extractor.schema.generated.{shipment_type}")
    except ModuleNotFoundError:
        return {}
    return getattr(module, "CHUNK_MODELS", {})
