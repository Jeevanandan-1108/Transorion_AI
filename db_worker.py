"""
db_worker.py
------------
Watches job_app_job. When a row's status becomes 'Queued':
  1. claims it (status -> 'Processing') so only one worker handles it,
  2. looks up its PDFs in job_app_document (job_id = job.id, is_delete = false),
  3. resolves each document's `file` column to a path on disk,
  4. runs the same extraction pipeline as api.py (_process_pdf),
  5. saves the source PDF + its result JSON together under
     results/<validation_success|validation_failed>/<job_id>/ (save_job_result),
  6. writes the per-document outcome back to job_app_document (status,
     kind, extraction_error -- see set_document_result),
  7. POSTs {"job_id", "results"} to the callback API (api._send_results_callback).

Run:
    python db_worker.py

Environment (nothing is hard-coded):
    DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT   PostgreSQL connection
    MEDIA_ROOT          folder that the `file` column is relative to
                        (e.g. "jobs/2026/10/x.pdf" -> <MEDIA_ROOT>/jobs/2026/10/x.pdf)
    POLL_SECONDS        fallback poll interval, default 10
    QUEUED_STATUS       default "Queued" (compared case-insensitively)
    PROCESSING_STATUS   default "Processing"
    FAILED_STATUS       default "Failed" (set only if NO document could be extracted)
    JOB_SHIPMENT_TYPE   optional "import" or "export" to override the DB value.
                        Normally the type comes from job_app_job.shipment_type_id
                        -> SHIPMENT_TYPE_TABLE.SHIPMENT_TYPE_VALUE_COLUMN; if that
                        is empty or unrecognised, it is auto-detected from the
                        invoice text instead.
    SHIPMENT_TYPE_TABLE         table holding shipment types, default
                                "admin_app_shipmenttype" -- set this to your
                                actual table name if it differs (one lookup
                                per job, not per document: job_app_job's own
                                shipment_type_id is shared by every document
                                in that job).
    SHIPMENT_TYPE_ID_COLUMN     its primary-key column, default "id"
    SHIPMENT_TYPE_VALUE_COLUMN  its "import"/"export" text column, default "type"
    plus everything api.py already uses (OPENAI_API_KEY, EXTRACTOR_CALLBACK_*, ...)

Wake-up: the worker LISTENs on the Postgres channel "job_queued" (see
queue_trigger.sql) so it reacts instantly, and ALSO polls every POLL_SECONDS,
so jobs are never missed if a notification is lost or the worker was down.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import select
import shutil
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

import api  # reuses _process_pdf, _save_result, _send_results_callback, _document_type
from invoice_extractor.schema.schema import load_template_schema
from invoice_extractor.schema.templates_config import TEMPLATE_PATH
from invoice_extractor.extraction.reextraction import MAX_REEXTRACTION_ATTEMPTS

logger = logging.getLogger("db_worker")
logging.basicConfig(level=logging.INFO)

CHANNEL = "job_queued"
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "10"))
QUEUED_STATUS = os.environ.get("QUEUED_STATUS", "Queued")
PROCESSING_STATUS = os.environ.get("PROCESSING_STATUS", "Processing")
FAILED_STATUS = os.environ.get("FAILED_STATUS", "Failed")
FORCED_TYPE = os.environ.get("JOB_SHIPMENT_TYPE") or None
# Table/column names for the shipment-type lookup -- NOT user input, so
# building the query with an f-string here is safe (same trust level as
# the job_app_job / job_app_document names already hardcoded below); this
# is operator config, set once per deployment, never per-request data.
SHIPMENT_TYPE_TABLE = os.environ.get("SHIPMENT_TYPE_TABLE", "admin_app_shipmenttype")
SHIPMENT_TYPE_ID_COLUMN = os.environ.get("SHIPMENT_TYPE_ID_COLUMN", "id")
SHIPMENT_TYPE_VALUE_COLUMN = os.environ.get("SHIPMENT_TYPE_VALUE_COLUMN", "type")
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", "."))
# job_app_document's OWN per-document status -- confirmed real value for a
# successfully-processed document is "Extracted" (seen directly in the
# database). No confirmed real value for a failed one yet -- "Failed" is a
# reasonable default, override via DOCUMENT_FAILED_STATUS if your schema
# uses something else.
DOCUMENT_EXTRACTED_STATUS = os.environ.get("DOCUMENT_EXTRACTED_STATUS", "Extracted")
DOCUMENT_FAILED_STATUS = os.environ.get("DOCUMENT_FAILED_STATUS", "Failed")


def _connect():
    return psycopg2.connect(
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "5432")),
    )


def claim_next_job(conn) -> dict | None:
    """Atomically take one Queued job and mark it Processing.
    FOR UPDATE SKIP LOCKED makes this safe with several workers."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            UPDATE job_app_job
               SET status = %s, updated_at = now()
             WHERE id = (
                   SELECT id FROM job_app_job
                    WHERE lower(status) = lower(%s) AND NOT is_delete
                    ORDER BY updated_at
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED)
            RETURNING id, number, shipment_type_id
            """,
            (PROCESSING_STATUS, QUEUED_STATUS),
        )
        row = cur.fetchone()
    conn.commit()
    return dict(row) if row else None


def fetch_documents(conn, job_id: int) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, file, original_name
              FROM job_app_document
             WHERE job_id = %s AND NOT is_delete
             ORDER BY id
            """,
            (job_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    conn.commit()
    return rows


def lookup_shipment_type(conn, shipment_type_id: int | None) -> str | None:
    """Map job_app_job.shipment_type_id -> 'import' / 'export' -- ONE lookup
    per job (job_app_job's own shipment_type_id is shared by every document
    in that job, not stored per-document). Table/column names come from
    SHIPMENT_TYPE_TABLE / SHIPMENT_TYPE_ID_COLUMN / SHIPMENT_TYPE_VALUE_COLUMN
    (defaults: admin_app_shipmenttype.id / .type) -- set these env vars if
    your actual schema uses different names. Returns None if unset or
    unrecognised (the pipeline then auto-detects from the invoice text)."""
    if shipment_type_id is None:
        return None
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {SHIPMENT_TYPE_VALUE_COLUMN} FROM {SHIPMENT_TYPE_TABLE} "
            f"WHERE {SHIPMENT_TYPE_ID_COLUMN} = %s AND NOT is_delete",
            (shipment_type_id,),
        )
        row = cur.fetchone()
    conn.commit()
    value = (row[0] or "").strip().lower() if row else ""
    return value if value in ("import", "export") else None


