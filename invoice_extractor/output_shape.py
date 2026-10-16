"""
output_shape.py
-----------------
Lets you control exactly what the final result JSON contains -- which
top-level keys, which header fields, and which sheet columns -- instead
of always emitting everything the pipeline happens to produce.

Edit OUTPUT_CONFIG below directly, OR point the OUTPUT_SHAPE_CONFIG
environment variable at your own JSON file with the same three keys.
Leave any of the three as `null`/`None` to mean "no filtering -- include
everything" -- that's the default, so nothing changes until you actually
set something.

Example OUTPUT_CONFIG that keeps only the business data, dropping every
pipeline/diagnostic field (detection, scan_quality, token_usage, ...):

    OUTPUT_CONFIG = {
        "top_level_keys": ["source_file", "shipment_type", "header", "sheets", "warnings"],
        "header_fields": None,   # keep every header field
        "sheet_fields": None,    # keep every sheet's every column
    }

Example that also trims the ITEM sheet down to a handful of columns:

    OUTPUT_CONFIG = {
        "top_level_keys": ["source_file", "header", "sheets"],
        "header_fields": ["invoice_number", "invoice_date", "supplier_name", "invoice_total"],
        "sheet_fields": {"ITEM": ["Item_Ser_No", "Item_Desc", "Item_Qty", "Item_Unit_Price"]},
    }

Applied by main.py (to the JSON written to disk), and by api.py (to the
JSON returned from /extract and /extract-batch). NOT applied to the
/save-pdf results callback (_send_results_callback in api.py) or to the
file api.py/main.py save for internal routing (valid vs. validation_failed
folder) -- both of those need the FULL result to make that decision /
keep the existing external integration contract intact; shaping only
touches what you, the human looking at the output, actually see.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any

# `None` for any of the three means "no filtering -- include everything."
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
    """
    Filters `result` (the full pipeline output dict) down to exactly what
    the config names. A name that doesn't exist in `result` is simply
    skipped -- never raises, same "no guessed values, no invented
    structure" stance as the rest of this pipeline. Does not mutate
    `result` itself.
    """
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
