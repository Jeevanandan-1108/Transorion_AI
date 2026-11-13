"""Charges, taxes and deductions printed between an invoice's item total and its
grand total: "ADD AIR FREIGHT 1663.69", "LESS TRADE DISCOUNT 119.05",
"IGST @ 18% 1,800.00", "Customs Clearance 55.00", "Verpackung 25.00".

One vocabulary for the three places that need it: the totals check explains a
gap between the summed line items and the printed total with these lines, the
re-extraction judges which of two item lists is nearer the truth with them, and
the item guardrails use the same words to spot a charge the model listed as if
it were an item.

Text reaching here has been through normalize_decimal_commas, so amounts are
written with a decimal point ("1663.69", "1,663.69", "1,23,456.00")."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import Any, Iterator

# --- vocabulary -------------------------------------------------------------------

# Takes its amount OFF the item total.
_DEDUCTION = "|".join((
    r"less", r"discounts?", r"rebates?", r"deduct(?:ions?)?",
    r"abzgl", r"abzüglich", r"\w*rabatt", r"skonto", r"\w*nachlass", r"abzug",    # German
    r"remises?", r"escompte", r"rabais", r"ristourne",                             # French
    r"sconto", r"abbuono",                                                         # Italian
    r"descuento", r"bonificaci[oó]n", r"rebaja",                                   # Spanish
    r"desconto", r"abatimento",                                                    # Portuguese
    r"korting",                                                                    # Dutch
    r"iskonto", r"indirim",                                                        # Turkish
    r"diskon", r"potongan",                                                        # Indonesian
    r"έκπτωση",                                                                    # Greek
))
# Adds its amount: freight, packing, insurance, customs, fees, taxes.
_CHARGE = "|".join((
    r"freight", r"frt", r"carriage", r"shipping", r"postage", r"courier", r"transport(?:ation)?",
    r"insurance", r"packing", r"packaging", r"handling", r"forwarding", r"charges?", r"fees?",
    r"surcharges?", r"customs", r"clearance", r"dut(?:y|ies)",
    r"tax(?:es)?", r"vat", r"gst", r"igst", r"cgst", r"sgst", r"utgst", r"cess",
    r"fracht\w*", r"verpackung\w*", r"versand\w*", r"porto", r"\w*kosten", r"\w*gebühr(?:en)?",   # German
    r"\w*zuschlag", r"zoll", r"zollabfertigung", r"verzollung", r"mwst", r"ust", r"mehrwertsteuer",
    r"umsatzsteuer",
    r"frais", r"emballage", r"tva", r"douanes?", r"assurance",                     # French
    r"spese", r"trasporto", r"imball(?:o|aggio)", r"iva", r"dogana(?:le)?", r"assicurazione",  # Italian
    r"portes", r"flete", r"embalaje", r"aduanas?", r"seguro", r"env[ií]o", r"gastos", r"recargo",  # Spanish
    r"frete", r"embalagem", r"despesas", r"impostos?", r"ipi", r"icms",            # Portuguese
    r"vracht\w*", r"verpakking\w*", r"verzend\w*", r"btw", r"verzekering", r"toeslag",  # Dutch
    r"navlun", r"nakliye", r"ambalaj", r"kdv", r"sigorta", r"gümrük",              # Turkish
    r"ongkos", r"pengiriman", r"asuransi", r"pajak", r"ppn", r"biaya",             # Indonesian
    r"thuế", r"phí", r"vận\s+chuyển", r"bảo\s+hiểm",                               # Vietnamese
    r"φπα", r"μεταφορικά", r"ασφάλιστρα",                                          # Greek
))
# Printed with or without its sign, so it is tried both ways.
_ROUND_OFF = r"round(?:ed)?[\s-]*off|rounding(?:[\s-]*off)?|rundung|arrondi"
_ADD = r"add|plus|zzgl|zuzüglich"

_DEDUCTION_RE = re.compile(rf"\b(?:{_DEDUCTION})\b", re.IGNORECASE)
_CHARGE_RE = re.compile(rf"\b(?:{_ADD}|{_CHARGE}|{_ROUND_OFF})\b", re.IGNORECASE)
_ROUND_OFF_RE = re.compile(rf"\b(?:{_ROUND_OFF})\b", re.IGNORECASE)
_STARTS_WITH_KEYWORD_RE = re.compile(
    rf"^\W*(?:{_DEDUCTION}|{_ADD}|{_CHARGE}|{_ROUND_OFF})\b", re.IGNORECASE,
)

# --- amounts ----------------------------------------------------------------------

# "1663.69", "1,663.69", "1,23,456.00" -- not a date ("19.08.2026") or a rate ("18.00%").
_DECIMAL_RE = re.compile(r"(?<![\d.,/])\d[\d,]*\.\d{2}(?![\d%])(?![.,]\d)(?!\s*%)")
# A whole amount ("USD 250", "55 EUR", "250/-") counts only next to a currency mark,
# so codes, counts and HS numbers on the same line are never read as money.
_WHOLE_RE = re.compile(r"(?<![\w.,/-])(?:\d{1,3}(?:,\d{3})+|\d{1,5})(?![\d.,%])(?!/\d)(?!\s*%)")
_CURRENCY = (
    r"(?:USD|EUROS?|EUR|INR|GBP|JPY|CNY|RMB|AED|SGD|CHF|AUD|CAD|HKD|SEK|NOK|DKK|PLN|CZK|HUF|TRY"
    r"|BRL|ARS|IDR|VND|THB|MYR|KRW|ZAR|SAR|NZD|MXN|Rs\.?|₹|\$|€|£|¥)"
)
_CURRENCY_BEFORE_RE = re.compile(rf"(?<![A-Za-z]){_CURRENCY}\s*[:.]?\s*$")
_CURRENCY_AFTER_RE = re.compile(rf"\s*(?:{_CURRENCY}(?![A-Za-z])|/-)")
# Any other number on the line, for the quantity x price test.
_NUMBER_RE = re.compile(r"(?<![\w.,/-])\d[\d,]*(?:\.\d+)?(?![\d%])(?![.,]\d)(?!\s*%)")
_ROW_TOLERANCE = Decimal("0.05")

MAX_CHARGE_LINES = 12


def _dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        return None


def money_amounts(line: str) -> list[Decimal]:
    """The decimal amounts printed on a line, leaving out dates and rates."""
    return [d for m in _DECIMAL_RE.finditer(line or "") if (d := _dec(m.group())) is not None]


def printed_amounts(text: str) -> set[Decimal]:
    return set(money_amounts(text))


def _amount_on(line: str) -> tuple[Decimal, int, int] | None:
    """The line's amount: its last decimal amount, else its one currency-marked whole number."""
    decimals = list(_DECIMAL_RE.finditer(line))
    if decimals:
        m = decimals[-1]
    else:
        marked = [
            w for w in _WHOLE_RE.finditer(line)
            if _CURRENCY_BEFORE_RE.search(line[:w.start()]) or _CURRENCY_AFTER_RE.match(line[w.end():])
        ]
        if len(marked) != 1:
            return None
        m = marked[0]
    value = _dec(m.group())
    if not value:
        return None
    return value, m.start(), m.end()


