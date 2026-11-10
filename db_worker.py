from __future__ import annotations
import asyncio
import json
import logging
import os
import select
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

import api
from invoice_extractor.concurrency import configure_cpu_pool, default_pdf_workers, shutdown_cpu_pool
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
#JOB_FILES_URL = os.environ.get("JOB_FILES_URL", "http://192.168.2.46:8765/api/v1/jobs/{job_id}/documents-fetch/") #https://api-medha.colanapps.in/api/v1/jobs/{job_id}/documents-fetch/
JOB_FILES_URL = os.environ.get("JOB_FILES_URL", "https://api-medha.colanapps.in/api/v1/jobs/{job_id}/documents-fetch/")
JOB_FILES_TIMEOUT = float(os.environ.get("JOB_FILES_TIMEOUT", "120"))
RECONNECT_MAX_SECONDS = float(os.environ.get("RECONNECT_MAX_SECONDS", "60"))

_CONN_ERRORS = (psycopg2.OperationalError, psycopg2.InterfaceError)

# Jobs processed at once x documents per job at once = most PDFs in flight in
# this process. The per-job cap stops one 100-invoice job starving the others;
# the job cap bounds memory. For more throughput run several worker processes:
# claim_next_job's SKIP LOCKED keeps them from claiming the same job.
JOB_CONCURRENCY = max(1, int(os.environ.get("WORKER_JOB_CONCURRENCY", "4")))
JOB_DOC_CONCURRENCY = max(1, int(os.environ.get("JOB_DOC_CONCURRENCY", "4")))

# One database connection shared by every running job. A lock keeps one job's
# query and rollback from interleaving with another's, and lets a dropped
# connection be replaced once.
_db_lock = threading.Lock()
_work_conn = None


def _connect():
    # TCP keepalives stop idle connections from being silently dropped by
    # firewalls/NAT or the server during long quiet periods (e.g. overnight).
    return psycopg2.connect(
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "5432")),
        keepalives=1,
        keepalives_idle=60,
        keepalives_interval=10,
    )


def _open_connections():
    work_conn = _connect()
    try:
        listen_conn = _connect()
        listen_conn.autocommit = True
        with listen_conn.cursor() as cur:
            cur.execute(f"LISTEN {CHANNEL};")
    except Exception:
        _close_quietly(work_conn)
        raise
    return work_conn, listen_conn


def _close_quietly(*conns) -> None:
    for conn in conns:
        try:
            conn.close()
        except Exception:
            pass


async def _connect_with_retry():
    delay = 1.0
    while True:
        try:
            return await asyncio.to_thread(_open_connections)
        except _CONN_ERRORS:
            logger.exception("Could not connect to the database; retrying in %.0fs", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_SECONDS)


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


