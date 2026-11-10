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
_TOTAL_CHARS = 48000
_MIN_PAGE_CHARS = 1200
_MAX_PAGE_CHARS = 4000
_HARD_CAP_CHARS = 200000
_HEADING_CHARS = 600
_TIMEOUT_SECONDS = 45.0

# Digital, Docling and RapidOCR text mark their pages differently.
_PAGE_BANNER_RE = re.compile(
    r"^(?:=+\s*page\s+\d+\s+of\s+\d+\s*\(.*?\)\s*=+|-{3}\s*page\s+\d+\s*\(ocr\)\s*-{3})\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_SPACES_RE = re.compile(r"[ \t]{2,}")
_BLANK_LINES_RE = re.compile(r"\n\s*\n+")

PAGE_TYPES = ("invoice", "packing_list", "transport", "certificate", "purchase_order", "terms", "other")
NON_INVOICE_PAGE_TYPES = frozenset(PAGE_TYPES) - {"invoice"}
# Names the model sometimes uses instead of the listed ones. Anything else it
# invents stays as given, and an unknown label never removes a page.
_PAGE_TYPE_SYNONYMS = {
    "packing_list": ("packing", "packing_slip", "weight_list", "delivery", "delivery_note", "delivery_challan"),
    "transport": ("air_waybill", "airway_bill", "awb", "waybill", "courier_waybill", "bill_of_lading",
                  "e_way_bill", "eway_bill"),
    "certificate": ("certificate_of_origin", "certificate_of_analysis", "certificate_of_conformity",
                    "certificate_of_compliance", "insurance_certificate"),
    "purchase_order": ("po",),
    "terms": ("terms_and_conditions", "contract", "conditions_of_sale", "general_conditions_of_sale"),
}
_SYNONYM_PAGE_TYPE = {name: kind for kind, names in _PAGE_TYPE_SYNONYMS.items() for name in names}
INVOICE_KINDS = {
    "commercial": "commercial invoice",
    "tax": "tax invoice",
    "proforma": "proforma invoice",
    "no_commercial_value": "no-commercial-value invoice",
}

# A page whose own heading says it is an invoice is never left out, whatever
# label it got. "Invoice No:", "Invoice Date", "Commercial Invoice 123" or an
# "Invoice" table column on a packing list refers to the invoice; it is not a
# heading. A bare "Invoice" counts only when it starts a line and stands alone
# or is followed by a column gap.
_NOT_A_HEADING = r"(?![ \t]*(?:[:#.]|-?[ \t]*\d|no\b|nos?\b|number|num\b|date|dt\b|ref|value|amount|amt|to\b|details))"
_INVOICE_HEADING_RE = re.compile(
    r"\b(?:tax|commercial|proforma|pro[\s-]*forma|export|import|customs|gst|vat|sales|e)[\s-]+invoice\b"
    + _NOT_A_HEADING
    + r"|^\W*invoice(?=[ \t]*(?:$|[│|])|[ \t]{2})" + _NOT_A_HEADING
    + r"|\binvoice\s*(?:and|&|cum|/|\+)\s*packing\s*list\b|\bpacking\s*list\s*(?:and|&|cum|/|\+)\s*invoice\b",
    re.IGNORECASE | re.MULTILINE,
)
# A table header with quantity, price and amount columns marks an item table
# that carries values: an invoice page, a priced shipment summary, or a
# combined packing list and invoice. Such a page is never left out. A plain
# packing list has quantities and weights, not prices.
_QTY_COLUMN_RE = re.compile(r"\b(?:qty|q'ty|q\.ty|quantity|qnty)\b", re.IGNORECASE)
_PRICE_COLUMN_RE = re.compile(r"\b(?:u/price|unit\s*price|unit\s*rate|price|rate)\b", re.IGNORECASE)
_AMOUNT_COLUMN_RE = re.compile(r"\b(?:amount|value|total)\b", re.IGNORECASE)
# The kind of invoice comes from the titles and stamps at the top of the
# invoice's own pages. (The check model's own invoice_kinds answer listed
# nearly every kind for every PDF, so it is not used.)
_KIND_TITLES = (
    ("commercial", re.compile(r"\b(?:commercial|export|import|customs|sales)\s+invoice\b" + _NOT_A_HEADING, re.IGNORECASE)),
    ("tax", re.compile(r"\b(?:tax|gst|vat)\s+invoice\b" + _NOT_A_HEADING, re.IGNORECASE)),
    ("proforma", re.compile(r"\bpro[\s-]*forma\s+invoice\b|\bpre[\s-]*invoice\b", re.IGNORECASE)),
    ("no_commercial_value", re.compile(
        r"no\s+commercial\s+value|not\s+for\s+commercial|free\s+of\s+(?:charge|cost)"
        r"|for\s+customs?\s+purposes?\s+only|not\s+for\s+(?:re)?sale", re.IGNORECASE)),
)

_SYSTEM_PROMPT = """You check whether an uploaded PDF contains an invoice, for a customs invoice-extraction service.

You get the top part of every page of one PDF. A PDF often bundles several documents in any order: an invoice together with a packing list, air waybill, bill of lading, certificate of origin, shipping bill, delivery challan and so on. The invoice is often not on page 1.

Count a page as an invoice when it bills or values specific goods or services with its own invoice number or amounts. That includes:
- commercial, tax, export, import, customs and proforma invoices, and pre-invoices
- non-commercial invoices: free of charge, no commercial value, samples, returns, repairs, "invoice for customs purposes only"
- combined documents such as "invoice cum packing list" or "packing list and invoice"

Do not count documents that only mention, describe or refer to invoices: a statement of work, contract, terms and conditions, proposal, report, manual, email or letter about invoices, or a packing list, waybill or certificate that only quotes an invoice number.

contains_invoice is true when at least one page is an invoice. If the text is too garbled or too sparse to judge, set readable to false.

Give every page a type in page_types:
- "invoice": a page of an invoice. Every page of a multi-page invoice is "invoice": pages that continue its item table, carry its totals, or repeat its header with a page number, even without an invoice title of their own.
- "packing_list": packing list, packing slip, weight list, delivery note or delivery challan
- "transport": air waybill, bill of lading, courier waybill or e-way bill
- "certificate": certificate of origin, analysis, conformity or insurance
- "purchase_order": purchase order
- "terms": terms and conditions or contract
- "other": anything else
A page that continues another document gets that document's type.

invoice_kinds lists each kind of invoice found, using only these values: "commercial" (commercial, export, import or customs invoice for a sale), "tax" (GST tax invoice), "proforma" (proforma invoice or pre-invoice), "no_commercial_value" (free of charge, samples, returns, repairs, or for customs purposes only).

Reply with JSON only:
{"contains_invoice": true or false, "readable": true or false, "page_types": {"1": type of page 1, "2": type of page 2, ... one entry for every page}, "invoice_kinds": [kinds of invoice found], "document_types": ["short name of each kind of document found"], "reason": "one short sentence"}"""


@dataclass
class InvoiceCheck:
    contains_invoice: bool
    readable: bool = True
    invoice_pages: list[int] = field(default_factory=list)
    page_types: dict[int, str] = field(default_factory=dict)
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
            "page_types": {str(page): kind for page, kind in self.page_types.items()},
            "document_types": self.document_types,
            "reason": self.reason,
            "model": self.model,
            "error": self.error,
        }


