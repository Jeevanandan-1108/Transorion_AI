from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from invoice_extractor import invoice_llm_check as chk


def _banner(i, n, name="Invoice 5.pdf"):
    return f"{'=' * 35} PAGE {i} OF {n} ({name}) {'=' * 35}\n"


def test_page_heads_cover_every_page_and_drop_the_file_name():
    text = (_banner(1, 2) + "PACKING LIST\nInvoice No: 123\n"
            + _banner(2, 2) + "COMMERCIAL INVOICE\nTotal 50.00\n")
    heads = chk.build_page_heads(text)
    assert "--- Page 1 of 2 ---" in heads and "--- Page 2 of 2 ---" in heads
    assert "PACKING LIST" in heads and "COMMERCIAL INVOICE" in heads
    assert "Invoice 5.pdf" not in heads


def test_a_long_pdf_keeps_every_page_with_less_text_per_page():
    n = 100
    text = "".join(_banner(i, n) + ("x" * 5000) + "\n" for i in range(1, n + 1))
    parts = chk.build_page_heads(text).split("--- Page ")[1:]
    assert len(parts) == n
    assert max(len(p) for p in parts) < chk._MIN_PAGE_CHARS + 40


def test_text_without_page_banners_is_treated_as_one_page():
    assert chk.build_page_heads("TAX INVOICE\nTotal 10").startswith("PDF with 1 page(s).")


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
        "contains_invoice": False, "readable": True, "invoice_pages": [],
        "document_types": ["Statement of Work"], "reason": "describes a project",
    }))
    res = _check()
    assert res.contains_invoice is False and res.readable is True and res.error is None
    assert res.document_types == ["Statement of Work"] and res.reason == "describes a project"
    assert res.token_usage["total_tokens"] == 1050 and res.token_usage["cost_usd"] > 0


def test_invoice_pages_come_back_as_numbers(monkeypatch):
    _answer(monkeypatch, json.dumps({"contains_invoice": True, "invoice_pages": ["2", 3, "x"]}))
    res = _check()
    assert res.contains_invoice is True and res.invoice_pages == [2, 3]


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
    asyncio.run(chk.check_contains_invoice(_banner(1, 2) + "PACKING LIST\n" + _banner(2, 2) + "TAX INVOICE\n",
                                           model="gpt-5-nano"))
    request = _FakeClient.last_request
    assert request["model"] == "gpt-5-nano" and request["reasoning_effort"] == "minimal"
    assert "--- Page 2 of 2 ---\nTAX INVOICE" in request["messages"][1]["content"]
