from __future__ import annotations
import re
from dataclasses import dataclass, field

# Below this many real words, treat the page as blank/unreadable rather than
# running the (much noisier) keyword checks against a near-empty string.
_MIN_WORDS_FOR_NON_BLANK = 15

# Phrases that, if present, confidently mark the document as an invoice (or an
# invoice-equivalent customs document this pipeline is built to extract, e.g.
# a shipping bill/bill of entry) -- checked BEFORE the non-invoice list, since
# a genuine invoice can still mention "packing list enclosed" in passing.
_INVOICE_STRONG = (
    "tax invoice", "commercial invoice", "proforma invoice", "export invoice",
    "import invoice", "invoice no", "invoice number", "invoice date",
    "bill of entry", "shipping bill", "expwp", "expwop",
)
_INVOICE_WEAK = ("invoice",)

# Other customs/shipping paperwork this pipeline must NOT try to extract as if
# it were an invoice. Checked only when no invoice phrase above matched.
_NON_INVOICE_STRONG = (
    "packing list", "packing slip", "weighment slip", "weight list",
    "certificate of origin", "bill of lading", "delivery challan",
    "delivery note", "purchase order", "insurance certificate",
    "inspection certificate", "test certificate", "fumigation certificate",
    "quotation", "deed of", "power of attorney", "memorandum of understanding",
    "non disclosure agreement", "lease agreement", "rental agreement",
)

_WORD_RE = re.compile(r"[a-zA-Z0-9]{2,}")


@dataclass
class InvoiceClassification:
    is_invoice: bool
    document_type: str
    reason: str
    matched: list[str] = field(default_factory=list)


def _phrase_present(text_lower: str, phrase: str) -> bool:
    if " " in phrase:
        pattern = r"\s+".join(re.escape(w) for w in phrase.split())
        return re.search(pattern, text_lower) is not None
    return re.search(rf"\b{re.escape(phrase)}\b", text_lower) is not None


def classify_document(text: str) -> InvoiceClassification:
    words = _WORD_RE.findall(text or "")
    if len(words) < _MIN_WORDS_FOR_NON_BLANK:
        return InvoiceClassification(
            is_invoice=False,
            document_type="blank_or_empty",
            reason=f"only {len(words)} word(s) of extractable text -- looks like a blank or unreadable page",
        )

    text_lower = text.lower()

    invoice_matches = [p for p in (*_INVOICE_STRONG, *_INVOICE_WEAK) if _phrase_present(text_lower, p)]
    if invoice_matches:
        return InvoiceClassification(
            is_invoice=True,
            document_type="invoice",
            reason="invoice-indicating text found",
            matched=invoice_matches,
        )

    non_invoice_matches = [p for p in _NON_INVOICE_STRONG if _phrase_present(text_lower, p)]
    if non_invoice_matches:
        guessed_type = non_invoice_matches[0]
        return InvoiceClassification(
            is_invoice=False,
            document_type=guessed_type,
            reason=f"looks like a '{guessed_type}', not an invoice",
            matched=non_invoice_matches,
        )

    return InvoiceClassification(
        is_invoice=True,
        document_type="invoice",
        reason="no clear non-invoice signal found; extracting as invoice by default",
    )