def _page_spans(text: str) -> list[tuple[int, int, int]]:
    """(banner start, page text start, page end) for every page."""
    banners = list(_PAGE_BANNER_RE.finditer(text))
    if not banners:
        return [(0, 0, len(text))]
    ends = [b.start() for b in banners[1:]] + [len(text)]
    return [(b.start(), b.end(), end) for b, end in zip(banners, ends)]


def split_pages(text: str) -> list[str]:
    text = text or ""
    return [text[start:end] for _, start, end in _page_spans(text)]


_PAGE_NUMBER_RE = re.compile(r"page\s+(\d+)", re.IGNORECASE)


def pdf_page_numbers(text: str) -> list[int]:
    """The PDF page number of each page of the text, in order. Pages are
    numbered by position everywhere in this module; a blank page leaves no
    banner, so position and PDF page number can differ."""
    text = text or ""
    numbers = []
    for position, (banner, start, _) in enumerate(_page_spans(text), 1):
        m = _PAGE_NUMBER_RE.search(text[banner:start]) if start > banner else None
        numbers.append(int(m.group(1)) if m else position)
    return numbers


def keep_pages(text: str, pages: list[int]) -> str:
    """The text of the given pages (numbered from 1), each with its own page banner."""
    text = text or ""
    spans = _page_spans(text)
    wanted = set(pages)
    return text[:spans[0][0]] + "".join(
        text[banner:end] for number, (banner, _, end) in enumerate(spans, 1) if number in wanted
    )


def _compact(page: str) -> str:
    return _BLANK_LINES_RE.sub("\n", _SPACES_RE.sub("  ", page)).strip()


