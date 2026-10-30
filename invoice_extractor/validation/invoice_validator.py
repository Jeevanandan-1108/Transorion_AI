from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from invoice_extractor.schema.template_item_fields import is_export_template, item_fields_for_template
from invoice_extractor.validation.validate import _GSTIN_RE, _is_valid_ad_code


AMOUNT_TOLERANCE = Decimal("0.05")

RETRYABLE_RULES = {
    "REQUIRED_FIELD_MISSING",
    "QTY_X_UNIT_PRICE",
    "TAX_RECONCILIATION",
    "PART_NUMBER_SOURCE_MATCH",
    "ORDER_NUMBER_SOURCE_MATCH",
    "MODEL_SOURCE_MATCH",
    "COUNTRY_OF_ORIGIN_CONSISTENCY",
    "FORMAT_TYPE",
    "MULTI_INVOICE_TOTAL_SPLIT",
    "LINE_ITEMS_INCOMPLETE",
    "HSN_FORMAT",
}


@dataclass
class ValidationCheck:
    rule: str
    status: str
    message: str = ""
    item: int | None = None
    field: str | None = None
    expected: Any = None
    actual: Any = None
    difference: Any = None
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("expected", "actual", "difference"):
            if isinstance(d[k], Decimal):
                d[k] = float(d[k])
        return d


@dataclass
class ValidationResult:
    valid: bool
    status: str
    checks: list[ValidationCheck] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationCheck]:
        return [c for c in self.checks if c.status == "ERROR"]

    @property
    def warnings(self) -> list[ValidationCheck]:
        return [c for c in self.checks if c.status == "WARNING"]

    @property
    def passed_rules(self) -> list[str]:
        return [c.rule for c in self.checks if c.status == "PASS"]

    @property
    def retryable_errors(self) -> list[ValidationCheck]:
        return [c for c in self.errors if c.retryable]

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "status": self.status,
            "errors": [c.to_dict() for c in self.errors],
            "warnings": [c.to_dict() for c in self.warnings],
        }


_ITEM_FIELDS: dict[str, dict[str, str | None]] = {
    "export": {
        "ser_no": "Item_Ser_No", "description": "Item_Desc",
        "quantity": "Item_Qty", "unit": "Item_Unit1",
        "unit_price": "Item_Unit_Price", "line_amount": "ItmTaxableVal",
        "taxable_value": "ItmTaxableVal",
        "tariff_qty": "Qty Tariff", "tariff_unit": "Unit Tariff",
        "country_of_origin": "Itm_Source_Cntry", "hsn": "Item_RITC",
        "igst_amount": "ItmIGSTamt", "igst_rate": "ItmIGSTPer",
        "state_code": "State Code", "total_packages": "ItmTotpkg",
    },
    "import": {
        "ser_no": "SL_No", "description": "Item_Desc1",
        "quantity": "Item_Qty", "unit": "Item_Unit",
        "unit_price": "Item_Unit_Price", "line_amount": "Item_Taxable_Val",
        "taxable_value": "Item_Taxable_Val",
        "tariff_qty": "Qty_Tariff", "tariff_unit": "Unit_Tariff",
        "country_of_origin": "Item_Country_Org", "hsn": "Item_RITC",
        "igst_amount": None, "igst_rate": None,
        "state_code": None, "total_packages": None,
    },
}

_COMMON_ITEM_FIELDS = ("part_number", "model_or_type", "order_number")

_REQUIRED_ITEM_CONCEPTS = ("ser_no", "description", "quantity", "unit_price")


def _item_fields_for(shipment_type: str) -> dict[str, str | None]:
    if shipment_type in _ITEM_FIELDS:
        return _ITEM_FIELDS[shipment_type]
    return item_fields_for_template(shipment_type)


def _item_field(shipment_type: str, concept: str) -> str | None:
    return _item_fields_for(shipment_type).get(concept)


def _per_unit_divisor(item: dict[str, Any], shipment_type: str) -> Decimal:
    col = _item_field(shipment_type, "per_unit")
    if not col:
        return Decimal("1")
    m = re.search(r"\d+(?:\.\d+)?", str(item.get(col) or ""))
    n = Decimal(m.group()) if m else None
    return n if n and n > 0 else Decimal("1")


def _rate_unit_mismatch(item: dict[str, Any], shipment_type: str) -> tuple[str, str] | None:
    rate_unit_col = _item_field(shipment_type, "rate_unit")
    unit_col = _item_field(shipment_type, "unit")
    if not (rate_unit_col and unit_col):
        return None
    unit = _normalize_token(item.get(unit_col))
    rate_unit = _normalize_token(item.get(rate_unit_col))
    if unit and rate_unit and unit != rate_unit:
        return unit, rate_unit
    return None


def _computed_line_value(item: dict[str, Any], shipment_type: str) -> Decimal | None:
    qty = _to_decimal(item.get(_item_field(shipment_type, "quantity")))
    price = _to_decimal(item.get(_item_field(shipment_type, "unit_price")))
    if qty is None or price is None or _rate_unit_mismatch(item, shipment_type):
        return None
    return (qty * price / _per_unit_divisor(item, shipment_type)).quantize(Decimal("0.01"))


