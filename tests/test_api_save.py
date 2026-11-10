from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

import api
from invoice_extractor.invoice_llm_check import InvoiceCheck


@pytest.fixture(autouse=True)
def _no_real_callback(monkeypatch):
    monkeypatch.setenv("EXTRACTOR_CALLBACK_URL", "")
    monkeypatch.setenv("INVOICE_LLM_CHECK", "0")
    monkeypatch.delenv("OUTPUT_SHAPE_CONFIG", raising=False)


def _result(name, valid):
    return {"source_file": name, "shipment_type": "export", "header": {}, "sheets": {"ITEM": []},
            "warnings": [], "validation": {"valid": valid}, "attempts": 1,
            "extraction_status": "validated" if valid else "validation_failed"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "RECEIVED_PDF_DIR", tmp_path / "received_pdfs")
    monkeypatch.setattr(api, "SAVE_DIR", tmp_path / "results")

    async def fake_process(tmp_path_, filename, *a, **k):
        if filename.startswith("boom"):
            raise api.HTTPException(status_code=422, detail="no text")
        return _result(filename, valid=not filename.startswith("bad"))

    monkeypatch.setattr(api, "_process_pdf", fake_process)
    return TestClient(api.app)


def _pdf(name):
    return (name, b"%PDF-1.4 x", "application/pdf")


_SAVE_PDF = "/save-pdf?job_id=1&shipment_type=export"


def test_save_pdf_saves_a_valid_result_under_success(client, tmp_path):
    r = client.post(_SAVE_PDF, files=[("files", _pdf("inv1.pdf"))])
    assert r.status_code == 200
    saved = json.loads((tmp_path / "results" / "validation_success" / "inv1" / "inv1.json").read_text(encoding="utf-8"))
    assert saved["source_file"] == "inv1.pdf" and saved == r.json()["results"][0]["extracted_json"]


def test_save_pdf_saves_a_failed_result_under_failure(client, tmp_path):
    client.post(_SAVE_PDF, files=[("files", _pdf("bad1.pdf"))])
    assert (tmp_path / "results" / "validation_failed" / "bad1" / "bad1.json").exists()
    assert not (tmp_path / "results" / "validation_success").exists()


def test_save_pdf_saves_every_extracted_file_and_skips_errors(client, tmp_path):
    r = client.post(_SAVE_PDF, files=[("files", _pdf("a.pdf")), ("files", _pdf("bad2.pdf")),
                                      ("files", _pdf("boom.pdf")), ("files", ("n.txt", b"x", "text/plain"))])
    assert r.status_code == 200 and len(r.json()["results"]) == 4
    root = tmp_path / "results"
    assert sorted(p.name for p in (root / "validation_success").iterdir()) == ["a"]
    assert (root / "validation_success" / "a" / "a.json").exists()
    assert sorted(p.name for p in (root / "validation_failed").iterdir()) == ["bad2"]
    assert (root / "validation_failed" / "bad2" / "bad2.json").exists()


def test_same_filename_twice_in_one_job_does_not_overwrite_the_result(client, tmp_path):
    client.post(_SAVE_PDF, files=[("files", _pdf("dup.pdf")), ("files", _pdf("dup.pdf"))])
    success = tmp_path / "results" / "validation_success"
    assert sorted(p.name for p in success.iterdir()) == ["dup", "dup_2"]
    assert (success / "dup" / "dup.json").exists()
    assert (success / "dup_2" / "dup_2.json").exists()


def test_a_path_in_the_upload_name_cannot_escape_the_folder(client, tmp_path):
    r = client.post(_SAVE_PDF, files=[("files", _pdf("../../evil.pdf"))])
    assert r.json()["results"][0]["file_name"] == "evil.pdf"
    assert (tmp_path / "received_pdfs" / "1" / "evil.pdf").exists()
    assert (tmp_path / "results" / "validation_success" / "evil" / "evil.json").exists()
    assert not (tmp_path / "evil.pdf").exists() and not (tmp_path / "received_pdfs" / "evil.pdf").exists()
    assert not (tmp_path / "evil.json").exists()


