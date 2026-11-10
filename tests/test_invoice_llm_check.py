from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from invoice_extractor import invoice_llm_check as chk
from invoice_extractor.invoice_llm_check import InvoiceCheck


def _banner(i, n, name="Invoice 5.pdf"):
    return f"{'=' * 35} PAGE {i} OF {n} ({name}) {'=' * 35}\n"


def _pdf(*pages):
    n = len(pages)
    return "".join("\n" + _banner(i, n) + page + "\n" for i, page in enumerate(pages, 1))


_INVOICE = "COMMERCIAL INVOICE\nInvoice No: 123\nWidget  10  5.00  50.00\nTotal 50.00"
_PACKING = "PACKING LIST\nCommercial Invoice No: 123\nWidget  10 PCS  Net Wt 4.5 KG  Gross Wt 5.0 KG"
_CONTINUED = "Page 2 of 2\nGadget  4  2.50  10.00\nTotal 60.00"


def test_page_heads_cover_every_page_and_drop_the_file_name():
    heads = chk.build_page_heads(_pdf(_PACKING, _INVOICE))
    assert "--- Page 1 of 2 ---" in heads and "--- Page 2 of 2 ---" in heads
    assert "PACKING LIST" in heads and "COMMERCIAL INVOICE" in heads
    assert "Invoice 5.pdf" not in heads


def test_a_long_pdf_keeps_every_page_with_a_readable_slice_of_each():
    n = 100
    parts = chk.build_page_heads(_pdf(*["x" * 5000] * n)).split("--- Page ")[1:]
    assert len(parts) == n
    assert max(len(p) for p in parts) < chk._MIN_PAGE_CHARS + 40
    assert min(len(p) for p in parts) > chk._MIN_PAGE_CHARS


def test_a_huge_pdf_stays_under_the_hard_cap():
    assert len(chk.build_page_heads(_pdf(*["x" * 5000] * 400))) < chk._HARD_CAP_CHARS + 400 * 40


def test_text_without_page_banners_is_treated_as_one_page():
    assert chk.build_page_heads("TAX INVOICE\nTotal 10").startswith("PDF with 1 page(s).")


def test_ocr_page_markers_split_pages_too():
    text = "--- Page 1 (OCR) ---\nPACKING LIST\n\n--- Page 2 (OCR) ---\nTAX INVOICE"
    assert [p.strip() for p in chk.split_pages(text)] == ["PACKING LIST", "TAX INVOICE"]


def test_keep_pages_returns_only_those_pages_with_their_banners():
    kept = chk.keep_pages(_pdf(_PACKING, _INVOICE, _CONTINUED), [2, 3])
    assert "PACKING LIST" not in kept and "COMMERCIAL INVOICE" in kept and "Gadget" in kept
    assert "PAGE 2 OF 3" in kept and "PAGE 3 OF 3" in kept and "PAGE 1 OF 3" not in kept


@pytest.mark.parametrize("page, expected", [
    ("TAX INVOICE\nOriginal for recipient", True),
    ("COMMERCIAL INVOICE CUM PACKING LIST", True),
    ("  │ INVOICE │  Date of issue  Page 2/4", True),
    ("TAX INVOICE - ORIGINAL FOR RECIPIENT", True),
    ("PACKING LIST\nCommercial Invoice No: 123", False),
    ("PACKING LIST\nInvoice No. & Date: 123", False),
    ("PACKING LIST\nInvoice To: Foo Corp", False),
    ("E-WAY BILL\nDocument: Tax Invoice - 203620644", False),
    ("AIR WAYBILL\nshipper", False),
    ("PACKING LIST\nInvoice Description of Goods/ Services", False),
    ("PACKING LIST\n" + "x" * 580 + "\nInvoice  Date: 17.08.2026", False),
    ("Ship from  │  PT Schneider Electric  │  Packing  List  and  Invoice", True),
    ("INVOICE & PACKING LIST", True),
])
def test_invoice_heading(page, expected):
    assert chk.has_invoice_heading(page) is expected


@pytest.mark.parametrize("page, expected", [
    ("Item No./CUSTOMER  P/N  Description  │  P/O No.  Unit  Q'ty  U/Price(USD)  Amount(USD)", True),
    ("Purchase Item. Customer Part Origin Unit Total KEMET Part  Qty Order No.  Price  Amount/", True),
    ("CTN NO.  │ CTN#  Item No.  │  Description  Unit  │ Q'ty  N.W.(Kg)  G.W.(Kg)  MEAS.(CBM)", False),
    ("Qty\nUnit Price\nAmount", False),
])
def test_priced_table(page, expected):
    assert chk.has_priced_table(page) is expected