def _explicit_sign(line: str, start: int, end: int) -> int | None:
    before = line[:start]
    if re.search(r"\(\s*[-−]\s*\)", before):
        return -1
    if re.search(r"\(\s*\+\s*\)", before):
        return 1
    # "-50.00" with the minus on the number -- not the ":-" that ends a label ("IN EURO:-  1663.69")
    if re.search(r"(?:^|[\s(])[-−]$", before):
        return -1
    if re.search(r"\(\s*$", before) and re.match(r"\s*\)", line[end:]):
        return -1
    return None


def _is_item_row(line: str, value: Decimal, start: int) -> bool:
    """True when two other numbers on the line multiply to the amount -- a
    quantity and a unit price, so the line is an item row whose description
    happens to contain a charge word ("HEAVY DUTY FILTER  10  5.00  50.00")."""
    others = [
        d for m in _NUMBER_RE.finditer(line)
        if m.start() != start and (d := _dec(m.group())) is not None and d > 0
    ]
    return any(abs(a * b - value) <= _ROW_TOLERANCE for a, b in combinations(others, 2))


def find_charge_lines(text: str) -> list[tuple[str, Decimal]]:
    """Every (label, signed amount) the text prints as a charge, tax or deduction.

    A line counts when the text in front of its amount carries a charge or
    deduction word. Deductions are negative; a charge is negative only when its
    amount is printed negative ("-0.40", "(-) 0.40", "(0.40)"), and an unsigned
    round-off is offered both ways. A line whose label does not start with such
    a word and whose other numbers multiply to the amount is an item row, not a
    charge, and is skipped -- otherwise a row the model left out could "explain"
    its own absence from the line items."""
    found: list[tuple[str, Decimal]] = []
    seen: set[tuple[str, Decimal]] = set()
    for raw in (text or "").splitlines():
        line = raw.replace("│", " ")
        hit = _amount_on(line)
        if hit is None:
            continue
        value, start, end = hit
        label = re.sub(r"\s+", " ", line[:start]).strip(" :-=")
        deduction = _DEDUCTION_RE.search(label) is not None
        if not deduction and _CHARGE_RE.search(label) is None:
            continue
        if not _STARTS_WITH_KEYWORD_RE.match(label) and _is_item_row(line, value, start):
            continue
        sign = _explicit_sign(line, start, end)
        if deduction:
            signs: tuple[int, ...] = (-1,)
        elif sign is not None:
            signs = (sign,)
        elif _ROUND_OFF_RE.search(label):
            signs = (1, -1)
        else:
            signs = (1,)
        for s in signs:
            key = (label[:60], value * s)
            if key not in seen:
                seen.add(key)
                found.append(key)
    return found


