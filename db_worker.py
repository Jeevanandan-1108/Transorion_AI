from __future__ import annotations
import asyncio
import json
import logging
import os
import select
import shutil
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

import api
from invoice_extractor.schema.schema import load_template_schema
from invoice_extractor.schema.templates_config import TEMPLATE_PATH, TYPE_ALIASES
from invoice_extractor.extraction.reextraction import MAX_REEXTRACTION_ATTEMPTS

logger = logging.getLogger("db_worker")
logging.basicConfig(level=logging.INFO)

CHANNEL = "job_queued"
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "10"))
QUEUED_STATUS = os.environ.get("QUEUED_STATUS", "Queued")
PROCESSING_STATUS = os.environ.get("PROCESSING_STATUS", "Processing")
FORCED_TYPE = os.environ.get("JOB_SHIPMENT_TYPE") or None
TEMPLATE_TABLE = os.environ.get("TEMPLATE_TABLE", "admin_app_outputtemplate")
TEMPLATE_ID_COLUMN = os.environ.get("TEMPLATE_ID_COLUMN", "id")
TEMPLATE_VALUE_COLUMN = os.environ.get("TEMPLATE_VALUE_COLUMN", "name")
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", "."))


def _connect():
    return psycopg2.connect(
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "5432")),
    )


def claim_next_job(conn) -> dict | None:
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
            RETURNING id, number, template_id
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


def lookup_template(conn, template_id: int | None) -> str | None:
    if template_id is None:
        return None
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {TEMPLATE_VALUE_COLUMN} FROM {TEMPLATE_TABLE} "
            f"WHERE {TEMPLATE_ID_COLUMN} = %s AND NOT is_delete",
            (template_id,),
        )
        row = cur.fetchone()
    conn.commit()
    value = (row[0] or "").strip() if row else ""
    known = [*TEMPLATE_PATH, *TYPE_ALIASES]
    if value in known:
        return value
    return next((t for t in known if t.lower() == value.lower()), None)


def resolve_path(file_value: str) -> Path:
    p = Path(file_value)
    return p if p.is_absolute() else MEDIA_ROOT / p


def save_job_result(job_id: int, name: str, pdf_path: Path, extraction: dict, used: set[str]) -> None:
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
    started_at = datetime.now(timezone.utc)
    docs = await asyncio.to_thread(fetch_documents, conn, job_id)
    shipment_type = FORCED_TYPE or await asyncio.to_thread(lookup_template, conn, job["template_id"])
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
            continue
        try:
            extraction = await api._process_pdf(
                str(path), name, api.DEFAULT_MODEL, shipment_type,
                force_ocr=None, max_retries=MAX_REEXTRACTION_ATTEMPTS,
            )
            await asyncio.to_thread(save_job_result, job_id, name, path, extraction, used_names)
            document_type = api._document_type(extraction.get("extraction_method"))
            results.append({**extraction, "document_type": document_type})
        except Exception as e:
            err = str(getattr(e, "detail", e))
            logger.exception("Job %s: extraction failed for %s", job_id, name)
            results.append({"source_file": name, "document_type": None, "error": err})

    callback = await api._send_results_callback(
        job_id, results, started_at=started_at, ended_at=datetime.now(timezone.utc),
    )
    logger.info("Job %s: callback -> %s", job_id, callback)


def _wait_for_notify(listen_conn, timeout: float) -> None:
    if select.select([listen_conn], [], [], timeout)[0]:
        listen_conn.poll()
        listen_conn.notifies.clear()


async def main() -> None:
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
            continue
        await asyncio.to_thread(_wait_for_notify, listen_conn, POLL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