def _labelled(*labels, **kw):
    return InvoiceCheck(contains_invoice=True, page_types={i: t for i, t in enumerate(labels, 1) if t}, **kw)


def test_line_items_skip_a_packing_list():
    assert chk.item_pages(_labelled("packing_list", "invoice"), _pdf(_PACKING, _INVOICE)) == [2]


def test_line_items_skip_a_trailing_packing_list_and_certificate():
    text = _pdf(_INVOICE, _CONTINUED, _PACKING, "CERTIFICATE OF ORIGIN")
    assert chk.item_pages(_labelled("invoice", "invoice", "packing_list", "certificate"), text) == [1, 2]


def test_an_unlabelled_page_at_the_end_stays():
    text = _pdf(_INVOICE, _PACKING, "something")
    assert chk.item_pages(_labelled("invoice", "packing_list", None), text) is None


def test_a_packing_list_between_two_invoices_stays():
    text = _pdf("TAX INVOICE", _PACKING, _INVOICE, _PACKING)
    assert chk.item_pages(_labelled("invoice", "packing_list", "invoice", "packing_list"), text) == [1, 2, 3]


def test_an_unknown_label_stays():
    assert chk.item_pages(_labelled("invoice", "shipping_bill"), _pdf(_INVOICE, "x")) is None


def test_a_page_with_an_invoice_heading_stays_whatever_its_label():
    assert chk.item_pages(_labelled("other", "invoice"), _pdf(_INVOICE, _CONTINUED)) is None


def test_a_page_with_a_priced_item_table_stays_whatever_its_label():
    summary = "Shipment Summary\nPart  Qty  Price  Amount\nC0402  10000  .002000  20.00 US"
    assert chk.item_pages(_labelled("packing_list", "invoice"), _pdf(summary, _CONTINUED)) is None


@pytest.mark.parametrize("check", [
    None,
    InvoiceCheck(contains_invoice=True, error="APITimeoutError"),
    InvoiceCheck(contains_invoice=True, readable=False, page_types={1: "packing_list", 2: "invoice"}),
    _labelled("packing_list", "other"),
    _labelled("invoice", "invoice"),
])
def test_every_page_is_read_when_there_is_nothing_safe_to_skip(check):
    assert chk.item_pages(check, _pdf(_PACKING, _INVOICE)) is None


def test_a_one_page_pdf_is_never_cut():
    assert chk.item_pages(_labelled("packing_list"), _INVOICE) is None


def test_kind_labels():
    assert chk.invoice_kind_label(["tax", "commercial"]) == "tax invoice, commercial invoice"
    assert chk.invoice_kind_label([]) is None


@pytest.mark.parametrize("invoice_page, kinds", [
    ("TAX INVOICE\nSupply meant for export under LUT", ["tax"]),
    ("COMMERCIAL INVOICE\nInvoice No: 1", ["commercial"]),
    ("PROFORMA INVOICE\nSamples", ["proforma"]),
    ("Pre-Invoice # 130307523", ["proforma"]),
    ("Invoice For Customs Purposes only\nDispatch Note", ["no_commercial_value"]),
    ("EXPORT INVOICE\nNO COMMERCIAL VALUE", ["commercial", "no_commercial_value"]),
    ("INVOICE\nGST Invoice Date: 12 Aug 2026", ["commercial"]),
])
def test_invoice_kind_comes_from_the_invoice_titles(invoice_page, kinds):
    check = _labelled("packing_list", "invoice")
    assert chk.detect_invoice_kinds(check, _pdf("PROFORMA PACKING LIST\nfree of charge", invoice_page)) == kinds


def test_invoice_kind_reads_every_page_when_no_page_is_labelled():
    assert chk.detect_invoice_kinds(None, _pdf("PACKING LIST", "TAX INVOICE")) == ["tax"]


class _FakeClient:
    last_request: dict = {}

    def __init__(self, content=None, error=None):
        self._content, self._error = content, error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **request):
        _FakeClient.last_request = request
        if self._error:
            raise self._error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))],
            usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=50, prompt_tokens_details=None),
        )


def _answer(monkeypatch, content=None, error=None):
    monkeypatch.setattr(chk, "AsyncOpenAI", lambda **kw: _FakeClient(content=content, error=error))


def _check(text="anything"):
    return asyncio.run(chk.check_contains_invoice(text, model="gpt-5-nano"))