def test_a_disk_error_does_not_break_the_response(client, tmp_path):
    (tmp_path / "results").write_text("a FILE where the folder should be")
    r = client.post(_SAVE_PDF, files=[("files", _pdf("inv2.pdf"))])
    entry = r.json()["results"][0]
    assert r.status_code == 200 and entry["error"] is None
    assert entry["extracted_json"]["source_file"] == "inv2.pdf"


def test_save_pdf_resolves_a_type_alias_to_its_canonical_name(tmp_path, monkeypatch):
    """shipment_type=Focus export Template must behave EXACTLY like
    shipment_type=export -- the alias is resolved inside _process_pdf
    itself (api.TYPE_ALIASES), before schema lookup or extraction ever
    sees it. Deliberately does NOT mock _process_pdf itself (unlike the
    `client`/`pdf_client` fixtures above) so this actually exercises the
    real normalization line, on the one endpoint (/save-pdf) not currently
    commented out in api.py."""
    monkeypatch.setattr(api, "RECEIVED_PDF_DIR", tmp_path / "received_pdfs")
    monkeypatch.setattr(api, "SAVE_DIR", tmp_path / "results")
    monkeypatch.setitem(
        api._schemas, "export",
        {"ITEM": {"fields": [], "types": {}, "aliases": {}, "link_field": None}},
    )

    def fake_extract_text(path, force_ocr=None):
        text = ("COMMERCIAL INVOICE  Invoice No: INV-001  Invoice Date: 01-08-2026  Seller: ACME Ltd  "
                "Buyer: Foo Corp  Description  Qty  Unit Price  Amount  Widget  10  5.00  50.00  Total  50.00")
        return text, "", "2d_layout_canvas", 0.0, None

    async def fake_orchestrated(*a, **k):
        return {
            "header": {}, "sheets": {}, "warnings": [],
            "validation": {"valid": True, "errors": []},
            "attempts": 1, "extraction_status": "validated", "token_usage": {},
            "models_used": ["gpt-5-nano"], "complexity": "simple",
        }

    monkeypatch.setattr(api, "extract_text", fake_extract_text)
    monkeypatch.setattr(api, "extract_and_validate_orchestrated", fake_orchestrated)

    c = TestClient(api.app)
    r = c.post("/save-pdf?job_id=1&shipment_type=Focus export Template", files=[("files", _pdf("inv.pdf"))])
    assert r.status_code == 200
    assert r.json()["results"][0]["extracted_json"]["shipment_type"] == "export"


# ── /save-pdf ────────────────────────────────────────────────────────────

@pytest.fixture
def pdf_client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "RECEIVED_PDF_DIR", tmp_path / "received_pdfs")
    monkeypatch.setattr(api, "SAVE_DIR", tmp_path / "results")
    seen = {}

    async def fake_process(path, filename, model, requested_type, **k):
        seen[filename] = (path, requested_type)
        assert open(path, "rb").read().startswith(b"%PDF")
        if filename.startswith("boom"):
            raise api.HTTPException(status_code=422, detail="no text")
        method = "ocrmypdf_docker" if filename.startswith("scan") else "2d_layout_canvas"
        return {**_result(filename, True), "extraction_method": method}

    monkeypatch.setattr(api, "_process_pdf", fake_process)
    c = TestClient(api.app)
    c.seen = seen
    return c


def test_save_pdf_saves_each_file_under_the_job_folder_and_reports_type_and_status(pdf_client, tmp_path):
    r = pdf_client.post("/save-pdf?job_id=123&shipment_type=import",
                        files=[("files", _pdf("a.pdf")), ("files", _pdf("scan1.pdf"))])
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] == 123 and body["shipment_type"] == "import"
    a, s = body["results"]
    assert (a["job_id"], a["file_name"], a["document_type"], a["error"]) == (123, "a.pdf", "selectable", None)
    assert (s["document_type"], s["error"]) == ("scanned", None)
    assert a["extracted_json"]["source_file"] == "a.pdf"
    assert a["extracted_json"]["extraction_status"] == s["extracted_json"]["extraction_status"] == "validated"
    saved = sorted(p.name for p in (tmp_path / "received_pdfs" / "123").iterdir())
    assert saved == ["a.pdf", "scan1.pdf"]
    assert pdf_client.seen["a.pdf"][1] == "import"
    assert (tmp_path / "results" / "validation_success" / "a" / "a.json").exists()
    assert (tmp_path / "results" / "validation_success" / "scan1" / "scan1.json").exists()


