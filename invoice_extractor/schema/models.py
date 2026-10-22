"""
models.py
---------
Post-extraction VALIDATION entry point: `validate_row_against_model()` is
what validate.py actually calls, per sheet, per row, to enforce the
complete column set ("" for anything not found) on the final output.

The Pydantic model-BUILDING logic itself (`build_sheet_models`, and the
type-mapping/description rules it relies on) now lives in
invoice_extractor/schema/pydantic_models.py -- the one shared place for
every kind of Pydantic model this pipeline builds (validation models here,
plus the per-chunk and grouped extraction models chunked_extraction.py
sends to the LLM). `build_sheet_models` is re-exported below so existing
`from invoice_extractor.schema.models import build_sheet_models` imports
keep working unchanged.

Usage:
    from invoice_extractor.schema.schema import load_template_schema
    from invoice_extractor.schema.models import build_sheet_models

    schema = load_template_schema("templates/EXP_TEMPLET.xlsx")
    models = build_sheet_models(schema)
    # models["ITEM"] is a Pydantic model class with all ITEM fields as Optional
"""
from __future__ import annotations
from typing import Any

from pydantic import BaseModel

from invoice_extractor.schema.pydantic_models import build_sheet_models

__all__ = ["build_sheet_models", "validate_row_against_model"]


def validate_row_against_model(
    row: dict[str, Any],
    model_class: type[BaseModel],
    sheet_name: str,
    row_idx: int,
    warnings: list[str],
    all_fields: list[str] | None = None,
) -> dict[str, Any] | None:
    """
    Validate a single row dict against the Pydantic model.

    - Strips any keys not in the model (with warnings).
    - Coerces types where possible.
    - Guarantees ALL template fields for the sheet are present in the exact
      order defined in the template schema; anything the row didn't
      populate is "" (empty string) -- no guessed defaults.
    """
    valid_fields = set(model_class.model_fields.keys())
    fields_order = all_fields if all_fields is not None else list(model_class.model_fields.keys())

    # Filter to only valid fields
    filtered: dict[str, Any] = {}
    for key, val in row.items():
        if key in valid_fields:
            filtered[key] = val
        else:
            warnings.append(
                f"[{sheet_name}][row {row_idx}] pydantic rejected "
                f"unknown field '{key}'"
            )

    if not filtered and not any(v for v in row.values() if v):
        return None

    # Validate via Pydantic (lenient — catches type errors)
    try:
        validated = model_class.model_validate(filtered)
        validated_dict = {
            k: v for k, v in validated.model_dump().items() if v is not None
        }
    except Exception as e:
        warnings.append(
            f"[{sheet_name}][row {row_idx}] pydantic validation error: {e}"
        )
        validated_dict = {k: v for k, v in filtered.items() if v is not None}

    # Construct full row containing EVERY template field in order, "" for
    # anything not populated.
    result: dict[str, Any] = {}
    for field_name in fields_order:
        value = validated_dict.get(field_name)
        result[field_name] = value if value not in (None, "") else ""

    return result