def set_job_status(conn, job_id: int, status: str) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE job_app_job SET status = %s, updated_at = now() WHERE id = %s", (status, job_id))
    conn.commit()


def set_document_result(
    conn, doc_id: int, status: str, kind: str | None = None, error: str | None = None,
) -> None:
    """Writes the per-document outcome back to job_app_document -- confirmed
    real columns: status ("Extracted" for a successful document, seen
    directly in the database), kind ("selectable"/"scanned", same
    vocabulary as api._document_type()), extraction_error (set on failure,
    cleared to NULL on success so a retried document doesn't keep showing
    a stale error from a previous attempt)."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE job_app_document SET status = %s, kind = %s, extraction_error = %s, updated_at = now() "
            "WHERE id = %s",
            (status, kind, error, doc_id),
        )
    conn.commit()


def resolve_path(file_value: str) -> Path:
    p = Path(file_value)
    return p if p.is_absolute() else MEDIA_ROOT / p


def save_job_result(job_id: int, name: str, pdf_path: Path, extraction: dict, used: set[str]) -> None:
    """Saves the extraction JSON AND a copy of the source PDF TOGETHER, in
    one folder per JOB (not per PDF, since several documents can belong to
    the same job and should sit together):

        SAVE_DIR/<validation_success|validation_failed>/<job_id>/<name>.pdf
        SAVE_DIR/<validation_success|validation_failed>/<job_id>/<name>.json

    This replaces calling api._save_result() directly (which groups by PDF
    stem instead of job_id, and never copies the source PDF) -- same
    success/failure routing and output shaping as that function, same
    "never raises" contract (a disk problem must not crash a finished,
    already-paid-for extraction), but reuses `used` the same way
    _save_result does to avoid two same-named documents in one job
    silently overwriting each other's files.
    """
    try:
        subdir = "validation_success" if extraction.get("validation", {}).get("valid") else "validation_failed"
        folder = api.SAVE_DIR / subdir / str(job_id)
        folder.mkdir(parents=True, exist_ok=True)

        stem = Path(name).stem or "unnamed"
        final_stem, n = stem, 1
        while f"{subdir}/{final_stem}".lower() in used:
            n += 1
            final_stem = f"{stem}_{n}"
        used.add(f"{subdir}/{final_stem}".lower())

        shutil.copy2(pdf_path, folder / f"{final_stem}{pdf_path.suffix or '.pdf'}")
        json_path = folder / f"{final_stem}.json"
        json_path.write_text(
            json.dumps(api.apply_output_shape(extraction), indent=2, ensure_ascii=False), encoding="utf-8",
        )
        logger.info("Job %s: saved PDF + JSON -> %s", job_id, folder)
    except Exception:
        logger.exception("Job %s: could not save result for %s", job_id, pdf_path)


async def process_job(conn, job: dict) -> None:
    job_id = job["id"]
    docs = await asyncio.to_thread(fetch_documents, conn, job_id)
    shipment_type = FORCED_TYPE or await asyncio.to_thread(lookup_shipment_type, conn, job["shipment_type_id"])
    logger.info("Job %s (%s): %d document(s), shipment type: %s",
                job_id, job["number"], len(docs), shipment_type or "auto-detect")

    results: list[dict] = []
    used_names: set[str] = set()
    for doc in docs:
        name = doc["original_name"] or Path(doc["file"]).name
        path = resolve_path(doc["file"])
        if not path.is_file():
            logger.error("Job %s: file not found: %s", job_id, path)
            results.append({"source_file": name, "document_type": None, "error": f"File not found: {path}"})
            await asyncio.to_thread(
                set_document_result, conn, doc["id"], DOCUMENT_FAILED_STATUS, None, f"File not found: {path}",
            )
            continue
        try:
            extraction = await api._process_pdf(
                str(path), name, api.DEFAULT_MODEL, shipment_type,
                force_ocr=None, max_retries=MAX_REEXTRACTION_ATTEMPTS,
            )
            await asyncio.to_thread(save_job_result, job_id, name, path, extraction, used_names)
            document_type = api._document_type(extraction.get("extraction_method"))
            results.append({**extraction, "document_type": document_type})
            await asyncio.to_thread(
                set_document_result, conn, doc["id"], DOCUMENT_EXTRACTED_STATUS, document_type, None,
            )
        except Exception as e:  # HTTPException from _process_pdf has .detail
            err = str(getattr(e, "detail", e))
            logger.exception("Job %s: extraction failed for %s", job_id, name)
            results.append({"source_file": name, "document_type": None, "error": err})
            await asyncio.to_thread(set_document_result, conn, doc["id"], DOCUMENT_FAILED_STATUS, None, err)

    callback = await api._send_results_callback(job_id, results)
    logger.info("Job %s: callback -> %s", job_id, callback)

    if not any("error" not in r for r in results):
        await asyncio.to_thread(set_job_status, conn, job_id, FAILED_STATUS)


def _wait_for_notify(listen_conn, timeout: float) -> None:
    """Block until a NOTIFY arrives or timeout; drain pending notifications."""
    if select.select([listen_conn], [], [], timeout)[0]:
        listen_conn.poll()
        listen_conn.notifies.clear()


async def main() -> None:
    # Load the template schemas (api.py does this in its FastAPI lifespan).
    for shipment_type, path in TEMPLATE_PATH.items():
        api._schemas[shipment_type] = load_template_schema(path)

    work_conn = _connect()
    listen_conn = _connect()
    listen_conn.autocommit = True
    with listen_conn.cursor() as cur:
        cur.execute(f"LISTEN {CHANNEL};")
    logger.info("Worker started; listening on '%s', polling every %ss", CHANNEL, POLL_SECONDS)

    while True:
        job = await asyncio.to_thread(claim_next_job, work_conn)
        if job:
            try:
                await process_job(work_conn, job)
            except Exception:
                logger.exception("Job %s crashed", job["id"])
                work_conn.rollback()
                await asyncio.to_thread(set_job_status, work_conn, job["id"], FAILED_STATUS)
            continue  # check for more work immediately
        await asyncio.to_thread(_wait_for_notify, listen_conn, POLL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