def test_a_no_comes_back_with_the_document_types_reason_and_cost(monkeypatch):
    _answer(monkeypatch, json.dumps({
        "contains_invoice": False, "readable": True, "page_types": {"1": "other"},
        "document_types": ["Statement of Work"], "reason": "describes a project",
    }))
    res = _check()
    assert res.contains_invoice is False and res.readable is True and res.error is None
    assert res.document_types == ["Statement of Work"] and res.reason == "describes a project"
    assert res.page_types == {1: "other"} and res.invoice_pages == []
    assert res.token_usage["total_tokens"] == 1050 and res.token_usage["cost_usd"] > 0


def test_page_labels_come_back_numbered_and_normalised(monkeypatch):
    _answer(monkeypatch, json.dumps({
        "contains_invoice": True,
        "page_types": {"2": "Invoice", "1": "Packing List", "x": "invoice", "9": "invoice", "3": ""},
        "invoice_kinds": ["Commercial", "proforma", "weird", "commercial"],
    }))
    res = _check(_pdf(_PACKING, _INVOICE, _CONTINUED))
    assert res.page_types == {1: "packing_list", 2: "invoice"} and res.invoice_pages == [2]
    assert res.as_dict()["page_types"] == {"1": "packing_list", "2": "invoice"}
    assert "invoice_kinds" not in res.as_dict()


def test_other_names_for_a_page_type_are_mapped_and_unknown_ones_kept(monkeypatch):
    _answer(monkeypatch, json.dumps({"contains_invoice": True, "page_types": {
        "1": "Delivery Note", "2": "commercial_invoice", "3": "shipping_document"}}))
    assert _check(_pdf(_PACKING, _INVOICE, "x")).page_types == {1: "packing_list", 2: "invoice", 3: "shipping_document"}


def test_a_yes_whose_labels_put_every_page_down_as_another_document_is_a_no(monkeypatch):
    _answer(monkeypatch, json.dumps({"contains_invoice": True, "page_types": {"1": "packing_list"}}))
    assert _check(_PACKING).contains_invoice is False


def test_a_yes_with_an_unlabelled_or_unknown_page_stays_a_yes(monkeypatch):
    _answer(monkeypatch, json.dumps({"contains_invoice": True, "page_types": {"1": "packing_list"}}))
    assert _check(_pdf(_PACKING, _INVOICE)).contains_invoice is True
    _answer(monkeypatch, json.dumps({"contains_invoice": True, "page_types": {"1": "shipping_document"}}))
    assert _check(_PACKING).contains_invoice is True


def test_page_labels_given_as_a_list_are_read_too(monkeypatch):
    _answer(monkeypatch, json.dumps({"contains_invoice": True, "page_types": ["packing_list", "invoice"]}))
    assert _check(_pdf(_PACKING, _INVOICE)).page_types == {1: "packing_list", 2: "invoice"}


def test_a_no_that_labels_an_invoice_page_is_let_through(monkeypatch):
    _answer(monkeypatch, json.dumps({"contains_invoice": False, "page_types": {"1": "invoice"}}))
    assert _check().contains_invoice is True


def test_garbled_text_is_let_through(monkeypatch):
    _answer(monkeypatch, json.dumps({"contains_invoice": False, "readable": False, "reason": "garbled"}))
    res = _check()
    assert res.contains_invoice is True and res.readable is False and res.error is None


@pytest.mark.parametrize("content", ["not json at all", json.dumps({"contains_invoice": "maybe"}), ""])
def test_an_unusable_answer_lets_the_pdf_through(monkeypatch, content):
    _answer(monkeypatch, content)
    res = _check()
    assert res.contains_invoice is True and res.error


def test_an_api_failure_lets_the_pdf_through(monkeypatch):
    _answer(monkeypatch, error=RuntimeError("connection reset"))
    res = _check()
    assert res.contains_invoice is True and "connection reset" in res.error


def test_the_request_carries_every_page_and_minimal_reasoning(monkeypatch):
    _answer(monkeypatch, json.dumps({"contains_invoice": True}))
    _check(_pdf(_PACKING, _INVOICE))
    request = _FakeClient.last_request
    assert request["model"] == "gpt-5-nano" and request["reasoning_effort"] == "minimal"
    assert "--- Page 2 of 2 ---\nCOMMERCIAL INVOICE" in request["messages"][1]["content"]


def test_pdf_page_numbers_follow_the_banners():
    text = f"{'=' * 35} PAGE 1 OF 3 (a.pdf) {'=' * 35}\nWAYBILL\n\n{'=' * 35} PAGE 3 OF 3 (a.pdf) {'=' * 35}\nINVOICE"
    assert chk.pdf_page_numbers(text) == [1, 3]
    assert chk.pdf_page_numbers("--- Page 2 (OCR) ---\nINVOICE") == [2]
    assert chk.pdf_page_numbers("TAX INVOICE") == [1]