def test_save_pdf_marks_failures_but_still_saves_the_pdf(pdf_client, tmp_path):
    r = pdf_client.post("/save-pdf?job_id=7&shipment_type=export",
                        files=[("files", _pdf("boom.pdf")), ("files", ("n.txt", b"x", "text/plain"))])
    boom, txt = r.json()["results"]
    assert boom["extracted_json"] is None and "no text" in boom["error"]
    assert boom["saved_path"] and boom["document_type"] is None
    assert txt["extracted_json"] is None and txt["error"] == "Only PDF files are accepted."
    assert txt["saved_path"] is None
    assert len(list((tmp_path / "received_pdfs" / "7").iterdir())) == 1


def test_save_pdf_keeps_the_original_name_and_a_same_name_twin_gets_a_suffix(pdf_client, tmp_path):
    r = pdf_client.post("/save-pdf?job_id=9&shipment_type=export", files=[("files", _pdf("d.pdf")), ("files", _pdf("d.pdf"))])
    assert sorted(p.name for p in (tmp_path / "received_pdfs" / "9").iterdir()) == ["d.pdf", "d_2.pdf"]
    first, second = r.json()["results"]
    assert first["saved_path"].endswith("d.pdf") and second["saved_path"].endswith("d_2.pdf")
    assert first["file_name"] == second["file_name"] == "d.pdf"


def test_save_pdf_requires_a_valid_shipment_type_and_job_id(pdf_client):
    assert pdf_client.post("/save-pdf?job_id=1&shipment_type=bogus", files=[("files", _pdf("a.pdf"))]).status_code == 422
    assert pdf_client.post("/save-pdf?shipment_type=import", files=[("files", _pdf("a.pdf"))]).status_code == 422


def test_save_pdf_saves_every_file_before_extracting_the_first(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "RECEIVED_PDF_DIR", tmp_path / "received_pdfs")
    monkeypatch.setattr(api, "SAVE_DIR", tmp_path / "results")
    on_disk_at_extraction = []

    async def fake_process(path, filename, *a, **k):
        on_disk_at_extraction.append(sorted(p.name for p in (tmp_path / "received_pdfs" / "5").iterdir()))
        return {**_result(filename, True), "extraction_method": "2d_layout_canvas"}

    monkeypatch.setattr(api, "_process_pdf", fake_process)
    TestClient(api.app).post("/save-pdf?job_id=5&shipment_type=export",
                             files=[("files", _pdf("a.pdf")), ("files", _pdf("b.pdf")), ("files", _pdf("c.pdf"))])
    assert on_disk_at_extraction[0] == ["a.pdf", "b.pdf", "c.pdf"]
    assert len(on_disk_at_extraction) == 3


