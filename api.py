from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import tempfile
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.responses import JSONResponse
import uvicorn

from invoice_extractor.schema.schema import load_template_schema
from invoice_extractor.pdf.reader import extract_text
from invoice_extractor.extraction.reextraction import extract_and_validate, MAX_REEXTRACTION_ATTEMPTS
from invoice_extractor.extraction.orchestrator import extract_and_validate_orchestrated
from invoice_extractor.detect_type import detect_shipment_type
from invoice_extractor.schema.templates_config import TEMPLATE_PATH, TYPE_ALIASES
from invoice_extractor.output_shape import apply_output_shape
from invoice_extractor.pipeline_log import print_pdf_received, print_extraction_result
import re
# Accepts an alias (e.g. "Focus export Template") in ?type=/shipment_type=
# right alongside its canonical registered name ("export") -- _process_pdf
# resolves it back to the canonical name immediately, before anything else
# (schema lookup, hand-tuned dispatch in validate.py/llm_extract.py) ever
# sees it, so this is the ONLY place an alias needs to be recognized.
_TYPE_PATTERN = "^(" + "|".join(re.escape(t) for t in sorted(set(TEMPLATE_PATH) | set(TYPE_ALIASES))) + ")$"

logger = logging.getLogger("invoice_extractor")
logging.basicConfig(level=logging.INFO)

DEFAULT_MODEL = os.environ.get("EXTRACTOR_MODEL", "gpt-5-nano")


SAVE_DIR = Path(os.environ.get("EXTRACTOR_SAVE_DIR", Path(__file__).resolve().parent / "results"))

RECEIVED_PDF_DIR = Path(os.environ.get("EXTRACTOR_PDF_DIR", Path(__file__).resolve().parent / "received_pdfs"))

_schemas: dict[str, dict] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    for shipment_type, path in TEMPLATE_PATH.items():
        if not path.exists():
            raise RuntimeError(f"Template not found at {path}")
        _schemas[shipment_type] = load_template_schema(path)
    yield


app = FastAPI(
    title="Invoice Extractor API",
    description="Extracts invoice PDFs into the customs export/import template schema.",
    lifespan=lifespan,
)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "templates": {t: list(s.keys()) for t, s in _schemas.items()},
        "default_model": DEFAULT_MODEL,
    }


async def _process_pdf(
    tmp_path: str, filename: str, model: str, requested_type: str | None, force_ocr: bool | None = None,
    max_retries: int = MAX_REEXTRACTION_ATTEMPTS, orchestrate: bool = True,
) -> dict:
    # An alias (e.g. "Focus export Template") resolves to its canonical
    # name ("export") here, before anything else sees it -- see
    # TYPE_ALIASES' own docstring for why this must happen exactly once,
    # this early.
    requested_type = TYPE_ALIASES.get(requested_type, requested_type)
    start_time = time.perf_counter()
    print_pdf_received(filename)
    text, tables_text, method, ocr_time_seconds, scan_quality = await asyncio.to_thread(
        extract_text, tmp_path, force_ocr=force_ocr,
    )
    if not text:
        raise HTTPException(
            status_code=422,
            detail=f"No text could be extracted from '{filename}', even with OCR.",
        )
    print_extraction_result(method, text)

    scan_warnings: list[str] = []
    if scan_quality and not scan_quality["valid"]:
        scan_warnings = [f"scan quality: {reason}" for reason in scan_quality["reasons"]]

    detection = None
    if requested_type in TEMPLATE_PATH:
        resolved_type = requested_type
    else:
        detection = detect_shipment_type(text)
        if detection.shipment_type == "unknown":
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Could not confidently determine whether '{filename}' is an import or "
                    f"export shipment (export_score={detection.export_score}, "
                    f"import_score={detection.import_score}). Auto-detection only distinguishes "
                    f"import vs. export -- resupply with an explicit ?type=, one of: "
                    f"{', '.join(sorted(TEMPLATE_PATH))}."
                ),
            )
        resolved_type = detection.shipment_type

    schema = _schemas[resolved_type]

    try:
        if orchestrate:
            extraction = await extract_and_validate_orchestrated(
                text, schema, resolved_type, tables_text=tables_text, max_retries=max_retries,
            )
        else:
            extraction = await extract_and_validate(
                text, schema, resolved_type, model=model, tables_text=tables_text, max_retries=max_retries,
            )
    except Exception as e:
        logger.exception(f"LLM extraction failed for '{filename}'")
        raise HTTPException(
            status_code=502,
            detail=f"LLM extraction failed for '{filename}': {e}",
        )

    extraction_time_seconds = round(time.perf_counter() - start_time, 2)

    return {
        "source_file": filename,
        "shipment_type": resolved_type,
        "detection": (
            {"export_score": detection.export_score, "import_score": detection.import_score, "signal": detection.signal}
            if detection else None
        ),
        "extraction_method": method,
        "ocr_time_seconds": ocr_time_seconds,
        "scan_quality": scan_quality,
        "model": model if not orchestrate else extraction.get("models_used", [model])[-1],
        "complexity": extraction.get("complexity") if orchestrate else None,
        "models_used": extraction.get("models_used") if orchestrate else [model],
        "extraction_time_seconds": extraction_time_seconds,
        "token_usage": extraction["token_usage"],
        **(
            {"invoices": extraction["invoices"]} if "invoices" in extraction
            else {"header": extraction["header"], "sheets": extraction["sheets"]}
        ),
        "warnings": scan_warnings + extraction["warnings"],
        "validation": extraction["validation"],
        "attempts": extraction["attempts"],
        "extraction_status": extraction["extraction_status"],
    }


