"""
pipeline_log.py
----------------
Terminal visibility into what actually reaches the LLM, per request: when a
PDF is received, whether it turned out digital or scanned (and which OCR
engine handled it), how much text came out of extraction, and -- per LLM
call -- the text sent in (truncated, with a length note, so a dense
multi-page invoice doesn't flood the terminal) and the Pydantic model
backing that call's structured output (chunked_extraction.py's chunk
calls; the two core header/items calls in llm_extract.py don't use
structured outputs, so only their input text is printed).

Deliberately NOT included: the system prompt text itself -- these prints
are about what DATA goes in, not the (already-documented, hand-tuned)
instructions wrapping it.
"""
from __future__ import annotations

from typing import Any

_PREVIEW_CHARS = 2000


def describe_extraction_method(method: str) -> str:
    """Human label for pdf_reader.extract_text's `method` return value
    ("2d_layout_canvas", "docling", "ocr_rapidocr", optionally suffixed
    "+rotation_recovered")."""
    base = method.split("+")[0]
    if base == "2d_layout_canvas":
        label = "digital (native text layer)"
    elif base == "docling":
        label = "scanned (OCR via Docling)"
    elif base.startswith("ocr_"):
        label = f"scanned (OCR via {base[len('ocr_'):]})"
    else:
        label = f"scanned ({base})"
    if "+rotation_recovered" in method:
        label += ", rotation-recovered"
    return label


def print_pdf_received(pdf_path: str) -> None:
    print(f"[pipeline] PDF received: {pdf_path}")


def print_extraction_result(method: str, text: str) -> None:
    print(f"[pipeline] document type: {describe_extraction_method(method)}")
    print(f"[pipeline] extracted text: {len(text)} chars")


def print_llm_input_text(label: str, text: str, max_chars: int = _PREVIEW_CHARS) -> None:
    """The text portion of one LLM call's user turn (invoice/table text,
    NOT the system prompt) -- truncated with a length note past
    `max_chars` so a long document doesn't flood the terminal."""
    n = len(text)
    preview = text[:max_chars] + f"\n... [truncated -- {n} chars total]" if n > max_chars else text
    print(f"\n----- [{label}] TEXT SENT TO LLM ({n} chars) -----\n{preview}\n")


def print_pydantic_model(label: str, model: Any) -> None:
    """Field names of the Pydantic model backing an LLM call's structured
    output -- for a chunk's "rows: [RowModel]" container, also the nested
    row model's own fields (what actually gets asked for per row)."""
    fields = list(model.model_fields.keys())
    row_fields = None
    rows_field = model.model_fields.get("rows")
    if rows_field is not None:
        args = getattr(rows_field.annotation, "__args__", None)
        row_model = args[0] if args else None
        if row_model is not None and hasattr(row_model, "model_fields"):
            row_fields = list(row_model.model_fields.keys())
    print(f"----- [{label}] PYDANTIC MODEL: {model.__name__} -----")
    print(f"  top-level fields: {fields}")
    if row_fields is not None:
        print(f"  row fields ({len(row_fields)}): {row_fields}")
    print()