def _mock_callback_client(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_save_pdf_posts_job_id_and_results_to_the_callback_api(pdf_client, monkeypatch):
    got = {}

    def handler(request):
        got["url"], got["headers"], got["body"] = str(request.url), request.headers, json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    _mock_callback_client(monkeypatch, handler)
    monkeypatch.setenv("EXTRACTOR_CALLBACK_URL", "http://cb.test/process_invoices/")
    monkeypatch.setenv("EXTRACTOR_CALLBACK_API_KEY", "k123")
    monkeypatch.setenv("EXTRACTOR_CALLBACK_BEARER_TOKEN", "tok")
    r = pdf_client.post("/save-pdf?job_id=5&shipment_type=export",
                        files=[("files", _pdf("a.pdf")), ("files", _pdf("boom.pdf"))])
    assert got["url"] == "http://cb.test/process_invoices/"
    assert got["headers"]["x-api-key"] == "k123" and got["headers"]["authorization"] == "Bearer tok"
    assert got["body"]["job_id"] == 5
    ok, failed = got["body"]["results"]
    assert ok["source_file"] == "a.pdf" and "sheets" in ok and "header" in ok
    assert ok["document_type"] == "selectable"
    assert failed == {"source_file": "boom.pdf", "document_type": None, "error": "no text"}
    assert r.json()["callback"] == {"sent": True, "status_code": 200}


def test_callback_carries_selectable_or_scanned_per_file(pdf_client, monkeypatch):
    got = {}

    def handler(request):
        got["body"] = json.loads(request.content)
        return httpx.Response(200)

    _mock_callback_client(monkeypatch, handler)
    monkeypatch.setenv("EXTRACTOR_CALLBACK_URL", "http://cb.test/x/")
    pdf_client.post("/save-pdf?job_id=5&shipment_type=export", files=[("files", _pdf("a.pdf")), ("files", _pdf("scan1.pdf"))])
    assert [r["document_type"] for r in got["body"]["results"]] == ["selectable", "scanned"]


def test_a_failing_callback_does_not_break_the_response(pdf_client, monkeypatch):
    _mock_callback_client(monkeypatch, lambda request: httpx.Response(401, text="bad token"))
    monkeypatch.setenv("EXTRACTOR_CALLBACK_URL", "http://cb.test/x/")
    r = pdf_client.post("/save-pdf?job_id=5&shipment_type=export", files=[("files", _pdf("a.pdf"))])
    entry = r.json()["results"][0]
    assert r.status_code == 200 and entry["error"] is None and entry["extracted_json"] is not None
    assert r.json()["callback"]["sent"] is False and r.json()["callback"]["status_code"] == 401


def test_an_unreachable_callback_is_reported_not_raised(pdf_client, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("no route to host")

    _mock_callback_client(monkeypatch, handler)
    monkeypatch.setenv("EXTRACTOR_CALLBACK_URL", "http://cb.test/x/")
    r = pdf_client.post("/save-pdf?job_id=5&shipment_type=export", files=[("files", _pdf("a.pdf"))])
    assert r.status_code == 200 and "no route" in r.json()["callback"]["error"]


def test_callback_is_off_when_the_url_is_empty(pdf_client):
    r = pdf_client.post("/save-pdf?job_id=5&shipment_type=export", files=[("files", _pdf("a.pdf"))])
    assert r.json()["callback"]["sent"] is False and "disabled" in r.json()["callback"]["reason"]


# ── scan-quality rejection: never reaches detection or the LLM orchestrator ─

def test_confirmed_bad_scan_quality_is_rejected_without_detection_or_llm_calls(tmp_path, monkeypatch):
    """Exercises the real _process_pdf() (not mocked, unlike pdf_client
    above) so this actually proves the scan-quality gate short-circuits
    before detect_shipment_type() or the LLM orchestrator ever run -- no
    wasted detection work and no LLM cost on a page already known bad."""
    monkeypatch.setattr(api, "RECEIVED_PDF_DIR", tmp_path / "received_pdfs")
    monkeypatch.setattr(api, "SAVE_DIR", tmp_path / "results")

    rejected_quality = {
        "skew_deg": 14.0, "skew_ok": False, "clipped_edges": [], "clipped": False,
        "valid": False, "reasons": ["Page is tilted +14.00°, exceeding the 2.0° limit."],
        "n_lines_used": 5,
    }

    def fake_extract_text(path, force_ocr=None):
        return "", "", api.SCAN_QUALITY_REJECTED, 0.0, rejected_quality

    def boom_detect(*a, **k):
        raise AssertionError("detect_shipment_type() must not run once scan quality is rejected")

    async def boom_orchestrated(*a, **k):
        raise AssertionError("extract_and_validate_orchestrated() must not run (no LLM calls)")

    monkeypatch.setattr(api, "extract_text", fake_extract_text)
    monkeypatch.setattr(api, "detect_shipment_type", boom_detect)
    monkeypatch.setattr(api, "extract_and_validate_orchestrated", boom_orchestrated)

    c = TestClient(api.app)
    r = c.post("/save-pdf?job_id=1&shipment_type=export", files=[("files", _pdf("tilted.pdf"))])
    assert r.status_code == 200
    entry = r.json()["results"][0]
    assert entry["document_type"] is None
    assert entry["error"] is None

    extracted = entry["extracted_json"]
    assert extracted["extraction_status"] == "failed"
    assert extracted["header"] == {} and extracted["sheets"] == {}
    assert extracted["shipment_type"] is None
    assert extracted["validation"]["valid"] is False and extracted["validation"]["status"] == "REJECTED"
    assert any("tilted" in w for w in extracted["warnings"])


_INVOICE_TEXT = ("COMMERCIAL INVOICE  Invoice No: INV-001  Invoice Date: 01-08-2026  Seller: ACME Ltd  "
                 "Buyer: Foo Corp  Description  Qty  Unit Price  Amount  Widget  10  5.00  50.00  Total  50.00")


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "RECEIVED_PDF_DIR", tmp_path / "received_pdfs")
    monkeypatch.setattr(api, "SAVE_DIR", tmp_path / "results")
    monkeypatch.setitem(api._schemas, "export", {"ITEM": {"fields": [], "types": {}, "aliases": {}, "link_field": None}})
    monkeypatch.setenv("INVOICE_LLM_CHECK", "1")
    monkeypatch.setattr(api, "extract_text",
                        lambda path, force_ocr=None: (_INVOICE_TEXT, "", "2d_layout_canvas", 0.0, None))
    calls = {"extract": 0, "check": 0}
    extract_args = []

    async def fake_orchestrated(*a, **k):
        calls["extract"] += 1
        extract_args.append((a, k))
        return {"header": {}, "sheets": {}, "warnings": [],
                "validation": {"valid": True, "status": "VALID", "errors": []},
                "attempts": 1, "extraction_status": "validated",
                "token_usage": {**api._ZERO_USAGE, "total_tokens": 100, "cost_inr": 1.0},
                "models_used": ["gpt-5-nano"], "complexity": "simple"}

    monkeypatch.setattr(api, "extract_and_validate_orchestrated", fake_orchestrated)

    def run(check):
        async def fake_check(text):
            calls["check"] += 1
            return check

        monkeypatch.setattr(api, "check_contains_invoice", fake_check)
        r = TestClient(api.app).post("/save-pdf?job_id=1&shipment_type=export", files=[("files", _pdf("doc.pdf"))])
        assert r.status_code == 200
        return r.json()["results"][0]["extracted_json"], calls

    run.extract_args = extract_args
    return run


def test_a_pdf_with_no_invoice_page_is_rejected_before_extraction(pipeline):
    usage = {**api._ZERO_USAGE, "total_tokens": 900, "cost_usd": 0.0001, "cost_inr": 0.0096}
    got, calls = pipeline(InvoiceCheck(contains_invoice=False, document_types=["Statement of Work"],
                                       reason="describes an invoice-extraction project", model="gpt-5-nano",
                                       token_usage=usage))
    assert calls == {"extract": 0, "check": 1}
    assert got["is_invoice"] is False and got["extraction_status"] == "failed"
    assert got["validation"]["status"] == "REJECTED" and got["invoice_doc_type"] == "Statement of Work"
    assert any(w.startswith("not an invoice:") for w in got["warnings"])
    assert got["token_usage"]["cost_inr"] == 0.0096


def test_a_pdf_with_an_invoice_page_is_extracted_and_the_check_cost_is_added(pipeline):
    usage = {**api._ZERO_USAGE, "total_tokens": 900, "cost_inr": 0.01}
    got, calls = pipeline(InvoiceCheck(contains_invoice=True, invoice_pages=[2], model="gpt-5-nano", token_usage=usage))
    assert calls == {"extract": 1, "check": 1} and got["is_invoice"] is True
    assert got["invoice_check"]["invoice_pages"] == [2]
    assert got["token_usage"]["total_tokens"] == 1000 and got["token_usage"]["cost_inr"] == 1.01


def test_a_failed_check_never_blocks_extraction(pipeline):
    got, calls = pipeline(InvoiceCheck(contains_invoice=True, error="APITimeoutError: timed out"))
    assert calls["extract"] == 1
    assert any(w.startswith("invoice check skipped") for w in got["warnings"])


def test_garbled_text_is_extracted_with_a_warning(pipeline):
    got, calls = pipeline(InvoiceCheck(contains_invoice=True, readable=False, reason="garbled OCR"))
    assert calls["extract"] == 1
    assert any("too garbled" in w for w in got["warnings"])


def test_the_invoice_check_can_be_switched_off(pipeline, monkeypatch):
    monkeypatch.setenv("INVOICE_LLM_CHECK", "0")
    got, calls = pipeline(InvoiceCheck(contains_invoice=False))
    assert calls == {"extract": 1, "check": 0} and got["invoice_check"] is None


_BUNDLE_TEXT = (
    "\n" + "=" * 35 + " PAGE 1 OF 2 (doc.pdf) " + "=" * 35 + "\n"
    "PACKING LIST  Commercial Invoice No: INV-001  Widget  10 PCS  Net Wt 4.5 KG  Gross Wt 5.0 KG\n"
    "\n" + "=" * 35 + " PAGE 2 OF 2 (doc.pdf) " + "=" * 35 + "\n" + _INVOICE_TEXT + "\n"
)


def test_line_items_read_only_the_invoice_pages_but_the_header_reads_all(pipeline, monkeypatch):
    monkeypatch.setattr(api, "extract_text",
                        lambda path, force_ocr=None: (_BUNDLE_TEXT, "", "2d_layout_canvas", 0.0, None))
    got, calls = pipeline(InvoiceCheck(contains_invoice=True, page_types={1: "packing_list", 2: "invoice"},
                                       invoice_pages=[2]))
    (args, kwargs), = pipeline.extract_args
    assert args[0] == _BUNDLE_TEXT
    assert "COMMERCIAL INVOICE" in kwargs["items_text"] and "PACKING LIST" not in kwargs["items_text"]
    assert got["item_pages"] == [2] and got["invoice_doc_type"] == "commercial invoice"
    assert "line items read from page 2 only; skipped page(s) 1 (packing list)" in got["warnings"]


def test_every_page_is_read_when_the_check_labels_no_pages(pipeline):
    got, calls = pipeline(InvoiceCheck(contains_invoice=True))
    (args, kwargs), = pipeline.extract_args
    assert kwargs["items_text"] is None and got["item_pages"] is None
    assert got["invoice_doc_type"] == "commercial invoice"


def test_a_proforma_invoice_is_named_and_warned_about(pipeline, monkeypatch):
    text = "PROFORMA INVOICE\n" + _INVOICE_TEXT.replace("COMMERCIAL INVOICE", "Samples")
    monkeypatch.setattr(api, "extract_text", lambda path, force_ocr=None: (text, "", "2d_layout_canvas", 0.0, None))
    got, _ = pipeline(InvoiceCheck(contains_invoice=True))
    assert got["invoice_doc_type"] == "proforma invoice"
    assert any(w.startswith("invoice kind: proforma") for w in got["warnings"])


def test_the_kind_is_named_even_with_the_invoice_check_off(pipeline, monkeypatch):
    monkeypatch.setenv("INVOICE_LLM_CHECK", "0")
    got, _ = pipeline(InvoiceCheck(contains_invoice=False))
    assert got["invoice_check"] is None and got["invoice_doc_type"] == "commercial invoice"


def _partly_bad_scan(bad_pages):
    return {"skew_deg": 0.0, "skew_ok": True, "clipped_edges": ["left"], "clipped": True, "valid": False,
            "reasons": [f"page {p}: Table border line(s) touch the page edge at: left." for p in bad_pages],
            "n_lines_used": 9, "page_count": 2, "checked_pages": [1, 2], "bad_pages": bad_pages}


def _scanned_bundle(monkeypatch, bad_pages):
    monkeypatch.setattr(api, "extract_text", lambda path, force_ocr=None: (
        _BUNDLE_TEXT, "", "ocr_rapidocr", 1.0, _partly_bad_scan(bad_pages)))


def test_a_cut_off_packing_list_page_does_not_stop_the_invoice(pipeline, monkeypatch):
    _scanned_bundle(monkeypatch, [1])
    got, calls = pipeline(InvoiceCheck(contains_invoice=True, page_types={1: "packing_list", 2: "invoice"}))
    assert calls["extract"] == 1 and got["validation"]["status"] == "VALID"
    assert any(w.startswith("scan quality: only pages that are not the invoice") and "page 1: packing list" in w
               for w in got["warnings"])


def test_a_cut_off_invoice_page_rejects_the_pdf_before_extraction(pipeline, monkeypatch):
    _scanned_bundle(monkeypatch, [2])
    usage = {**api._ZERO_USAGE, "total_tokens": 900, "cost_inr": 0.01}
    got, calls = pipeline(InvoiceCheck(contains_invoice=True, page_types={1: "packing_list", 2: "invoice"},
                                       model="gpt-5-nano", token_usage=usage))
    assert calls["extract"] == 0 and got["is_invoice"] is True
    assert got["validation"]["status"] == "REJECTED" and got["extraction_status"] == "failed"
    assert got["token_usage"]["cost_inr"] == 0.01
    assert any("invoice page(s) 2 tilted or cut off" in w for w in got["warnings"])


def test_without_page_labels_any_bad_scanned_page_rejects(pipeline, monkeypatch):
    monkeypatch.setenv("INVOICE_LLM_CHECK", "0")
    _scanned_bundle(monkeypatch, [1])
    got, calls = pipeline(InvoiceCheck(contains_invoice=True))
    assert calls["extract"] == 0 and got["validation"]["status"] == "REJECTED"


_GAP_TEXT = (
    "\n" + "=" * 35 + " PAGE 1 OF 3 (doc.pdf) " + "=" * 35 + "\n"
    "PACKING LIST  Commercial Invoice No: INV-001  Widget  10 PCS  Net Wt 4.5 KG  Gross Wt 5.0 KG\n"
    "\n" + "=" * 35 + " PAGE 3 OF 3 (doc.pdf) " + "=" * 35 + "\n" + _INVOICE_TEXT + "\n"
)


def test_pages_are_named_by_pdf_page_number_when_a_blank_page_is_missing(pipeline, monkeypatch):
    monkeypatch.setattr(api, "extract_text", lambda path, force_ocr=None: (_GAP_TEXT, "", "2d_layout_canvas", 0.0, None))
    got, _ = pipeline(InvoiceCheck(contains_invoice=True, page_types={1: "packing_list", 2: "invoice"},
                                   invoice_pages=[2]))
    assert got["item_pages"] == [3]
    assert got["invoice_check"]["page_types"] == {"1": "packing_list", "3": "invoice"}
    assert got["invoice_check"]["invoice_pages"] == [3]
    assert "line items read from page 3 only; skipped page(s) 1 (packing list)" in got["warnings"]


def test_scan_quality_pages_are_matched_by_pdf_page_number(pipeline, monkeypatch):
    bad_invoice = {**_partly_bad_scan([3]), "page_count": 3}
    monkeypatch.setattr(api, "extract_text", lambda path, force_ocr=None: (_GAP_TEXT, "", "ocr_rapidocr", 1.0, bad_invoice))
    got, calls = pipeline(InvoiceCheck(contains_invoice=True, page_types={1: "packing_list", 2: "invoice"}))
    assert calls["extract"] == 0 and got["validation"]["status"] == "REJECTED"
    assert any("invoice page(s) 3 tilted or cut off" in w for w in got["warnings"])
