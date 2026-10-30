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
    valid_fields = set(model_class.model_fields.keys())
    fields_order = all_fields if all_fields is not None else list(model_class.model_fields.keys())

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

    result: dict[str, Any] = {}
    for field_name in fields_order:
        value = validated_dict.get(field_name)
        result[field_name] = value if value not in (None, "") else ""

    return result
