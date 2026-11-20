"""Part-number candidates for one line item.

An invoice row can print several identifiers for the same item (Manufacturer
PN, Internal PN, Customer PN, Article No, Model No ...). The master-data
mapping needs every one of them, so each item carries `part_numbers`, an
ordered list with the best candidate first, and `part_number` stays as that
first entry for anything that still reads a single value.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# No fixed ceiling: part_numbers is a candidate set the backend loops against
# the masters database, so a row contributes as many codes as it actually
# prints. A caller that needs a bound passes `limit=` itself.
MAX_PART_NUMBERS: int | None = None

# -- the labels a part number is printed under ---------------------------
# ONE source of truth for every alias seen on real invoices. The item-table
# column scan, the "label: value" scan of the raw document text and the
# extraction prompt are all built from these, so a newly seen alias is added
# here once and all three follow.
#
# Readable form, for the prompt and for anyone adding to the list:
PART_LABEL_ALIASES: tuple[str, ...] = (
    "Part No", "Part Number", "Part Code", "P/N", "PN", "P.N.",
    "APN", "KPN", "MPN", "CPT#",
    "Manufacturer PN", "MFG Part No", "MFG", "Mfr Part No",
    "Internal PN", "Customer PN", "Cust P/N", "Customer Part No",
    "Supplier PN", "Vendor Part No", "Your Part No", "Our Part No",
    "<Brand> Part No (e.g. VISTEON PART NO.)",
    "ART", "Art No", "Article", "Article No",
    "Material", "Material No", "Material Code", "Customer Material", "Cust Material",
    "Product", "Product No", "Product Code",
    "Model", "Model No", "Type No",
    "Item Code", "Catalog No", "Cat No", "Drawing No", "SKU", "FG Code",
    "Seller Reference No", "Reference No", "Ref No",
    "Particulars",
    "a code printed inside the description / model / material cell",
)

# An owner or brand word that may sit in FRONT of any label below --
# "Customer Part No", "MFG Part Number", "VISTEON PART NO.", "ABB Article No".
# The generic brand branch needs 3+ letters so that "HS Code" can never be
# read as brand "HS" + a label.
_OWNER = (
    r"(?:(?:Manufactur(?:er|ing|e)|Mfr|Mfg|M\.F\.G|Internal|Customer|Cust"
    r"|Supplier|Seller|Vendor|Buyer|Your|Our|Factory|Original"
    r"|[A-Za-z][A-Za-z&.]{2,})"
    r"\s*\.?\s*){0,2}"
)

# The suffix a label may carry, spelled out so that "No" can never eat the
# "No" of "Number" and leave "mber" behind as the value.
_SUF = r"(?:No\b|Nos\b|Nr\b|Number|Numbers|Code|#|ID\b)"

# The label itself. The suffix is optional on labels whose bare word is
# already unambiguous ("Material", "Article"), and required on the ones that
# are not ("Reference", "Ref").
_LABEL = (
    r"(?:P\s*[/.]?\s*N\.?"
    r"|PN|APN|KPN|MPN|SKU"
    r"|M\.?F\.?G\b"
    rf"|Part\s*\.?\s*{_SUF}?"
    rf"|Art(?:icle)?\b\.?\s*{_SUF}?"
    rf"|Material\s*{_SUF}?"
    rf"|Product\s*{_SUF}?"
    rf"|Model\s*{_SUF}?"
    rf"|Type\s*{_SUF}?"
    r"|Item\s*Code"
    rf"|Catalog(?:ue)?\s*{_SUF}?"
    r"|Cat\.?\s*No\b"
    rf"|Drawing\s*{_SUF}?"
    r"|F\.?G\.?\s*Code"
    r"|CPT\s*#?"
    rf"|Reference\s*{_SUF}"
    r"|Ref\.?\s*No\b"
    r"|Particulars)"
)

#: A whole item-table column header that IS a part-number column.
PART_HEADER_RE = re.compile(rf"^\s*{_OWNER}{_LABEL}\s*\.?\s*$", re.IGNORECASE)

#: The same label found INSIDE a longer header -- "Description/Customer Part
#: No.", "Material / Description": such a cell carries the description AND
#: the item's own code.
PART_LABEL_IN_HEADER_RE = re.compile(rf"{_OWNER}{_LABEL}\s*\.?", re.IGNORECASE)

#: "Cust P/N: AB-12", "MFG PART NO. 1004128", "Article No 70-3569" printed in
#: free text rather than in a column of its own.
PART_VALUE_RE = re.compile(
    rf"{_OWNER}{_LABEL}\s*\.?\s*[:\-#.]?\s*([A-Za-z0-9][A-Za-z0-9\-/.]{{2,}})",
    re.IGNORECASE,
)

#: An HS / HSN / tariff / CTH code is never a part number, however it is
#: labeled -- used to drop a tariff code the model listed as one.
HSN_VALUE_RE = re.compile(
    r"(?:\bHSN?\b|\bCTH\b|\bTariff\b|\bCustoms?\s*Tariff\b|\bHS\s*Code\b)"
    r"\s*(?:Code\b|No\b\.?|Number)?\s*[:\-#.]?\s*(\d[\d.\s]{5,13}\d)",
    re.IGNORECASE,
)


_EMPTY = {"", "NA", "N/A", "NONE", "NULL", "NIL", "-", "--"}
_SPLIT_RE = re.compile(r"\s*(?:[;\n\r]|,(?=\s)|\s\|\s|\s{3,})\s*")


# -- a date is never part of an identifier --------------------------------
# PO / order cells routinely carry their own date inside the same value --
# "249876187 000170 dated 10.08.2026 / 5191747503" -- and no masters database
# knows a key with a date in it. The date is cut out and the identifiers
# around it are kept.
#
# Only an UNAMBIGUOUS date is removed: one introduced by a date word, one
# spelled with a month name, or one carrying a full four-digit year. A bare
# "12-05-99" is left alone -- with no marker it is as likely to be a part
# number as a date, and `part_numbers` is a candidate set, so keeping it
# costs one failed lookup while dropping it could lose the only real code.
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*"
_YEAR4 = r"(?:19|20)\d{2}"
_DD = r"\d{1,2}"
_DATE_BODY = (
    rf"(?:{_DD}\s*[-/.]\s*{_DD}\s*[-/.]\s*{_YEAR4}"
    rf"|{_YEAR4}\s*[-/.]\s*{_DD}\s*[-/.]\s*{_DD}"
    rf"|{_DD}\s*[-/. ]\s*{_MONTH}\s*[-/. ]\s*(?:{_YEAR4}|\d{{2}})"
    rf"|{_MONTH}\s*[-/. ]\s*{_DD}\s*,?\s*[-/. ]\s*{_YEAR4})"
)
_DATE_MARKER = r"(?:dated|date|dt|d\.t)\b\.?"

#: A date introduced by a date word -- "dated 10.08.2026", "(dt. 10/08/26)".
#: The marker's own two-digit-year form counts here: "dated" settles it.
DATE_WITH_MARKER_RE = re.compile(
    rf"[\s,(\[]*\b{_DATE_MARKER}\s*:?\s*"
    rf"(?:{_DATE_BODY}|{_DD}\s*[-/.]\s*{_DD}\s*[-/.]\s*\d{{2}}(?!\d))"
    rf"\s*[)\]]*",
    re.IGNORECASE,
)

#: A full date standing inside a value with no date word to introduce it.
BARE_DATE_RE = re.compile(rf"(?<![A-Za-z0-9]){_DATE_BODY}(?![A-Za-z0-9])", re.IGNORECASE)

def strip_dates(value: Any) -> str:
    """The value with any date printed inside it removed, so a PO cell's date
    never reaches the masters lookup. Returns "" when nothing but a date is
    left. A value holding no date comes back untouched apart from trimming."""
    text = str(value or "").strip()
    if not text:
        return ""
    out = BARE_DATE_RE.sub(" ", DATE_WITH_MARKER_RE.sub(" ", text))
    if out == text:
        return text
    # Only tidy up separators that the removal left dangling -- a value with
    # no date keeps its own punctuation exactly as printed.
    return re.sub(r"\s{2,}", " ", out).strip().strip(" ,;:-/|").strip()


def part_key(value: Any) -> str:
    """Comparison key: upper-case letters and digits only."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _pieces(value: Any) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, (list, tuple, set)):
        for v in value:
            yield from _pieces(v)
        return
    if isinstance(value, dict):
        yield from _pieces(value.get("value"))
        return
    text = str(value).strip()
    if not text:
        return
    for part in _SPLIT_RE.split(text):
        part = part.strip().strip("\"'")
        if part:
            yield part


