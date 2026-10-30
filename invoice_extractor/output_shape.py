from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any

OUTPUT_CONFIG: dict[str, Any] = {
    "top_level_keys": None,
    "header_fields": None,
    "sheet_fields": None,
}


def _load_config() -> dict[str, Any]:
    path = os.environ.get("OUTPUT_SHAPE_CONFIG")
    if path and Path(path).exists():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return OUTPUT_CONFIG


def apply_output_shape(result: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config if config is not None else _load_config()
    shaped = dict(result)

    top_level_keys = config.get("top_level_keys")
    if top_level_keys:
        shaped = {k: shaped[k] for k in top_level_keys if k in shaped}

    header_fields = config.get("header_fields")
    if header_fields and isinstance(shaped.get("header"), dict):
        shaped["header"] = {k: shaped["header"][k] for k in header_fields if k in shaped["header"]}

    sheet_fields = config.get("sheet_fields")
    if sheet_fields and isinstance(shaped.get("sheets"), dict):
        new_sheets: dict[str, Any] = {}
        for sheet_name, rows in shaped["sheets"].items():
            wanted = sheet_fields.get(sheet_name)
            if not wanted or not isinstance(rows, list):
                new_sheets[sheet_name] = rows
                continue
            new_sheets[sheet_name] = [
                {k: row[k] for k in wanted if k in row} if isinstance(row, dict) else row
                for row in rows
            ]
        shaped["sheets"] = new_sheets

    return shaped
