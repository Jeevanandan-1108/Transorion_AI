"""Proof that documents, requests and LLM calls really run at the same time."""
from __future__ import annotations

import asyncio
import os
import time

import httpx
import pytest

import api
from invoice_extractor import concurrency as conc

_PDF = b"%PDF-1.4\n%%EOF\n"


class _Probe:
    """Counts how many fake extractions are running at once."""

    def __init__(self, hold=0.3):
        self.hold, self.running, self.peak, self.calls = hold, 0, 0, 0

    async def process(self, tmp_path, filename, *a, **k):
        self.calls += 1
        self.running += 1
        self.peak = max(self.peak, self.running)
        await asyncio.sleep(self.hold)
        self.running -= 1
        return {"source_file": filename, "shipment_type": "export", "header": {}, "sheets": {},
                "warnings": [], "validation": {"valid": True, "status": "VALID", "errors": []},
                "attempts": 1, "extraction_status": "validated", "extraction_method": "2d_layout_canvas"}


@pytest.fixture
def probe(tmp_path, monkeypatch):
    monkeypatch.setenv("EXTRACTOR_CALLBACK_URL", "")
    monkeypatch.setenv("INVOICE_LLM_CHECK", "0")
    monkeypatch.setattr(api, "RECEIVED_PDF_DIR", tmp_path / "received_pdfs")
    monkeypatch.setattr(api, "SAVE_DIR", tmp_path / "results")
    monkeypatch.setattr(api, "API_DOC_CONCURRENCY", 4)
    p = _Probe()
    monkeypatch.setattr(api, "_process_pdf", p.process)
    return p


async def _post(client, job_id, names):
    files = [("files", (n, _PDF, "application/pdf")) for n in names]
    return await client.post(f"/save-pdf?job_id={job_id}&shipment_type=export", files=files)


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test", timeout=60)


def test_all_files_of_one_request_run_at_once(probe):
    async def go():
        async with _client() as c:
            t = time.perf_counter()
            r = await _post(c, 1, [f"inv{i}.pdf" for i in range(4)])
            return r, time.perf_counter() - t

    r, took = asyncio.run(go())
    assert r.status_code == 200 and len(r.json()["results"]) == 4
    assert probe.peak == 4
    assert took < 4 * probe.hold * 0.75      # one after another would take 1.2 s


def test_files_beyond_the_cap_wait_for_a_free_slot(probe):
    async def go():
        async with _client() as c:
            return await _post(c, 1, [f"inv{i}.pdf" for i in range(6)])

    r = asyncio.run(go())
    assert len(r.json()["results"]) == 6 and all(e["extracted_json"] for e in r.json()["results"])
    assert probe.peak == 4


def test_two_requests_at_once_overlap_like_two_docs_tabs(probe):
    async def go():
        async with _client() as c:
            t = time.perf_counter()
            a, b = await asyncio.gather(_post(c, 1, ["a1.pdf", "a2.pdf"]), _post(c, 2, ["b1.pdf", "b2.pdf"]))
            return a, b, time.perf_counter() - t

    a, b, took = asyncio.run(go())
    assert a.status_code == b.status_code == 200
    assert [e["file_name"] for e in a.json()["results"]] == ["a1.pdf", "a2.pdf"]
    assert [e["file_name"] for e in b.json()["results"]] == ["b1.pdf", "b2.pdf"]
    assert a.json()["job_id"] == 1 and b.json()["job_id"] == 2
    assert probe.peak == 4 and took < 4 * probe.hold * 0.75


def test_one_failing_file_does_not_stop_the_others(probe, monkeypatch):
    async def flaky(tmp_path, filename, *a, **k):
        if filename == "bad.pdf":
            raise RuntimeError("boom")
        return await probe.process(tmp_path, filename)

    monkeypatch.setattr(api, "_process_pdf", flaky)

    async def go():
        async with _client() as c:
            return await _post(c, 1, ["ok1.pdf", "bad.pdf", "ok2.pdf"])

    entries = {e["file_name"]: e for e in asyncio.run(go()).json()["results"]}
    assert entries["bad.pdf"]["error"] == "boom" and entries["bad.pdf"]["extracted_json"] is None
    assert entries["ok1.pdf"]["extracted_json"] and entries["ok2.pdf"]["extracted_json"]


def test_same_filename_twice_in_one_request_does_not_collide(probe):
    async def go():
        async with _client() as c:
            return await _post(c, 1, ["inv.pdf", "inv.pdf"])

    saved = sorted(e["saved_path"].rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for e in asyncio.run(go()).json()["results"])
    assert saved == ["inv.pdf", "inv_2.pdf"]


# ── LLM calls ────────────────────────────────────────────────────────────

def test_llm_calls_are_capped_process_wide(monkeypatch):
    monkeypatch.setenv("LLM_MAX_CONCURRENCY", "3")
    state = {"running": 0, "peak": 0}

    async def call():
        async with conc.llm_slot():
            state["running"] += 1
            state["peak"] = max(state["peak"], state["running"])
            await asyncio.sleep(0.05)
            state["running"] -= 1

    async def go():
        await asyncio.gather(*(call() for _ in range(12)))

    asyncio.run(go())
    assert state["peak"] == 3


def test_a_failed_sibling_cancels_the_other_llm_calls():
    cancelled = []

    async def slow():
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    async def boom():
        await asyncio.sleep(0.05)
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        asyncio.run(conc.gather_all(slow(), boom()))
    assert cancelled == [True]


# ── CPU work ─────────────────────────────────────────────────────────────

def _pid_after_sleep(seconds):
    time.sleep(seconds)
    return os.getpid()


def test_pdf_work_runs_in_separate_processes_in_parallel():
    conc.configure_cpu_pool(3)
    try:
        async def go():
            await asyncio.gather(*(conc.run_cpu(_pid_after_sleep, 0.5) for _ in range(3)))   # start every worker
            t = time.perf_counter()
            pids = await asyncio.gather(*(conc.run_cpu(_pid_after_sleep, 1.0) for _ in range(3)))
            return pids, time.perf_counter() - t

        pids, took = asyncio.run(go())
    finally:
        conc.shutdown_cpu_pool()
        conc.configure_cpu_pool(None)
    assert os.getpid() not in pids and len(set(pids)) == 3
    assert took < 2.0                                           # three 1 s jobs, not 3 s


def test_without_a_pool_pdf_work_runs_in_threads():
    conc.configure_cpu_pool(0)
    try:
        assert asyncio.run(conc.run_cpu(_pid_after_sleep, 0)) == os.getpid()
    finally:
        conc.configure_cpu_pool(None)