def build_page_heads(text: str) -> str:
    pages = split_pages(text)
    n = len(pages)
    per_page = min(_MAX_PAGE_CHARS, max(_MIN_PAGE_CHARS, _TOTAL_CHARS // n), _HARD_CAP_CHARS // n)
    parts = [f"PDF with {n} page(s)."]
    for i, page in enumerate(pages, 1):
        parts.append(f"--- Page {i} of {n} ---\n{_compact(page)[:per_page]}")
    return "\n\n".join(parts)


def has_invoice_heading(page: str) -> bool:
    # Read a little past the heading area so a word cut off at its edge is
    # still seen with what follows it ("Invoice" + "Date" is not a heading).
    top = _compact(page)[:_HEADING_CHARS + 80]
    return any(m.start() < _HEADING_CHARS for m in _INVOICE_HEADING_RE.finditer(top))


def has_priced_table(page: str) -> bool:
    return any(
        _QTY_COLUMN_RE.search(line) and _PRICE_COLUMN_RE.search(line) and _AMOUNT_COLUMN_RE.search(line)
        for line in _compact(page).splitlines()
    )


def item_pages(check: InvoiceCheck | None, text: str) -> list[int] | None:
    """The pages to read line items from, or None to read every page.

    Only pages before the first invoice page or after the last one are left
    out, and only when the check labelled them as another document (packing
    list, waybill, certificate, ...), their own heading does not say invoice,
    and they have no priced item table. Pages between two invoice pages stay:
    a packing list between a tax invoice and a commercial invoice for the
    same goods is what tells the extraction they are two copies, not one
    longer invoice. Nothing is left out when the check failed, could not read
    the text, or labelled no page as an invoice.
    """
    if check is None or check.error or not check.readable or not check.contains_invoice:
        return None
    pages = split_pages(text)
    labels = [check.page_types.get(number) for number in range(1, len(pages) + 1)]
    if len(pages) < 2 or "invoice" not in labels:
        return None
    needed = [
        number for number, (page, label) in enumerate(zip(pages, labels), 1)
        if label not in NON_INVOICE_PAGE_TYPES or has_invoice_heading(page) or has_priced_table(page)
    ]
    first, last = needed[0], needed[-1]
    return list(range(first, last + 1)) if (first, last) != (1, len(pages)) else None


def detect_invoice_kinds(check: InvoiceCheck | None, text: str) -> list[str]:
    """Kinds of invoice named at the top of the invoice pages (every page when
    the check labelled none); "commercial" when the title is just "Invoice"."""
    pages = split_pages(text)
    labelled = [n for n in range(1, len(pages) + 1) if check is not None and check.page_types.get(n) == "invoice"]
    heads = [_compact(pages[n - 1])[:_HEADING_CHARS] for n in labelled or range(1, len(pages) + 1)]
    return [kind for kind, title in _KIND_TITLES if any(title.search(h) for h in heads)] or ["commercial"]


def invoice_kind_label(kinds: list[str]) -> str | None:
    return ", ".join(INVOICE_KINDS[k] for k in kinds if k in INVOICE_KINDS) or None


def _as_label(value: Any) -> str:
    return re.sub(r"[\s-]+", "_", str(value).strip().lower())


def _as_page_type(value: Any) -> str:
    label = _as_label(value)
    if "invoice" in label:
        return "invoice"
    return _SYNONYM_PAGE_TYPE.get(label, label)


def _page_types(value: Any, n_pages: int) -> dict[int, str]:
    pairs: list[tuple[Any, Any]] = []
    if isinstance(value, dict):
        pairs = list(value.items())
    elif isinstance(value, list):
        for i, v in enumerate(value, 1):
            pairs.append((v.get("page"), v.get("type")) if isinstance(v, dict) else (i, v))
    out: dict[int, str] = {}
    for page, label in pairs:
        try:
            number = int(str(page).strip())
        except (TypeError, ValueError):
            continue
        if 1 <= number <= n_pages and isinstance(label, str) and label.strip():
            out[number] = _as_page_type(label)
    return dict(sorted(out.items()))


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
    n_pages = len(split_pages(text))
    page_types = _page_types(data.get("page_types"), n_pages)
    invoice_pages = [page for page, label in page_types.items() if label == "invoice"]
    # The page labels are the more specific answer: a "yes" whose labels put
    # every page down as another document is a "no", and a "no" that labels a
    # page as an invoice is let through.
    every_page_other = len(page_types) == n_pages and all(
        label in NON_INVOICE_PAGE_TYPES for label in page_types.values()
    )
    types = data.get("document_types")
    return InvoiceCheck(
        contains_invoice=(bool(invoice_pages) or (verdict and not every_page_other)) or not readable,
        readable=readable,
        invoice_pages=invoice_pages,
        page_types=page_types,
        document_types=[str(t) for t in types] if isinstance(types, list) else [],
        reason=str(data.get("reason") or ""),
        model=model,
        token_usage=token_usage,
    )
