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

MAX_PART_NUMBERS = 8

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


def clean_part_numbers(*sources: Any, limit: int = MAX_PART_NUMBERS) -> list[str]:
    """Merge candidates from strings and lists into one ordered, de-duplicated
    list. Earlier sources win the earlier slots; values are kept as printed."""
    out: list[str] = []
    seen: set[str] = set()
    for source in sources:
        for piece in _pieces(source):
            key = part_key(piece)
            if len(key) < 2 or piece.strip().upper() in _EMPTY or key in seen:
                continue
            seen.add(key)
            out.append(piece)
            if len(out) >= limit:
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
    ka = {part_key(v) for v in all_part_numbers(a)}
    kb = {part_key(v) for v in all_part_numbers(b)}
    return bool(ka & kb)
