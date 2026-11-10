from __future__ import annotations

import asyncio
import functools
import logging
import multiprocessing
import os
import threading
import weakref
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any, Callable, TypeVar

from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential

logger = logging.getLogger("invoice_extractor")

T = TypeVar("T")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# --- LLM: one process-wide cap on in-flight OpenAI requests -----------------
# Every extraction (CLI, batch, API, db_worker) fans out into many small
# calls; without a shared cap, N concurrent documents x M chunks each would
# all hit OpenAI at once and trip 429s. Sized to the account's RPM/TPM tier.

_llm_semaphores: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)


def llm_slot() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _llm_semaphores.get(loop)
    if sem is None:
        sem = _llm_semaphores[loop] = asyncio.Semaphore(max(1, _env_int("LLM_MAX_CONCURRENCY", 24)))
    return sem


# Jittered backoff so a burst of 429s doesn't retry in lockstep. The retry
# sleeps OUTSIDE llm_slot() (the slot is taken per attempt inside the
# decorated call), so a backing-off request never holds a slot.
llm_retry = retry(
    retry=retry_if_exception_type(
        (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError)
    ),
    wait=wait_random_exponential(multiplier=1, min=2, max=60),
    stop=stop_after_attempt(max(1, _env_int("LLM_MAX_ATTEMPTS", 6))),
    reraise=True,
)


async def gather_all(*coros: Any) -> list[Any]:
    """asyncio.gather that cancels the siblings if one fails, so a failed
    extraction doesn't leave its other LLM calls running (and billing)."""
    tasks = [asyncio.ensure_future(c) for c in coros]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for t in tasks:
            t.cancel()
        raise


# --- CPU: PDF parsing / OCR in a process pool -------------------------------
# pdfplumber's layout pass is pure Python and Docling/RapidOCR are CPU-bound,
# so threads serialize on the GIL (and starve the event loop). Worker
# processes give real multi-core parallelism. 0 workers = old behavior
# (asyncio.to_thread), which is also what single-file CLI runs want.

_cpu_workers: int | None = None
_cpu_pool: ProcessPoolExecutor | None = None
_cpu_pool_lock = threading.Lock()


def default_pdf_workers() -> int:
    return max(1, min(4, (os.cpu_count() or 2) - 1))


def configure_cpu_pool(workers: int | None) -> None:
    global _cpu_workers
    _cpu_workers = workers


def _worker_count() -> int:
    return _cpu_workers if _cpu_workers is not None else _env_int("PDF_PROCESS_WORKERS", 0)


def _get_pool(workers: int) -> ProcessPoolExecutor:
    global _cpu_pool
    with _cpu_pool_lock:
        if _cpu_pool is None:
            # Stop each worker's BLAS/onnxruntime from claiming every core.
            threads = str(max(1, (os.cpu_count() or 1) // workers))
            for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
                os.environ.setdefault(var, threads)
            _cpu_pool = ProcessPoolExecutor(
                max_workers=workers, mp_context=multiprocessing.get_context("spawn"),
            )
            logger.info("Started PDF/OCR process pool with %d worker(s)", workers)
        return _cpu_pool


def _discard_pool(pool: ProcessPoolExecutor) -> None:
    global _cpu_pool
    with _cpu_pool_lock:
        if _cpu_pool is pool:
            _cpu_pool = None
    pool.shutdown(wait=False, cancel_futures=True)


def shutdown_cpu_pool() -> None:
    global _cpu_pool
    with _cpu_pool_lock:
        pool, _cpu_pool = _cpu_pool, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


async def run_cpu(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    workers = _worker_count()
    if workers <= 0:
        return await asyncio.to_thread(fn, *args, **kwargs)

    call = functools.partial(fn, *args, **kwargs)
    loop = asyncio.get_running_loop()
    for attempt in (1, 2):
        pool = _get_pool(workers)
        try:
            return await loop.run_in_executor(pool, call)
        except BrokenProcessPool:
            # A worker died (typically OOM on a huge scan). Every in-flight
            # task on this pool fails together; rebuild it and retry once.
            logger.error("PDF/OCR worker process died (attempt %d/2); rebuilding the pool", attempt)
            _discard_pool(pool)
            if attempt == 2:
                raise
    raise AssertionError("unreachable")