def _save_result(result: dict, used: set[str] | None = None) -> None:
    try:
        stem = Path(str(result.get("source_file") or "unnamed")).stem or "unnamed"
        subdir = "validation_success" if result.get("validation", {}).get("valid") else "validation_failed"
        name, n = stem, 1
        while used is not None and f"{subdir}/{name}".lower() in used:
            n += 1
            name = f"{stem}_{n}"
        if used is not None:
            used.add(f"{subdir}/{name}".lower())
        folder = SAVE_DIR / subdir / name
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}.json"
        path.write_text(json.dumps(apply_output_shape(result), indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Saved extraction -> %s", path)
    except Exception:
        logger.exception("Could not save extraction for %r to %s", result.get("source_file"), SAVE_DIR)


async def extract(
    file: UploadFile = File(...),
    model: str = Query(default=None),
    type: str | None = Query(default=None, pattern=_TYPE_PATTERN),
    force_ocr: bool | None = Query(default=None),
    max_retries: int = Query(default=MAX_REEXTRACTION_ATTEMPTS, ge=0,
                              description="Re-extraction attempts allowed after a validation failure."),
    orchestrate: bool = Query(default=True,
                               description="Use the LangGraph multi-model orchestrator (picks the "
                                            "model per attempt by PDF complexity, see orchestrator.py). "
                                            "False uses the single fixed `model` for every attempt instead."),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    model = model or DEFAULT_MODEL

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    try:
        tmp.write(await file.read())
        tmp.close()
        result = await _process_pdf(
            tmp.name, file.filename, model, type, force_ocr=force_ocr,
            max_retries=max_retries, orchestrate=orchestrate,
        )
        _save_result(result)
    finally:
        try:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)
        except Exception:
            pass

    return JSONResponse(apply_output_shape(result))


async def extract_batch(
    files: list[UploadFile] = File(...),
    model: str = Query(default=None),
    type: str | None = Query(default=None, pattern=_TYPE_PATTERN),
    force_ocr: bool | None = Query(default=None),
    max_retries: int = Query(default=MAX_REEXTRACTION_ATTEMPTS, ge=0,
                              description="Re-extraction attempts allowed after a validation failure."),
    orchestrate: bool = Query(default=True,
                               description="Use the LangGraph multi-model orchestrator (picks the "
                                            "model per attempt by PDF complexity, see orchestrator.py). "
                                            "False uses the single fixed `model` for every attempt instead."),
):
    model = model or DEFAULT_MODEL
    results = []
    used_names: set[str] = set()

    for file in files:
        if not file.filename.lower().endswith(".pdf"):
            results.append({"source_file": file.filename, "error": "not a PDF, skipped"})
            continue
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        try:
            tmp.write(await file.read())
            tmp.close()
            result = await _process_pdf(
                tmp.name, file.filename, model, type, force_ocr=force_ocr,
                max_retries=max_retries, orchestrate=orchestrate,
            )
            _save_result(result, used_names)
            results.append(apply_output_shape(result))
        except HTTPException as e:
            results.append({"source_file": file.filename, "error": e.detail})
        except Exception as e:
            results.append({"source_file": file.filename, "error": str(e)})
        finally:
            try:
                if os.path.exists(tmp.name):
                    os.unlink(tmp.name)
            except Exception:
                pass
    return JSONResponse({"results": results})


def _document_type(extraction_method: str | None) -> str | None:
    if not extraction_method:
        return None
    return "selectable" if extraction_method == "2d_layout_canvas" else "scanned"


def _callback_config() -> tuple[str, dict[str, str]]:
    url = os.environ.get("EXTRACTOR_CALLBACK_URL", "http://192.168.2.46:8765/process_invoices/")
    headers = {"Content-Type": "application/json"}
    if os.environ.get("EXTRACTOR_CALLBACK_API_KEY"):
        headers["X-API-KEY"] = os.environ["EXTRACTOR_CALLBACK_API_KEY"]
    if os.environ.get("EXTRACTOR_CALLBACK_BEARER_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["EXTRACTOR_CALLBACK_BEARER_TOKEN"]
    return url, headers


def _invoice_counts(results: list[dict]) -> dict:
    valid = invalid = rejected = 0
    for r in results:
        if "error" in r:
            rejected += 1
            continue
        for inv in r.get("invoices") or [r]:
            if (inv.get("validation") or {}).get("valid"):
                valid += 1
            else:
                invalid += 1
    return {
        "total_invoices": valid + invalid + rejected,
        "valid_invoices": valid,
        "invalid_invoices": invalid,
        "rejected_invoices": rejected,
    }


async def _send_results_callback(
    job_id: int, results: list[dict],
    started_at: datetime | None = None, ended_at: datetime | None = None,
) -> dict:
    url, headers = _callback_config()
    if not url:
        return {"sent": False, "reason": "callback disabled (EXTRACTOR_CALLBACK_URL is empty)"}
    payload = {
        "job_id": job_id,
        "started_at": started_at.isoformat() if started_at else None,
        "ended_at": (ended_at or datetime.now(timezone.utc)).isoformat(),
        **_invoice_counts(results),
        "results": results,
    }
    print("=" * 70)
    print(f"[callback] POST {url}")
    print(f"[callback] headers: {headers}")
    print(f"[callback] payload:\n{json.dumps(payload, indent=2, ensure_ascii=False)}")
    print("=" * 70)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, content=json.dumps(
                payload, ensure_ascii=False).encode("utf-8"))
        ok = resp.is_success
        if not ok:
            logger.warning("Results callback for job %s got HTTP %s: %s", job_id, resp.status_code, resp.text[:300])
        else:
            logger.info("Results callback for job %s -> HTTP %s", job_id, resp.status_code)
        return {"sent": ok, "status_code": resp.status_code, **({} if ok else {"response": resp.text[:300]})}
    except Exception as e:
        logger.exception("Results callback for job %s failed", job_id)
        return {"sent": False, "error": str(e)}


@app.post("/save-pdf")
async def save_pdf(
    files: list[UploadFile] = File(...),
    job_id: int = Query(...),
    shipment_type: str = Query(..., pattern=_TYPE_PATTERN),
):
    started_at = datetime.now(timezone.utc)
    job_folder = RECEIVED_PDF_DIR / str(job_id)
    job_folder.mkdir(parents=True, exist_ok=True)

    entries: list[dict] = []
    used_names: set[str] = set()
    for file in files:
        if not file.filename:
            continue
        original_filename = Path(file.filename).name
        entry = {
            "job_id": job_id,
            "file_name": original_filename,
            "saved_path": None,
            "document_type": None,
            "extraction_status": "failed",
            "extracted_json": None,
            "error": None,
        }
        entries.append(entry)
        try:
            if not original_filename.lower().endswith(".pdf"):
                entry["error"] = "Only PDF files are accepted."
                continue
            stem, suffix = Path(original_filename).stem, Path(original_filename).suffix
            n, name = 1, original_filename
            while name.lower() in used_names:
                n += 1
                name = f"{stem}_{n}{suffix}"
            used_names.add(name.lower())
            pdf_path = job_folder / name
            pdf_path.write_bytes(await file.read())
            entry["saved_path"] = str(pdf_path)
        except Exception as e:
            logger.exception("save-pdf could not save %r", original_filename)
            entry["error"] = f"Could not save the file: {e}"
        finally:
            await file.close()

    used_result_names: set[str] = set()
    for entry in entries:
        if not entry["saved_path"]:
            continue
        try:
            extraction = await _process_pdf(
                entry["saved_path"], entry["file_name"], DEFAULT_MODEL, shipment_type,
                force_ocr=None, max_retries=MAX_REEXTRACTION_ATTEMPTS,
            )
            # job_id ahead of "source_file" -- the first key in the saved
            # JSON, the callback payload, and the HTTP response's own
            # extracted_json (not just the outer {"job_id": ..., "results":
            # [...]} wrapper _send_results_callback builds separately).
            extraction = {"job_id": job_id, **extraction}
            entry["extracted_json"] = extraction
            entry["document_type"] = _document_type(extraction.get("extraction_method"))
            entry["extraction_status"] = "success"
            _save_result(extraction, used_result_names)
        except HTTPException as e:
            entry["error"] = str(e.detail)
        except Exception as e:
            logger.exception("save-pdf extraction failed for %r", entry["file_name"])
            entry["error"] = str(e)

    callback_results = [
        {**e["extracted_json"], "document_type": e["document_type"]} if e["extracted_json"] is not None
        else {"source_file": e["file_name"], "document_type": None, "error": e["error"]}
        for e in entries
    ]
    callback = await _send_results_callback(
        job_id, callback_results, started_at=started_at, ended_at=datetime.now(timezone.utc),
    )

    shaped_entries = [
        {**e, "extracted_json": apply_output_shape(e["extracted_json"])} if e["extracted_json"] is not None else e
        for e in entries
    ]
    return JSONResponse({"job_id": job_id, "shipment_type": shipment_type, "results": shaped_entries, "callback": callback})

if __name__ == "__main__":
    uvicorn.run(
        "api:app",
        host ="0.0.0.0",
        port = 8000
    )