def _job_files_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if os.environ.get("JOB_FILES_API_KEY"):
        headers["X-API-KEY"] = os.environ["JOB_FILES_API_KEY"]
    if os.environ.get("JOB_FILES_BEARER_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["JOB_FILES_BEARER_TOKEN"]
    return headers


async def fetch_job_files(client: httpx.AsyncClient, job_id: int) -> tuple[str, list[dict]]:
    url = JOB_FILES_URL.format(job_id=job_id)
    resp = await client.get(url)
    resp.raise_for_status()
    body = resp.json()
    if isinstance(body, dict) and str(body.get("status", "success")).lower() != "success":
        raise RuntimeError(body.get("message") or f"documents-fetch returned status {body.get('status')!r}")
    docs = body.get("data", []) if isinstance(body, dict) else body
    return url, [
        {"name": d.get("originalName"), "url": d["url"]}
        for d in docs or [] if isinstance(d, dict) and d.get("url")
    ]


async def download_file(client: httpx.AsyncClient, base_url: str, url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with client.stream("GET", httpx.URL(base_url).join(url)) as resp:
        resp.raise_for_status()
        with dest.open("wb") as fh:
            async for chunk in resp.aiter_bytes():
                fh.write(chunk)
    return dest


def _unique_name(name: str, used: set[str]) -> str:
    stem, suffix = Path(name).stem or "unnamed", Path(name).suffix or ".pdf"
    final, n = f"{stem}{suffix}", 1
    while final.lower() in used:
        n += 1
        final = f"{stem}_{n}{suffix}"
    used.add(final.lower())
    return final


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


def _db(fn, *args):
    """Run fn(work_conn, *args) on the shared connection; if the connection
    has dropped, reconnect once and retry."""
    global _work_conn
    with _db_lock:
        try:
            return fn(_work_conn, *args)
        except _CONN_ERRORS:
            logger.exception("Database connection lost; reconnecting")
            _close_quietly(_work_conn)
            _work_conn = _connect()
            return fn(_work_conn, *args)
        except Exception:
            try:
                _work_conn.rollback()
            except Exception:
                pass
            raise


async def process_job(job: dict) -> None:
    job_id = job["id"]
    started_at = datetime.now(timezone.utc)
    shipment_type = FORCED_TYPE or await asyncio.to_thread(_db, lookup_template, job["template_id"])
    job_folder = api.RECEIVED_PDF_DIR / str(job_id)

    head_errors: list[dict] = []
    used_names: set[str] = set()
    used_files: set[str] = set()
    async with httpx.AsyncClient(
        headers=_job_files_headers(), timeout=JOB_FILES_TIMEOUT, follow_redirects=True,
    ) as client:
        try:
            files_url, files = await fetch_job_files(client, job_id)
        except Exception as e:
            logger.exception("Job %s: could not get the file list from %s", job_id, JOB_FILES_URL)
            files_url, files = JOB_FILES_URL, []
            head_errors.append({"source_file": None, "document_type": None, "error": f"Could not get job files: {e}"})
        logger.info("Job %s (%s): %d file(s), shipment type: %s",
                    job_id, job["number"], len(files), shipment_type or "auto-detect")

        # Every file of the job is downloaded and extracted at once (up to
        # JOB_DOC_CONCURRENCY); results keep the order of the file list.
        doc_slots = asyncio.Semaphore(JOB_DOC_CONCURRENCY)

        async def process_file(f: dict, name: str, dest: Path) -> dict:
            async with doc_slots:
                try:
                    path = await download_file(client, files_url, f["url"], dest)
                except Exception as e:
                    logger.exception("Job %s: download failed for %s", job_id, f["url"])
                    return {"source_file": name, "document_type": None, "error": f"Download failed: {e}"}
                return await _extract_one(job_id, name, path, shipment_type, used_names)

        planned = []
        for f in files:
            name = Path(str(f.get("name") or httpx.URL(f["url"]).path)).name or "unnamed.pdf"
            planned.append((f, name, job_folder / _unique_name(name, used_files)))
        results = head_errors + list(await asyncio.gather(*(process_file(*item) for item in planned)))

    callback = await api._send_results_callback(
        job_id, results, started_at=started_at, ended_at=datetime.now(timezone.utc),
    )
    logger.info("Job %s: callback -> %s", job_id, callback)


async def _extract_one(job_id: int, name: str, path: Path, shipment_type: str | None, used_names: set[str]) -> dict:
    try:
        extraction = await api._process_pdf(
            str(path), name, api.DEFAULT_MODEL, shipment_type,
            force_ocr=None, max_retries=MAX_REEXTRACTION_ATTEMPTS,
        )
        await asyncio.to_thread(save_job_result, job_id, name, path, extraction, used_names)
        return {**extraction, "document_type": api._document_type(extraction.get("extraction_method"))}
    except Exception as e:
        logger.exception("Job %s: extraction failed for %s", job_id, name)
        return {"source_file": name, "document_type": None, "error": str(getattr(e, "detail", e))}


def _wait_for_notify(listen_conn, timeout: float) -> None:
    if select.select([listen_conn], [], [], timeout)[0]:
        listen_conn.poll()
        listen_conn.notifies.clear()


async def _run_job(job: dict, job_slots: asyncio.Semaphore) -> None:
    try:
        await process_job(job)
    except Exception:
        logger.exception("Job %s crashed", job["id"])
    finally:
        job_slots.release()


async def _claim(job_slots: asyncio.Semaphore) -> dict | None:
    """Take a slot, then claim a job. Returns the job (its slot is released when
    it finishes) or None (slot released here)."""
    await job_slots.acquire()
    try:
        job = await asyncio.to_thread(_db, claim_next_job)
    except BaseException:
        job_slots.release()
        raise
    if job is None:
        job_slots.release()
    return job


async def main() -> None:
    global _work_conn
    for shipment_type, path in TEMPLATE_PATH.items():
        api._schemas[shipment_type] = load_template_schema(path)

    pdf_workers = int(os.environ.get("PDF_PROCESS_WORKERS", default_pdf_workers()))
    configure_cpu_pool(pdf_workers)

    _work_conn, listen_conn = await _connect_with_retry()
    logger.info(
        "Worker started; listening on '%s', polling every %ss; %d job(s) x %d doc(s) in flight, "
        "%d PDF/OCR process(es)", CHANNEL, POLL_SECONDS, JOB_CONCURRENCY, JOB_DOC_CONCURRENCY, pdf_workers,
    )

    # A slot is taken BEFORE claiming, so this worker never claims a job it
    # can't start -- the rest stay Queued for other worker processes.
    job_slots = asyncio.Semaphore(JOB_CONCURRENCY)
    running: set[asyncio.Task] = set()
    try:
        while True:
            try:
                job = await _claim(job_slots)
                if job:
                    task = asyncio.create_task(_run_job(job, job_slots))
                    running.add(task)
                    task.add_done_callback(running.discard)
                    continue
                await asyncio.to_thread(_wait_for_notify, listen_conn, POLL_SECONDS)
            except _CONN_ERRORS:
                # Any NOTIFY sent while disconnected is lost, but that's harmless:
                # claim_next_job polls the table itself, so queued jobs are still picked up.
                logger.exception("Database connection lost; reconnecting")
                _close_quietly(listen_conn)
                new_work_conn, listen_conn = await _connect_with_retry()
                with _db_lock:
                    _close_quietly(_work_conn)
                    _work_conn = new_work_conn
                logger.info("Reconnected; listening on '%s' again", CHANNEL)
            except Exception:
                logger.exception("Could not claim a job; retrying in %ss", POLL_SECONDS)
                await asyncio.sleep(POLL_SECONDS)
    finally:
        for task in running:
            task.cancel()
        shutdown_cpu_pool()
        _close_quietly(_work_conn, listen_conn)


if __name__ == "__main__":
    asyncio.run(main())
