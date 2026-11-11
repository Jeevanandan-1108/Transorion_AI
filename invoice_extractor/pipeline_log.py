from __future__ import annotations

from typing import Any

_PREVIEW_CHARS = 2000


def describe_extraction_method(method: str) -> str:
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
    n = len(text)
    preview = text[:max_chars] + f"\n... [truncated -- {n} chars total]" if n > max_chars else text
    print(f"\n----- [{label}] TEXT SENT TO LLM ({n} chars) -----\n{preview}\n")


def print_pydantic_model(label: str, model: Any) -> None:
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