def charge_candidates(text: str, header: dict[str, Any] | None = None) -> list[tuple[str, Decimal]]:
    """find_charge_lines, plus the tax amount the model extracted when that very
    amount is printed in the text -- a tax set out in a table ("IGST Amount" in
    a header row, 1,800.00 in the row below) has no label on the amount's line."""
    found = find_charge_lines(text)
    tax = _dec((header or {}).get("tax_amount"))
    if tax is not None and tax > 0 and tax in printed_amounts(text) and all(a != tax for _, a in found):
        found.append(("tax_amount", tax))
    return found


def _subsets(candidates: list[tuple[str, Decimal]]) -> Iterator[tuple[tuple[str, Decimal], ...]]:
    for size in range(1, len(candidates) + 1):
        yield from combinations(candidates, size)


def reconcile(
    base: Decimal, target: Decimal, candidates: list[tuple[str, Decimal]], tolerance: Decimal,
) -> list[tuple[str, Decimal]] | None:
    """The smallest set of candidates that takes base to target, or None.
    Gives up beyond MAX_CHARGE_LINES candidates: the more combinations there
    are, the likelier one matches by chance."""
    if not candidates or len(candidates) > MAX_CHARGE_LINES:
        return None
    for subset in _subsets(candidates):
        if abs(base + sum((amt for _, amt in subset), Decimal("0")) - target) <= tolerance:
            return list(subset)
    return None


def closest_reconciliation(base: Decimal, target: Decimal, candidates: list[tuple[str, Decimal]]) -> Decimal:
    """How far base stays from target after the best set of candidates (none included)."""
    best = abs(target - base)
    if len(candidates) <= MAX_CHARGE_LINES:
        for subset in _subsets(candidates):
            best = min(best, abs(target - base - sum((amt for _, amt in subset), Decimal("0"))))
    return best


# --- charge rows in the line items --------------------------------------------------

_FILLERS = r"and|of|de|d|du|des|la|le|les|di|del|e|y|und|et|en|van|ve|dan"
_QUALIFIERS = (
    r"air|sea|ocean|inland|road|rail|marine|local|bank|documentation|documents?|other|others"
    r"|misc|miscellaneous|service|delivery|export|import|origin|destination|total"
)
_EXTRA = rf"{_FILLERS}|{_QUALIFIERS}|{_ADD}"
_CHARGE_NAME_RE = re.compile(
    rf"^(?:(?:{_EXTRA})\s+)*(?:{_CHARGE}|{_ROUND_OFF})(?:\s+(?:{_EXTRA}|{_CHARGE}|{_ROUND_OFF}))*$",
    re.IGNORECASE,
)


def is_charge_name(description: str) -> bool:
    """True when a line item's description is only the name of a charge --
    "FREIGHT", "Air Freight", "Customs Clearance", "Freight & Insurance",
    "IGST @ 18%", "Versandkosten" -- not a product that merely mentions one
    ("Freight container lock", "Heavy duty filter")."""
    words = " ".join(re.findall(r"[^\W\d_]+", description or ""))
    return bool(words) and _CHARGE_NAME_RE.match(words) is not None
