from __future__ import annotations
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from invoice_extractor.extraction.llm_extract import (
    _calculate_cost,
    _extract_json,
    _extract_usage,
    _is_reasoning_model,
)

logger = logging.getLogger("invoice_extractor")

DEFAULT_CHECK_MODEL = "gpt-5-nano"
_TOTAL_CHARS = 24000
_MIN_PAGE_CHARS = 300
_MAX_PAGE_CHARS = 4000
_TIMEOUT_SECONDS = 45.0

_PAGE_BANNER_RE = re.compile(
    r"^=+\s*page\s+\d+\s+of\s+\d+\s*\(.*?\)\s*=+\s*$", re.IGNORECASE | re.MULTILINE,
)
_SPACES_RE = re.compile(r"[ \t]{2,}")
_BLANK_LINES_RE = re.compile(r"\n\s*\n+")

_SYSTEM_PROMPT = """You check whether an uploaded PDF contains an invoice, for a customs invoice-extraction service.

You get the top part of every page of one PDF. A PDF often bundles several documents in any order: an invoice together with a packing list, air waybill, bill of lading, certificate of origin, shipping bill, delivery challan and so on. The invoice is often not on page 1.

Count a page as an invoice when it bills or values specific goods or services with its own invoice number or amounts. That includes:
- commercial, tax, export, import, customs and proforma invoices, and pre-invoices
- non-commercial invoices: free of charge, no commercial value, samples, returns, repairs, "invoice for customs purposes only"
- combined documents such as "invoice cum packing list" or "packing list and invoice"

Do not count documents that only mention, describe or refer to invoices: a statement of work, contract, terms and conditions, proposal, report, manual, email or letter about invoices, or a packing list, waybill or certificate that only quotes an invoice number.

contains_invoice is true when at least one page is an invoice. If the text is too garbled or too sparse to judge, set readable to false.

Reply with JSON only:
{"contains_invoice": true or false, "readable": true or false, "invoice_pages": [page numbers that are invoices], "document_types": ["short name of each kind of document found"], "reason": "one short sentence"}"""


@dataclass
class InvoiceCheck:
    contains_invoice: bool
    readable: bool = True
    invoice_pages: list[int] = field(default_factory=list)
    document_types: list[str] = field(default_factory=list)
    reason: str = ""
    model: str | None = None
    token_usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "contains_invoice": self.contains_invoice,
            "readable": self.readable,
            "invoice_pages": self.invoice_pages,
            "document_types": self.document_types,
            "reason": self.reason,
            "model": self.model,
            "error": self.error,
        }


def split_pages(text: str) -> list[str]:
    banners = list(_PAGE_BANNER_RE.finditer(text or ""))
    if not banners:
        return [text or ""]
    pages = []
    for i, banner in enumerate(banners):
        end = banners[i + 1].start() if i + 1 < len(banners) else len(text)
        pages.append(text[banner.end():end])
    return pages


def build_page_heads(text: str) -> str:
    pages = split_pages(text)
    n = len(pages)
    per_page = max(_MIN_PAGE_CHARS, min(_MAX_PAGE_CHARS, _TOTAL_CHARS // max(n, 1)))
    parts = [f"PDF with {n} page(s)."]
    for i, page in enumerate(pages, 1):
        compact = _BLANK_LINES_RE.sub("\n", _SPACES_RE.sub("  ", page)).strip()
        parts.append(f"--- Page {i} of {n} ---\n{compact[:per_page]}")
    return "\n\n".join(parts)


def _as_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    out = []
    for v in value:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out


async def check_contains_invoice(text: str, model: str | None = None) -> InvoiceCheck:
    model = model or os.environ.get("INVOICE_CHECK_MODEL") or DEFAULT_CHECK_MODEL
    request_kwargs: dict[str, Any] = dict(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": build_page_heads(text)},
        ],
    )
    if _is_reasoning_model(model):
        request_kwargs["reasoning_effort"] = "minimal"
        request_kwargs["verbosity"] = "low"
    else:
        request_kwargs["response_format"] = {"type": "json_object"}

    try:
        client = AsyncOpenAI(
            api_key=os.environ.get("OPENAI_API_KEY"), timeout=_TIMEOUT_SECONDS, max_retries=2,
        )
        resp = await client.chat.completions.create(**request_kwargs)
        usage = _extract_usage(resp)
        token_usage = _calculate_cost(model, usage["input_tokens"], usage["cached_tokens"], usage["output_tokens"])
        data = _extract_json(resp.choices[0].message.content)
    except Exception as e:
        logger.warning("Invoice check failed (%s) -- treating the PDF as an invoice", e)
        return InvoiceCheck(contains_invoice=True, model=model, error=f"{type(e).__name__}: {e}")

    verdict = data.get("contains_invoice") if isinstance(data, dict) else None
    if not isinstance(verdict, bool):
        return InvoiceCheck(
            contains_invoice=True, model=model, token_usage=token_usage,
            error=f"unreadable invoice-check answer: {str(data)[:200]}",
        )
    readable = data.get("readable") is not False
    types = data.get("document_types")
    return InvoiceCheck(
        contains_invoice=verdict or not readable,
        readable=readable,
        invoice_pages=_as_int_list(data.get("invoice_pages")),
        document_types=[str(t) for t in types] if isinstance(types, list) else [],
        reason=str(data.get("reason") or ""),
        model=model,
        token_usage=token_usage,
    )