def clean_part_numbers(*sources: Any, limit: int | None = MAX_PART_NUMBERS) -> list[str]:
    """Merge candidates from strings and lists into one ordered, de-duplicated
    list. Earlier sources win the earlier slots; values are kept as printed.
    Every code the row prints is kept -- `limit` is None (no ceiling) unless a
    caller asks for one."""
    out: list[str] = []
    seen: set[str] = set()
    for source in sources:
        for raw in _pieces(source):
            # A date inside the value belongs to no identifier: cut it out and
            # keep the codes around it. A value that was only a date is gone.
            piece = strip_dates(raw)
            key = part_key(piece)
            if len(key) < 2 or piece.strip().upper() in _EMPTY or key in seen:
                continue
            seen.add(key)
            out.append(piece)
            if limit is not None and len(out) >= limit:
                return out
    return out


def primary_part_number(item: dict[str, Any]) -> str:
    """The single best part number of an item: `part_number` when set (the
    guardrails keep it in step with the list), else the first listed."""
    found = all_part_numbers(item)
    return found[0] if found else ""


def all_part_numbers(item: dict[str, Any]) -> list[str]:
    """Every candidate of an item, primary first (also for rows that only have
    the single `part_number`)."""
    return clean_part_numbers(item.get("part_number"), item.get("part_numbers"))


def share_part_number(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """True when two rows name the same item. A row's own order number is
    excluded: it is carried in part_numbers as an extra masters-lookup
    candidate, and every row of one purchase order shares it -- matching on it
    would make unrelated items look like the same one."""
    def keys(item: dict[str, Any]) -> set[str]:
        order = part_key(item.get("order_number"))
        return {k for k in (part_key(v) for v in all_part_numbers(item)) if k and k != order}
    return bool(keys(a) & keys(b))