def _to_decimal(value: Any) -> Decimal | None:
    """Best-effort Decimal conversion. None means "not a real number
    present" (missing/blank/non-numeric) -- callers must treat that as
    "can't check this", never as zero.

    Strings get the same thousands-separator/currency-symbol cleanup as
    validate.py's own _coerce_number() -- confirmed needed: a template with
    no hand-tuned item mapper (e.g. iCaffe import Template) never routes its
    item fields through _coerce_number() at all (_map_generic_item() copies
    _extra_template_fields through verbatim), so a perfectly legitimate
    "1,753.000" reached here untouched and Decimal() rejected the comma,
    reporting a real number as FORMAT_TYPE "not numeric"."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, float):
            return Decimal(str(value))
        if isinstance(value, str):
            cleaned = value.strip()
            for sym in ("$", "€", "£", "₹", ","):
                cleaned = cleaned.replace(sym, "")
            cleaned = cleaned.strip()
            if not cleaned:
                return None
            return Decimal(cleaned)
        return Decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        return None


_COMBINED_SEPARATOR_RE = re.compile(r"\s+/\s*|\s*/\s+")


def _split_combined(value: Any) -> list[str]:
    return [p.strip() for p in _COMBINED_SEPARATOR_RE.split(str(value or "")) if p.strip()]


def _normalize_token(s: Any) -> str:
    return re.sub(r"\s+", "", str(s or "")).upper()


def validate_required_fields(
    items: list[dict[str, Any]], shipment_type: str
) -> list[ValidationCheck]:
    checks = []
    for idx, item in enumerate(items, start=1):
        for concept in _REQUIRED_ITEM_CONCEPTS:
            col = _item_field(shipment_type, concept)
            if col is None:
                continue
            val = item.get(col)
            if val is None or val == "":
                checks.append(ValidationCheck(
                    rule="REQUIRED_FIELD_MISSING", status="ERROR", item=idx,
                    field=col, retryable=True,
                    message=f"Item {idx}: required field '{col}' ({concept}) is missing.",
                ))
            else:
                checks.append(ValidationCheck(
                    rule="REQUIRED_FIELD_MISSING", status="PASS", item=idx, field=col,
                ))
    return checks


def validate_line_items(
    items: list[dict[str, Any]], shipment_type: str, tolerance: Decimal = AMOUNT_TOLERANCE
) -> list[ValidationCheck]:
    checks = []
    qty_col = _item_field(shipment_type, "quantity")
    price_col = _item_field(shipment_type, "unit_price")
    amount_col = _item_field(shipment_type, "line_amount")

    for idx, item in enumerate(items, start=1):
        if amount_col is None:
            checks.append(ValidationCheck(
                rule="QTY_X_UNIT_PRICE", status="NOT_CHECKED", item=idx,
                message=(
                    f"Item {idx}: the {shipment_type} template has no per-line amount "
                    f"column to reconcile quantity x unit price against."
                ),
            ))
            continue

        units = _rate_unit_mismatch(item, shipment_type)
        if units:
            checks.append(ValidationCheck(
                rule="QTY_X_UNIT_PRICE", status="NOT_CHECKED", item=idx,
                message=(
                    f"Item {idx}: quantity is in '{units[0]}' but the rate is per '{units[1]}' "
                    f"-- quantity x unit price can't be compared to the line amount."
                ),
            ))
            continue

        qty = _to_decimal(item.get(qty_col))
        price = _to_decimal(item.get(price_col))
        amount = _to_decimal(item.get(amount_col))
        if qty is None or price is None or amount is None:
            checks.append(ValidationCheck(
                rule="QTY_X_UNIT_PRICE", status="NOT_CHECKED", item=idx,
                message=f"Item {idx}: quantity, unit price, or line amount is missing -- cannot check.",
            ))
            continue

        per = _per_unit_divisor(item, shipment_type)
        expected = (qty * price / per).quantize(Decimal("0.01"))
        per_note = f" / {per} (price per {per} units)" if per != 1 else ""
        diff = abs(expected - amount)
        if amount == 0 and expected > 0:
            checks.append(ValidationCheck(
                rule="QTY_X_UNIT_PRICE", status="WARNING", item=idx, field=amount_col,
                expected=expected, actual=amount, difference=diff,
                message=(
                    f"Item {idx}: {amount_col} is 0 although Quantity ({qty}) x Unit Price "
                    f"({price}){per_note} = {expected} -- free-of-cost line? Verify against the document."
                ),
            ))
        elif diff <= tolerance:
            checks.append(ValidationCheck(
                rule="QTY_X_UNIT_PRICE", status="PASS", item=idx,
                field=amount_col, expected=expected, actual=amount, difference=diff,
            ))
        else:
            checks.append(ValidationCheck(
                rule="QTY_X_UNIT_PRICE", status="ERROR", item=idx, field=amount_col,
                expected=expected, actual=amount, difference=diff, retryable=True,
                message=(
                    f"Item {idx}: Quantity ({qty}) x Unit Price ({price}){per_note} = {expected}, "
                    f"but extracted {amount_col} is {amount} (difference {diff})."
                ),
            ))
    return checks


def _parse_header_total(header: dict[str, Any]) -> Decimal | None:
    raw = header.get("invoice_total")
    if raw is None or raw == "":
        return None
    single = _to_decimal(raw)
    if single is not None:
        return single
    if len(_split_combined(header.get("invoice_number"))) < 2:
        return None
    decimals = [d for d in (_to_decimal(p) for p in _split_combined(raw)) if d is not None]
    return sum(decimals, Decimal("0")) if decimals else None


_ADJUSTMENT_NEG_RE = re.compile(r"\b(?:less|discount|rebate|deduction|deduct)\b", re.IGNORECASE)
_ADJUSTMENT_POS_RE = re.compile(
    r"\b(?:add|freight|insurance|packing|packaging|handling|forwarding|charges?)\b", re.IGNORECASE,
)
_AMOUNT_RE = re.compile(r"(?<![\d.,])\d[\d,]*\.\d{2}(?!\d)(?!\s*%)")
_MAX_ADJUSTMENT_CANDIDATES = 8


def _find_adjustment_candidates(invoice_text: str) -> list[tuple[str, Decimal]]:
    seen: set[tuple[str, Decimal]] = set()
    found: list[tuple[str, Decimal]] = []
    for line in (invoice_text or "").splitlines():
        amounts = _AMOUNT_RE.findall(line)
        if not amounts:
            continue
        negative = _ADJUSTMENT_NEG_RE.search(line) is not None
        if not negative and _ADJUSTMENT_POS_RE.search(line) is None:
            continue
        amount = _to_decimal(amounts[-1].replace(",", ""))
        if amount is None:
            continue
        label = re.sub(r"[│\s]+", " ", line).strip()
        label = re.sub(r"\s*[\d,]+\.\d{2}\s*$", "", label)[:60]
        signed = -amount if negative else amount
        if (label, signed) not in seen:
            seen.add((label, signed))
            found.append((label, signed))
    return found


def _reconcile_with_adjustments(
    base: Decimal, target: Decimal, candidates: list[tuple[str, Decimal]], tolerance: Decimal,
) -> list[tuple[str, Decimal]] | None:
    from itertools import combinations

    if not candidates or len(candidates) > _MAX_ADJUSTMENT_CANDIDATES:
        return None
    for size in range(1, len(candidates) + 1):
        for subset in combinations(candidates, size):
            if abs(base + sum((amt for _, amt in subset), Decimal("0")) - target) <= tolerance:
                return list(subset)
    return None


def validate_totals(
    header: dict[str, Any], items: list[dict[str, Any]], shipment_type: str,
    tolerance: Decimal = AMOUNT_TOLERANCE, invoice_text: str = "",
) -> list[ValidationCheck]:
    checks = []

    invoice_number_raw = str(header.get("invoice_number") or "").strip()
    invoice_number_parts = _split_combined(invoice_number_raw)
    invoice_total_raw = header.get("invoice_total")
    if len(invoice_number_parts) > 1 and invoice_total_raw not in (None, ""):
        invoice_total_parts = _split_combined(invoice_total_raw)
        if len(invoice_total_parts) != len(invoice_number_parts):
            checks.append(ValidationCheck(
                rule="MULTI_INVOICE_TOTAL_SPLIT", status="ERROR", field="invoice_total",
                expected=f"{len(invoice_number_parts)} '/'-separated total(s), one per sub-invoice",
                actual=invoice_total_raw,
                message=(
                    f"invoice_number holds {len(invoice_number_parts)} combined sub-invoices "
                    f"(\"{invoice_number_raw}\") but invoice_total has "
                    f"{len(invoice_total_parts)} part(s) (\"{invoice_total_raw}\") instead of "
                    f"{len(invoice_number_parts)}. Find EACH sub-invoice's own explicitly "
                    f"printed total and join them with \" / \" in the same order as "
                    f"invoice_number -- do not compute, sum, or otherwise deduce a single "
                    f"combined figure."
                ),
            ))

    amount_col = _item_field(shipment_type, "line_amount")
    invoice_total = _parse_header_total(header)

    computed = amount_col is None
    if computed and not (_item_field(shipment_type, "quantity") and _item_field(shipment_type, "unit_price")):
        checks.append(ValidationCheck(
            rule="HEADER_LINE_TOTAL", status="NOT_CHECKED",
            message=f"No per-line amount column on the {shipment_type} template to sum.",
        ))
        return checks

    if computed:
        line_amounts = [_computed_line_value(it, shipment_type) for it in items]
    else:
        line_amounts = [_to_decimal(it.get(amount_col)) for it in items]
    valid_amounts = [a for a in line_amounts if a is not None]
    if not valid_amounts or invoice_total is None:
        checks.append(ValidationCheck(
            rule="HEADER_LINE_TOTAL", status="NOT_CHECKED",
            message="Missing header invoice_total, or no line amounts available to sum.",
        ))
        return checks
    n_missing = len(line_amounts) - len(valid_amounts)
    if n_missing:
        checks.append(ValidationCheck(
            rule="HEADER_LINE_TOTAL", status="NOT_CHECKED",
            message=(
                f"{n_missing} of {len(line_amounts)} line item(s) have no line amount"
                f"{' (quantity/unit price missing, or quantity and rate in different units)' if computed else ''} -- "
                f"a partial sum can't be reconciled against the invoice total."
            ),
        ))
        return checks

    calculated_total = sum(valid_amounts, Decimal("0"))
    diff = abs(calculated_total - invoice_total)
    if diff <= tolerance:
        checks.append(ValidationCheck(
            rule="HEADER_LINE_TOTAL", status="PASS", field="invoice_total",
            expected=calculated_total, actual=invoice_total, difference=diff,
        ))
    elif (adjustments := _reconcile_with_adjustments(
        calculated_total, invoice_total, _find_adjustment_candidates(invoice_text), tolerance,
    )) is not None:
        applied = ", ".join(f"{label} ({amt:+})" for label, amt in adjustments)
        checks.append(ValidationCheck(
            rule="HEADER_LINE_TOTAL", status="PASS", field="invoice_total",
            expected=calculated_total + sum((amt for _, amt in adjustments), Decimal("0")),
            actual=invoice_total, difference=abs(
                calculated_total + sum((amt for _, amt in adjustments), Decimal("0")) - invoice_total
            ),
            message=(
                f"invoice_total {invoice_total} reconciles with the line total "
                f"{calculated_total} after the adjustment(s) printed on the invoice: {applied}."
            ),
        ))
    elif invoice_total - calculated_total > tolerance:
        checks.append(ValidationCheck(
            rule="LINE_ITEMS_INCOMPLETE", status="ERROR", field="line_items",
            expected=invoice_total, actual=calculated_total, difference=diff,
            message=(
                f"The invoice's printed total is {invoice_total} but the {len(valid_amounts)} line "
                f"item(s) extracted add up to only {calculated_total} ({diff} short): rows were "
                f"probably skipped, or a line amount was misread. Extract EVERY row of the item table, "
                f"from EVERY page -- a table that continues onto later pages is one table -- and copy "
                f"each row's unit price and amount from the right columns. Do not invent rows or amounts."
            ),
        ))
    else:
        checks.append(ValidationCheck(
            rule="HEADER_LINE_TOTAL", status="ERROR", field="invoice_total",
            expected=calculated_total, actual=invoice_total, difference=diff,
            retryable=False,
            message=(
                f"Header invoice_total {invoice_total} does not match calculated "
                f"line total {calculated_total} (difference: {diff}). This may be a "
                f"genuine mismatch printed on the source document itself, not "
                f"necessarily an extraction error -- verify against the document. "
                f"Not auto-retried: a past retry on this exact rule overwrote an "
                f"already-correct total with a wrong one to force a match."
            ),
        ))
    return checks


def validate_taxes(
    items: list[dict[str, Any]], shipment_type: str, tolerance: Decimal = AMOUNT_TOLERANCE
) -> list[ValidationCheck]:
    checks = []
    taxable_col = _item_field(shipment_type, "taxable_value")
    rate_col = _item_field(shipment_type, "igst_rate")
    amount_col = _item_field(shipment_type, "igst_amount")
    if not (taxable_col and rate_col and amount_col):
        checks.append(ValidationCheck(
            rule="TAX_RECONCILIATION", status="NOT_CHECKED",
            message=f"No taxable-value/IGST-rate/IGST-amount columns on the {shipment_type} template.",
        ))
        return checks

    for idx, item in enumerate(items, start=1):
        taxable = _to_decimal(item.get(taxable_col))
        rate_raw = item.get(rate_col)
        rate = _to_decimal(re.sub(r"[^\d.]", "", str(rate_raw))) if rate_raw not in (None, "") else None
        igst_amount = _to_decimal(item.get(amount_col))

        if taxable is None or rate is None or igst_amount is None:
            checks.append(ValidationCheck(
                rule="TAX_RECONCILIATION", status="NOT_CHECKED", item=idx,
                message=f"Item {idx}: taxable value, IGST rate, or IGST amount missing -- cannot check.",
            ))
            continue

        expected = (taxable * rate / Decimal("100")).quantize(Decimal("0.01"))
        diff = abs(expected - igst_amount)
        if diff <= tolerance:
            checks.append(ValidationCheck(
                rule="TAX_RECONCILIATION", status="PASS", item=idx, field=amount_col,
                expected=expected, actual=igst_amount, difference=diff,
            ))
        else:
            checks.append(ValidationCheck(
                rule="TAX_RECONCILIATION", status="ERROR", item=idx, field=amount_col,
                expected=expected, actual=igst_amount, difference=diff, retryable=True,
                message=(
                    f"Item {idx}: Taxable Value ({taxable}) x IGST Rate ({rate}%) = {expected}, "
                    f"but extracted {amount_col} is {igst_amount} (difference {diff})."
                ),
            ))
    return checks


def validate_cross_fields(
    header: dict[str, Any], items: list[dict[str, Any]], shipment_type: str
) -> list[ValidationCheck]:
    checks = []

    origin_col = _item_field(shipment_type, "country_of_origin")
    header_origin = _normalize_token(header.get("country_of_origin"))
    if origin_col and header_origin:
        for idx, item in enumerate(items, start=1):
            item_origin = _normalize_token(item.get(origin_col))
            if not item_origin:
                checks.append(ValidationCheck(
                    rule="COUNTRY_OF_ORIGIN_CONSISTENCY", status="NOT_CHECKED", item=idx,
                    message=f"Item {idx}: no item-level country of origin to compare.",
                ))
            elif item_origin == header_origin:
                checks.append(ValidationCheck(
                    rule="COUNTRY_OF_ORIGIN_CONSISTENCY", status="PASS", item=idx,
                    field=origin_col, expected=header_origin, actual=item_origin,
                ))
            else:
                checks.append(ValidationCheck(
                    rule="COUNTRY_OF_ORIGIN_CONSISTENCY", status="WARNING", item=idx,
                    field=origin_col, expected=header_origin, actual=item_origin,
                    message=(
                        f"Item {idx}: country of origin '{item_origin}' differs from "
                        f"header country_of_origin '{header_origin}'."
                    ),
                ))

    qty_col = _item_field(shipment_type, "quantity")
    tariff_qty_col = _item_field(shipment_type, "tariff_qty")
    if qty_col and tariff_qty_col:
        for idx, item in enumerate(items, start=1):
            qty = _to_decimal(item.get(qty_col))
            tqty = _to_decimal(item.get(tariff_qty_col))
            if qty is None or tqty is None:
                checks.append(ValidationCheck(
                    rule="QTY_VS_TARIFF_QTY", status="NOT_CHECKED", item=idx,
                    message=f"Item {idx}: quantity or tariff quantity missing -- cannot check.",
                ))
            elif qty == tqty:
                checks.append(ValidationCheck(
                    rule="QTY_VS_TARIFF_QTY", status="PASS", item=idx,
                    field=tariff_qty_col, expected=qty, actual=tqty,
                ))
            else:
                checks.append(ValidationCheck(
                    rule="QTY_VS_TARIFF_QTY", status="ERROR", item=idx, field=tariff_qty_col,
                    expected=qty, actual=tqty, difference=abs(qty - tqty), retryable=True,
                    message=f"Item {idx}: quantity ({qty}) does not match tariff quantity ({tqty}).",
                ))

    unit_col = _item_field(shipment_type, "unit")
    tariff_unit_col = _item_field(shipment_type, "tariff_unit")
    if unit_col and tariff_unit_col:
        for idx, item in enumerate(items, start=1):
            unit = _normalize_token(item.get(unit_col))
            tunit = _normalize_token(item.get(tariff_unit_col))
            if not unit or not tunit:
                checks.append(ValidationCheck(
                    rule="UNIT_VS_TARIFF_UNIT", status="NOT_CHECKED", item=idx,
                    message=f"Item {idx}: unit or tariff unit missing -- cannot check.",
                ))
            elif unit == tunit:
                checks.append(ValidationCheck(
                    rule="UNIT_VS_TARIFF_UNIT", status="PASS", item=idx, field=tariff_unit_col,
                ))
            else:
                checks.append(ValidationCheck(
                    rule="UNIT_VS_TARIFF_UNIT", status="WARNING", item=idx, field=tariff_unit_col,
                    expected=unit, actual=tunit,
                    message=f"Item {idx}: unit ('{unit}') does not match tariff unit ('{tunit}').",
                ))

    state_col = _item_field(shipment_type, "state_code")
    header_state = _normalize_token(header.get("state_code"))
    if state_col and header_state:
        for idx, item in enumerate(items, start=1):
            item_state = _normalize_token(item.get(state_col))
            if not item_state:
                checks.append(ValidationCheck(
                    rule="STATE_CODE_CONSISTENCY", status="NOT_CHECKED", item=idx,
                    message=f"Item {idx}: no item-level state code to compare.",
                ))
            elif item_state.lstrip("0") == header_state.lstrip("0"):
                checks.append(ValidationCheck(
                    rule="STATE_CODE_CONSISTENCY", status="PASS", item=idx, field=state_col,
                ))
            else:
                checks.append(ValidationCheck(
                    rule="STATE_CODE_CONSISTENCY", status="WARNING", item=idx, field=state_col,
                    expected=header_state, actual=item_state,
                    message=(
                        f"Item {idx}: state code '{item_state}' differs from "
                        f"header state_code '{header_state}'."
                    ),
                ))

    pkg_col = _item_field(shipment_type, "total_packages")
    header_pkgs = _to_decimal(header.get("total_packages"))
    if pkg_col and header_pkgs is not None:
        item_pkgs_list = [(idx, _to_decimal(item.get(pkg_col))) for idx, item in enumerate(items, start=1)]
        checked = [(idx, v) for idx, v in item_pkgs_list if v is not None]
        if checked:
            values = [v for _, v in checked]
            summed = sum(values, Decimal("0"))
            all_same_as_header = all(v == header_pkgs for v in values)
            if summed == header_pkgs or all_same_as_header:
                checks.append(ValidationCheck(
                    rule="PACKAGE_COUNT_CONSISTENCY", status="PASS",
                    field="total_packages", expected=header_pkgs,
                    message="Item-level package counts reconcile with header.total_packages.",
                ))
            else:
                checks.append(ValidationCheck(
                    rule="PACKAGE_COUNT_CONSISTENCY", status="WARNING",
                    field="total_packages", expected=header_pkgs, actual=summed,
                    message=(
                        f"Item-level package counts sum to {summed}, which matches neither "
                        f"header.total_packages ({header_pkgs}) nor a per-row copy of it."
                    ),
                ))
        else:
            checks.append(ValidationCheck(
                rule="PACKAGE_COUNT_CONSISTENCY", status="NOT_CHECKED",
                message="No item-level package count populated to compare against the header.",
            ))

    return checks


_PART_NO_PATTERNS = [
    re.compile(r'P\s*/\s*N\.?\s*[:\-]?\s*([A-Za-z0-9\-\./]{3,})', re.IGNORECASE),
    re.compile(r'Part\s*No\.?\s*[:\-]\s*([A-Za-z0-9\-\./]{3,})', re.IGNORECASE),
]
_MODEL_PATTERNS = [
    re.compile(r'Model(?:\s*(?:No\.?|Number))?\s*[:\-]\s*([A-Za-z0-9\-\./]{2,})', re.IGNORECASE),
    re.compile(r'\bType\s*[:\-]\s*([A-Za-z0-9\-\./]{2,})', re.IGNORECASE),
]
_ORDER_NO_PATTERNS = [
    re.compile(r'P\s*/\s*O\.?\s*[:\-]?\s*([A-Za-z0-9\-]{3,})', re.IGNORECASE),
    re.compile(r"(?:Purchase\s*)?Order\s*No\.?\s*[:\-]\s*([A-Za-z0-9\-]{3,})", re.IGNORECASE),
]


def _find_labeled_value(patterns: list[re.Pattern], text: str) -> str | None:
    for pat in patterns:
        m = pat.search(text or "")
        if m:
            return m.group(1)
    return None


def _find_all_labeled_values(patterns: list[re.Pattern], text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for pat in patterns:
        for m in pat.finditer(text or ""):
            norm = _normalize_token(m.group(1))
            if norm not in seen:
                seen.add(norm)
                found.append(m.group(1))
    return found


def validate_source_consistency(
    items: list[dict[str, Any]], shipment_type: str, invoice_text: str
) -> list[ValidationCheck]:
    checks = []
    desc_col = _item_field(shipment_type, "description")

    for idx, item in enumerate(items, start=1):
        description = str(item.get(desc_col) or "")

        part_no_in_text = _find_labeled_value(_PART_NO_PATTERNS, description)
        extracted_part_no = _normalize_token(item.get("part_number"))
        if part_no_in_text is None:
            checks.append(ValidationCheck(
                rule="PART_NUMBER_SOURCE_MATCH", status="NOT_CHECKED", item=idx,
                message=f"Item {idx}: no 'P/N:'/'Part No:' label found in the description to check against.",
            ))
        elif _normalize_token(part_no_in_text) == extracted_part_no:
            checks.append(ValidationCheck(
                rule="PART_NUMBER_SOURCE_MATCH", status="PASS", item=idx, field="part_number",
                expected=part_no_in_text, actual=item.get("part_number"),
            ))
        elif not extracted_part_no:
            checks.append(ValidationCheck(
                rule="PART_NUMBER_SOURCE_MATCH", status="WARNING", item=idx, field="part_number",
                expected=part_no_in_text, actual="",
                message=f"Item {idx}: description names part number '{part_no_in_text}' but part_number was left empty.",
            ))
        else:
            checks.append(ValidationCheck(
                rule="PART_NUMBER_SOURCE_MATCH", status="ERROR", item=idx, field="part_number",
                expected=part_no_in_text, actual=item.get("part_number"), retryable=True,
                message=(
                    f"Item {idx}: description says part number '{part_no_in_text}', "
                    f"but extracted part_number is '{item.get('part_number')}'."
                ),
            ))

        model_in_text = _find_labeled_value(_MODEL_PATTERNS, description)
        extracted_model = _normalize_token(item.get("model_or_type"))
        if model_in_text is None:
            checks.append(ValidationCheck(
                rule="MODEL_SOURCE_MATCH", status="NOT_CHECKED", item=idx,
                message=f"Item {idx}: no 'Model:'/'Type:' label found in the description to check against.",
            ))
        elif _normalize_token(model_in_text) == extracted_model:
            checks.append(ValidationCheck(
                rule="MODEL_SOURCE_MATCH", status="PASS", item=idx, field="model_or_type",
            ))
        elif not extracted_model:
            checks.append(ValidationCheck(
                rule="MODEL_SOURCE_MATCH", status="WARNING", item=idx, field="model_or_type",
                expected=model_in_text, actual="",
                message=f"Item {idx}: description names model '{model_in_text}' but model_or_type was left empty.",
            ))
        else:
            checks.append(ValidationCheck(
                rule="MODEL_SOURCE_MATCH", status="ERROR", item=idx, field="model_or_type",
                expected=model_in_text, actual=item.get("model_or_type"), retryable=True,
                message=(
                    f"Item {idx}: description says model '{model_in_text}', "
                    f"but extracted model_or_type is '{item.get('model_or_type')}'."
                ),
            ))

    seen_order_values: set[str] = set()
    for idx, item in enumerate(items, start=1):
        extracted_order = str(item.get("order_number") or "")
        norm_order = _normalize_token(extracted_order)
        if not norm_order or norm_order in seen_order_values:
            continue
        seen_order_values.add(norm_order)

        orders_in_text = _find_all_labeled_values(_ORDER_NO_PATTERNS, invoice_text)
        if not orders_in_text:
            checks.append(ValidationCheck(
                rule="ORDER_NUMBER_SOURCE_MATCH", status="NOT_CHECKED", item=idx,
                message="No 'P/O:'/'Order No:' label found in the source text to check against.",
            ))
        elif any(
            (n := _normalize_token(o)) in norm_order or norm_order in n for o in orders_in_text
        ):
            checks.append(ValidationCheck(
                rule="ORDER_NUMBER_SOURCE_MATCH", status="PASS", item=idx, field="order_number",
                expected=", ".join(orders_in_text), actual=extracted_order,
            ))
        else:
            shown = ", ".join(f"'{o}'" for o in orders_in_text)
            checks.append(ValidationCheck(
                rule="ORDER_NUMBER_SOURCE_MATCH", status="ERROR", item=idx, field="order_number",
                expected=", ".join(orders_in_text), actual=extracted_order, retryable=True,
                message=(
                    f"Item {idx}: source text shows order number(s) {shown}, "
                    f"but extracted order_number is '{extracted_order}' -- it matches none of them."
                ),
            ))

    return checks


_KNOWN_CURRENCIES = {
    "USD", "EUR", "GBP", "INR", "JPY", "CNY", "AUD", "CAD", "CHF", "SGD",
    "AED", "SAR", "HKD", "KRW", "SEK", "NOK", "DKK", "ZAR", "MXN", "BRL",
}

_DATE_FORMATS = (
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%d %b %Y",
    "%d.%m.%Y", "%Y/%m/%d",
)


def _parse_date_loose(s: str) -> datetime | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


def validate_format_and_types(
    header: dict[str, Any], items: list[dict[str, Any]], shipment_type: str
) -> list[ValidationCheck]:
    checks = []

    qty_col = _item_field(shipment_type, "quantity")
    price_col = _item_field(shipment_type, "unit_price")
    for idx, item in enumerate(items, start=1):
        for concept, col in (("quantity", qty_col), ("unit_price", price_col)):
            if col is None:
                continue
            raw = item.get(col)
            if raw is None or raw == "":
                continue
            val = _to_decimal(raw)
            if val is None:
                checks.append(ValidationCheck(
                    rule="FORMAT_TYPE", status="ERROR", item=idx, field=col,
                    actual=raw, retryable=True,
                    message=f"Item {idx}: {concept} ('{raw}') is not numeric.",
                ))
            elif val < 0:
                checks.append(ValidationCheck(
                    rule="FORMAT_TYPE", status="ERROR", item=idx, field=col,
                    actual=val, retryable=True,
                    message=f"Item {idx}: {concept} ({val}) is negative.",
                ))
            else:
                checks.append(ValidationCheck(rule="FORMAT_TYPE", status="PASS", item=idx, field=col))

    hsn_col = _item_field(shipment_type, "hsn")
    if hsn_col:
        for idx, item in enumerate(items, start=1):
            raw = item.get(hsn_col)
            if raw is None or str(raw).strip() == "":
                continue
            n_digits = len(re.sub(r"\D", "", str(raw)))
            if n_digits in (4, 6, 8):
                checks.append(ValidationCheck(rule="HSN_FORMAT", status="PASS", item=idx, field=hsn_col))
            elif 1 <= n_digits <= 3:
                checks.append(ValidationCheck(
                    rule="HSN_FORMAT", status="ERROR", item=idx, field=hsn_col, actual=raw, retryable=True,
                    message=(
                        f"Item {idx}: {hsn_col} '{raw}' has only {n_digits} digit(s), so it is not an HSN / "
                        f"tariff code (4, 6 or 8 digits) -- it looks like an item or line number. Read the "
                        f"'HSN Code' column instead, or return null if no HSN is printed for this row."
                    ),
                ))
            else:
                checks.append(ValidationCheck(
                    rule="HSN_FORMAT", status="WARNING", item=idx, field=hsn_col, actual=raw,
                    message=f"Item {idx}: {hsn_col} '{raw}' has {n_digits} digit(s); an HSN is normally 4, 6 or 8.",
                ))

    total_raw = header.get("invoice_total")
    if total_raw not in (None, ""):
        total_val = _parse_header_total(header)
        if total_val is None:
            checks.append(ValidationCheck(
                rule="FORMAT_TYPE", status="ERROR", field="invoice_total", actual=total_raw,
                retryable=True, message=f"invoice_total ('{total_raw}') is not numeric.",
            ))
        elif total_val < 0:
            checks.append(ValidationCheck(
                rule="FORMAT_TYPE", status="ERROR", field="invoice_total", actual=total_val,
                retryable=True, message=f"invoice_total ({total_val}) is negative.",
            ))
        else:
            checks.append(ValidationCheck(rule="FORMAT_TYPE", status="PASS", field="invoice_total"))

    if not str(header.get("invoice_number") or "").strip():
        checks.append(ValidationCheck(
            rule="REQUIRED_FIELD_MISSING", status="ERROR", field="invoice_number", retryable=True,
            message="invoice_number is empty.",
        ))
    else:
        checks.append(ValidationCheck(rule="REQUIRED_FIELD_MISSING", status="PASS", field="invoice_number"))

    date_raw = str(header.get("invoice_date") or "").strip()
    if date_raw:
        if _parse_date_loose(date_raw) is None:
            checks.append(ValidationCheck(
                rule="FORMAT_TYPE", status="WARNING", field="invoice_date", actual=date_raw,
                message=f"invoice_date '{date_raw}' doesn't match any recognized date format.",
            ))
        else:
            checks.append(ValidationCheck(rule="FORMAT_TYPE", status="PASS", field="invoice_date"))
    else:
        checks.append(ValidationCheck(
            rule="FORMAT_TYPE", status="NOT_CHECKED", field="invoice_date",
            message="invoice_date is empty.",
        ))

    currency = str(header.get("currency") or "").strip().upper()
    if currency:
        if currency in _KNOWN_CURRENCIES:
            checks.append(ValidationCheck(rule="FORMAT_TYPE", status="PASS", field="currency"))
        elif re.match(r"^[A-Z]{3}$", currency):
            checks.append(ValidationCheck(
                rule="FORMAT_TYPE", status="WARNING", field="currency", actual=currency,
                message=f"currency '{currency}' looks like a 3-letter code but isn't in the common-currency list.",
            ))
        else:
            checks.append(ValidationCheck(
                rule="FORMAT_TYPE", status="ERROR", field="currency", actual=currency, retryable=True,
                message=f"currency '{currency}' doesn't look like a valid ISO currency code.",
            ))

    gstin = str(header.get("supplier_tax_id") or "").strip()
    if gstin and not is_export_template(shipment_type):
        checks.append(ValidationCheck(
            rule="FORMAT_TYPE", status="NOT_CHECKED", field="supplier_tax_id",
            message="supplier_tax_id on an import invoice is the foreign supplier's own "
                    "tax ID (VAT/EIN/...), not an Indian GSTIN -- shape not checked.",
        ))
    elif gstin:
        if _GSTIN_RE.match(gstin):
            checks.append(ValidationCheck(rule="FORMAT_TYPE", status="PASS", field="supplier_tax_id"))
        else:
            checks.append(ValidationCheck(
                rule="FORMAT_TYPE", status="WARNING", field="supplier_tax_id", actual=gstin,
                message=f"supplier_tax_id '{gstin}' doesn't match the standard 15-character GSTIN shape.",
            ))

    ad_code = str(header.get("ad_code") or "").strip()
    if ad_code and not _is_valid_ad_code(ad_code):
        checks.append(ValidationCheck(
            rule="FORMAT_TYPE", status="WARNING", field="ad_code", actual=ad_code,
            message=f"ad_code '{ad_code}' doesn't match the expected 14-digit AD Code shape.",
        ))
    elif ad_code:
        checks.append(ValidationCheck(rule="FORMAT_TYPE", status="PASS", field="ad_code"))

    iec = str(header.get("iec_number") or "").strip()
    if iec and not re.match(r"^[A-Z0-9]{10}$", iec.upper()):
        checks.append(ValidationCheck(
            rule="FORMAT_TYPE", status="WARNING", field="iec_number", actual=iec,
            message=f"iec_number '{iec}' isn't the usual 10-character IEC shape.",
        ))
    elif iec:
        checks.append(ValidationCheck(rule="FORMAT_TYPE", status="PASS", field="iec_number"))

    return checks


def validate_invoice(
    result: dict[str, Any],
    invoice_text: str,
    shipment_type: str,
    tolerance: Decimal = AMOUNT_TOLERANCE,
) -> ValidationResult:
    from invoice_extractor.schema.templates_config import ITEM_SHEET_NAME

    header = result.get("header", {}) or {}
    item_sheet = ITEM_SHEET_NAME.get(shipment_type)
    items = (result.get("sheets", {}) or {}).get(item_sheet, []) or []

    checks: list[ValidationCheck] = []
    checks += validate_required_fields(items, shipment_type)
    checks += validate_line_items(items, shipment_type, tolerance)
    checks += validate_totals(header, items, shipment_type, tolerance, invoice_text=invoice_text)
    checks += validate_taxes(items, shipment_type, tolerance)
    checks += validate_cross_fields(header, items, shipment_type)
    checks += validate_source_consistency(items, shipment_type, invoice_text)
    checks += validate_format_and_types(header, items, shipment_type)

    for c in checks:
        if c.status == "ERROR" and c.rule in RETRYABLE_RULES:
            c.retryable = True

    has_error = any(c.status == "ERROR" for c in checks)
    return ValidationResult(valid=not has_error, status="INVALID" if has_error else "VALID", checks=checks)


_HEADER_RULES = {"HEADER_LINE_TOTAL", "MULTI_INVOICE_TOTAL_SPLIT"}
_ITEM_RULES = {
    "QTY_X_UNIT_PRICE", "TAX_RECONCILIATION", "PART_NUMBER_SOURCE_MATCH",
    "MODEL_SOURCE_MATCH", "ORDER_NUMBER_SOURCE_MATCH", "QTY_VS_TARIFF_QTY",
    "UNIT_VS_TARIFF_UNIT", "LINE_ITEMS_INCOMPLETE", "HSN_FORMAT",
}


def _format_check_for_feedback(c: ValidationCheck) -> str:
    loc = f"ITEM {c.item}" if c.item is not None else "HEADER"
    parts = [f"{loc}: [{c.rule}] {c.message}"]
    if c.expected is not None or c.actual is not None:
        parts.append(f"    expected: {c.expected!s}   extracted: {c.actual!s}")
    return "\n".join(parts)


def build_retry_feedback(
    validation: ValidationResult,
) -> tuple[str | None, str | None]:
    errors = validation.retryable_errors
    if not errors:
        return None, None

    header_errors = [c for c in errors if c.rule in _HEADER_RULES or (c.item is None and c.rule not in _ITEM_RULES)]
    item_errors = [c for c in errors if c.rule in _ITEM_RULES or (c.item is not None and c.rule not in _HEADER_RULES)]

    def _build(errs: list[ValidationCheck]) -> str | None:
        if not errs:
            return None
        lines = [
            "VALIDATION FAILED", "",
            f"{len(errs)} issue(s) found in your previous extraction:",
        ]
        for i, c in enumerate(errs, start=1):
            lines.append(f"{i}. {_format_check_for_feedback(c)}")
        lines += [
            "",
            "Re-check the original invoice text above and correct ONLY the fields "
            "named in these issues. Do not invent missing information. Do not modify "
            "any other field that was not flagged. Return the complete corrected JSON "
            "in the same format as before.",
        ]
        return "\n".join(lines)

    return _build(header_errors), _build(item_errors)


def build_header_failed_part(validation: ValidationResult) -> dict[str, Any] | None:
    header_errors = [c for c in validation.retryable_errors if c.item is None]
    if not header_errors:
        return None

    def _plain(v: Any) -> Any:
        return str(v) if isinstance(v, Decimal) else v

    failed: dict[str, Any] = {}
    for c in header_errors:
        key = c.field or c.rule
        failed[key] = {
            "rule": c.rule, "message": c.message,
            "expected": _plain(c.expected), "actual": _plain(c.actual),
        }
    return failed
