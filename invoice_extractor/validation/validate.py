from __future__ import annotations
import re
from typing import Any

from invoice_extractor.schema.models import build_sheet_models, validate_row_against_model
from invoice_extractor.schema.template_item_fields import GENERIC_KEY_TO_CONCEPT, item_fields_for_template
from invoice_extractor.schema.templates_config import ITEM_SHEET_NAME


def _unwrap_value(raw_val: Any) -> Any:
    if isinstance(raw_val, dict) and "value" in raw_val:
        return raw_val["value"]
    return raw_val


def _coerce_number(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        cleaned = value.strip()
        for sym in ("$", "€", "£", "₹", ","):
            cleaned = cleaned.replace(sym, "")
        cleaned = cleaned.strip()
        if not cleaned:
            return None
        try:
            if "." in cleaned:
                return float(cleaned)
            return int(cleaned)
        except ValueError:
            return None
    return None


_GSTIN_RE = re.compile(r'^\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[A-Z0-9]$')

_AD_CODE_SHAPE_RE = re.compile(r'^\d+-?\d+$')


def _repair_gstin(val: str, warnings: list[str] | None = None) -> str:
    v = str(val).strip().upper().replace(" ", "")
    if not v:
        return v
    if _GSTIN_RE.match(v):
        return v
    if len(v) == 16 and _GSTIN_RE.match(v[:-1]):
        return v[:-1]
    if warnings is not None:
        warnings.append(f"supplier_tax_id '{v}' doesn't match the standard 15-character GSTIN shape")
    return v


def _is_valid_ad_code(v: str) -> bool:
    return bool(_AD_CODE_SHAPE_RE.match(v)) and len(v.replace('-', '')) == 14


def _repair_ad_code(val: str) -> str:
    v = str(val).strip()
    if _is_valid_ad_code(v):
        return v
    deduped = v[::2]
    if _is_valid_ad_code(deduped):
        return deduped
    return v


_CURRENCY_ALIASES = {
    "EURO": "EUR", "EUROS": "EUR", "€": "EUR",
    "US$": "USD", "USD$": "USD", "US DOLLAR": "USD", "US DOLLARS": "USD",
    "UNITED STATES DOLLAR": "USD", "UNITED STATES DOLLARS": "USD",
    "RS": "INR", "RUPEE": "INR", "RUPEES": "INR",
    "INDIAN RUPEE": "INR", "INDIAN RUPEES": "INR", "₹": "INR",
    "£": "GBP", "POUND STERLING": "GBP", "POUNDS STERLING": "GBP",
    "YEN": "JPY", "RMB": "CNY", "RENMINBI": "CNY", "YUAN": "CNY",
}


def _normalize_currency(val: Any) -> str:
    s = str(val).strip()
    key = re.sub(r"\s+", " ", s.replace(".", "")).upper()
    if key in _CURRENCY_ALIASES:
        return _CURRENCY_ALIASES[key]
    if re.fullmatch(r"[A-Za-z]{3}", s):
        return s.upper()
    return s


_HEADER_FIELDS = [
    "invoice_number", "invoice_date", "supplier_name", "supplier_address",
    "supplier_tax_id", "buyer_name", "buyer_address", "consignee_name",
    "consignee_address", "currency", "invoice_total", "net_realisable_amount",
    "tax_amount", "tax_rate", "payment_terms", "country_of_origin",
    "country_of_destination", "transit_country", "port_of_loading",
    "port_of_discharge", "export_type_status", "reward_scheme_claim",
    "state_code", "district_code", "ad_code", "iec_number",
    "total_packages", "gross_weight", "net_weight",
]


def _clean_invoice_header(
    header_raw: dict[str, Any], warnings: list[str], shipment_type: str = "export"
) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    is_export = shipment_type == "export"

    for k in _HEADER_FIELDS:
        val = _unwrap_value(header_raw.get(k, ""))
        if val is None:
            val = ""

        if k == "supplier_tax_id" and val:
            if is_export:
                val = _repair_gstin(val, warnings)
        elif k == "ad_code" and val:
            val = _repair_ad_code(val)
        elif k == "state_code":
            sc_str = str(val).strip() if val else ""
            if sc_str:
                num_match = re.search(r'\((\d{1,2})\)', sc_str)
                if num_match:
                    sc_str = num_match.group(1)
                elif re.match(r'^\d{1,2}$', sc_str):
                    pass
                elif re.search(r'\b(\d{1,2})\b', sc_str):
                    sc_str = re.search(r'\b(\d{1,2})\b', sc_str).group(1)
            val = sc_str
            gstin_val = str(clean.get("supplier_tax_id") or "") if is_export else ""
            if _GSTIN_RE.match(gstin_val):
                gstin_state = gstin_val[:2]
                if not val or val.strip().zfill(2) != gstin_state:
                    if val and val.strip().zfill(2) != gstin_state:
                        warnings.append(
                            f"state_code '{val}' didn't match supplier GSTIN's state "
                            f"prefix '{gstin_state}' -- used the GSTIN's instead"
                        )
                    val = gstin_state
        elif k == "currency" and val:
            val = _normalize_currency(val)
        elif k in ("country_of_origin", "country_of_destination") and val:
            val_str = str(val).strip().upper()
            if val_str in ("INDIA", "IND"):
                val = "IN"
            elif val_str in ("USA", "UNITED STATES", "UNITED STATES OF AMERICA"):
                val = "USA"
        elif k in ("invoice_total", "net_realisable_amount", "tax_amount", "total_packages") and val != "":
            num = _coerce_number(val)
            val = num if num is not None else str(val).strip()
        elif k == "reward_scheme_claim":
            val_str = str(val).strip().upper()
            val = "Y" if any(x in val_str for x in ("Y", "YES", "TRUE", "RODTEP", "MEIS")) else "N"

        clean[k] = val

    return clean


_SUBTOTAL_KEYWORDS = {"TOTAL", "SUBTOTAL", "LESS", "NET REALISABLE", "FREE OF COST", "RAW MATERIAL"}


def _clean_generic_item(
    raw_item: dict[str, Any],
    warnings: list[str],
    row_idx: int,
    ser_no: int,
) -> dict[str, Any] | None:
    if not isinstance(raw_item, dict):
        return None

    item_vals: dict[str, Any] = {}
    for k, v in raw_item.items():
        val = _unwrap_value(v)
        if val is not None:
            item_vals[k] = val

    desc = str(item_vals.get("product_description", "")).upper()
    if any(kw in desc for kw in _SUBTOTAL_KEYWORDS) and "quantity" not in item_vals:
        warnings.append(f"[line_items][row {row_idx+1}] discarded summary/deduction line '{desc}'")
        return None

    qty = _coerce_number(item_vals.get("quantity"))
    price = _coerce_number(item_vals.get("unit_price"))
    total = _coerce_number(item_vals.get("line_total"))
    packages = _coerce_number(item_vals.get("packages"))

    if qty is None and price is None and total is None and not item_vals.get("_extra_template_fields"):
        warnings.append(
            f"[line_items][row {row_idx+1}] discarded a row with no quantity, unit price or amount "
            f"('{str(item_vals.get('product_description', ''))[:40]}')"
        )
        return None

    if isinstance(qty, (int, float)) and isinstance(price, (int, float)) and qty > 0 and price > 0:
        calc_total = round(qty * price, 2)
        if isinstance(total, (int, float)) and total > 0:
            diff = abs(calc_total - total)
            if diff > max(1.0, total * 0.02):
                warnings.append(
                    f"[line_items][row {ser_no}] math discrepancy: "
                    f"Qty({qty}) * Price({price}) = {calc_total} != Line Total({total})"
                )
        elif isinstance(total, (int, float)) and total == 0:
            warnings.append(
                f"[line_items][row {ser_no}] line total printed as 0 although "
                f"Qty({qty}) * Price({price}) = {calc_total} -- kept as 0 (free of cost?)"
            )

    fta = item_vals.get("fta_code")
    end_use = item_vals.get("end_use_code")
    if fta:
        fta_str = str(fta).strip()
        if "AD CODE" in fta_str.upper() or re.match(r'^\d{6,8}[-\s]\d{6,8}$', fta_str):
            fta = None
        elif fta_str.startswith("GNX") or fta_str.startswith("EU"):
            end_use = end_use or fta_str
            fta = None

    part_no = str(
        item_vals.get("part_number")
        or item_vals.get("part_no")
        or item_vals.get("item_code")
        or item_vals.get("drawing_no")
        or item_vals.get("catalog_no")
        or ""
    ).strip()

    model_type = str(
        item_vals.get("model_or_type")
        or item_vals.get("type")
        or item_vals.get("model")
        or item_vals.get("type_no")
        or item_vals.get("model_no")
        or ""
    ).strip()

    raw_desc = str(item_vals.get("product_description", "")).strip()
    if len(model_type) > 40:
        model_type = ""
    elif model_type.lower() == raw_desc.lower() or (len(raw_desc) > 8 and model_type.lower() in raw_desc.lower()):
        model_type = ""

    ohms_val = item_vals.get("ohms") or item_vals.get("rating") or item_vals.get("spec")
    if ohms_val and str(ohms_val) not in str(model_type):
        model_type = f"{model_type} ({ohms_val} OHMS)" if model_type else f"{ohms_val} OHMS"

    extracted_sno = item_vals.get("item_ser_no")
    actual_sno = extracted_sno if extracted_sno not in (None, "") else ser_no

    return {
        "item_ser_no": actual_sno,
        "product_description": raw_desc,
        "part_number": part_no,
        "model_or_type": model_type,
        "order_number": str(item_vals.get("order_number") or "").strip(),
        "hsn_code": item_vals.get("hsn_code", ""),
        "quantity": qty if qty is not None else "",
        "unit_of_measurement": item_vals.get("unit_of_measurement", ""),
        "unit_price": price if price is not None else "",
        "line_total": total if total is not None else "",
        "end_use_code": end_use or "",
        "fta_code": fta or "",
        "country_of_origin": item_vals.get("country_of_origin", ""),
        "igst_amount": item_vals.get("igst_amount", ""),
        "igst_rate": item_vals.get("igst_rate", ""),
        "packages": packages if packages is not None else "",
        "_extra_template_fields": item_vals.get("_extra_template_fields") or {},
        "invoice_ref": item_vals.get("invoice_ref") or "",
    }


def _map_export_item(item: dict[str, Any], header: dict[str, Any]) -> dict[str, Any]:
    def _hdr(k: str) -> Any:
        return header.get(k) or ""

    mapped = {
        "Item_Ser_No": item["item_ser_no"],
        "Item_RITC": item["hsn_code"],
        "Item_Desc": item["product_description"],
        "Item_Unit1": item["unit_of_measurement"],
        "Item_Qty": item["quantity"],
        "Item_Unit_Price": item["unit_price"],
        "Item_Reward": _hdr("reward_scheme_claim"),
        "Itm_Source_Cntry": item["country_of_origin"] or _hdr("country_of_origin"),
        "Itm_Transit_Cntry": _hdr("transit_country"),
        "ItmTotpkg": item["packages"] if item["packages"] != "" else _hdr("total_packages"),
        "ItmIGSTstatus": _hdr("export_type_status"),
        "ItmTaxableVal": item["line_total"],
        "ItmIGSTamt": item["igst_amount"],
        "Item_End_Use": item["end_use_code"],
        "ItmIGSTPer": item["igst_rate"] or _hdr("tax_rate"),
        "State Code": _hdr("state_code"),
        "District Code": _hdr("district_code"),
        "FTA Code": item["fta_code"],
        "Qty Tariff": item["quantity"],
        "Unit Tariff": item["unit_of_measurement"],
    }
    for k, v in (item.get("_extra_template_fields") or {}).items():
        if mapped.get(k) in (None, ""):
            mapped[k] = v
    return mapped


def _map_import_item(item: dict[str, Any], header: dict[str, Any]) -> dict[str, Any]:
    def _hdr(k: str) -> Any:
        return header.get(k) or ""

    country = item["country_of_origin"] or _hdr("country_of_origin")

    mapped = {
        "SL_No": item["item_ser_no"],
        "Item_RITC": item["hsn_code"],
        "Item_Desc1": item["product_description"],
        "Item_End_Use": item["end_use_code"],
        "Item_Country_Org": country,
        "Item_Qty": item["quantity"],
        "Item_Unit": item["unit_of_measurement"],
        "Item_Unit_Price": item["unit_price"],
        "Item_Taxable_Val": item["line_total"],
        "Qty_Tariff": item["quantity"],
        "Unit_Tariff": item["unit_of_measurement"],
        "Model": item["model_or_type"],
        "COO Country": country,
        "Transit Country": _hdr("transit_country"),
    }
    for k, v in (item.get("_extra_template_fields") or {}).items():
        if mapped.get(k) in (None, ""):
            mapped[k] = v
    return mapped


def _map_generic_item(item: dict[str, Any], header: dict[str, Any]) -> dict[str, Any]:
    return dict(item.get("_extra_template_fields") or {})


def _map_template_file_item(
    item: dict[str, Any], header: dict[str, Any], fields: dict[str, str | None],
) -> dict[str, Any]:
    row = _map_generic_item(item, header)
    for generic_key, concept in GENERIC_KEY_TO_CONCEPT.items():
        col = fields.get(concept)
        val = item.get(generic_key)
        if col and val not in (None, ""):
            row[col] = val
    return row


def _apply_generic_domain_rules(row: dict[str, Any], warnings: list[str], row_idx: int) -> None:
    return None


_MAPPERS = {"export": _map_export_item, "import": _map_import_item}
_DEFAULT_MAPPER = _map_generic_item


_CONTAINER_PATTERN = re.compile(r'^[A-Z]{3}[UJZ]\d{6,7}$')

_HSN_DESC_TAIL = re.compile(r'\s+\d{4,8}[A-Z]\s+[A-Z].*$', re.IGNORECASE)

_NA_LIKE = ("NA", "N/A", "-", "NIL", "NONE", "")


def _trim_hsn_desc_tail(row: dict[str, Any], desc_field: str, sheet_name: str, warnings: list[str], row_idx: int) -> None:
    desc = row.get(desc_field)
    if desc is None:
        return
    desc_str = str(desc)
    match = _HSN_DESC_TAIL.search(desc_str)
    if match:
        clean = desc_str[:match.start()].strip()
        if clean:
            row[desc_field] = clean
            warnings.append(
                f"[{sheet_name}][row {row_idx}] trimmed HSN tariff description from {desc_field}"
            )


def _apply_export_domain_rules(row: dict[str, Any], warnings: list[str], row_idx: int) -> None:
    sheet_name = "ITEM"

    hawb = row.get("ItmHawb")
    if hawb is not None:
        hawb_str = str(hawb).strip().upper()
        if _CONTAINER_PATTERN.match(hawb_str):
            warnings.append(
                f"[{sheet_name}][row {row_idx}] removed container number "
                f"'{hawb_str}' from ItmHawb (not an airway bill)"
            )
            row["ItmHawb"] = ""
        elif hawb_str in _NA_LIKE:
            row["ItmHawb"] = ""

    _trim_hsn_desc_tail(row, "Item_Desc", sheet_name, warnings, row_idx)

    fta = row.get("FTA Code")
    if fta:
        fta_str = str(fta).strip()
        if fta_str.startswith("GNX") or fta_str.startswith("EU"):
            if not row.get("Item_End_Use"):
                row["Item_End_Use"] = fta_str
                warnings.append(
                    f"[{sheet_name}][row {row_idx}] moved EU Code '{fta_str}' from FTA Code to Item_End_Use"
                )
            row["FTA Code"] = ""
        elif "AD CODE" in fta_str.upper() or re.match(r'^\d{6,8}[-\s]\d{6,8}$', fta_str) or re.match(r'^\d{7,}$', fta_str):
            warnings.append(
                f"[{sheet_name}][row {row_idx}] removed AD Code '{fta_str}' from FTA Code "
                f"(AD Code is a bank code, not an FTA Code)"
            )
            row["FTA Code"] = ""

    for field in ("Itm_Transit_Cntry", "Itm_Source_Cntry"):
        val = row.get(field)
        if val is not None and str(val).strip().upper() in _NA_LIKE:
            row[field] = ""

    for field in ("item_Cess", "item_accessory", "item_thirdparty", "item_Quota",
                  "item_AR4", "Item_Reward", "Item_STR", "Item_JNoti_No"):
        if row.get(field):
            val_str = str(row[field]).strip().upper()
            if field == "Item_Reward" and any(k in val_str for k in ("RODTEP", "MEIS", "REWARD", "CLAIM", "SCHEME")):
                row[field] = "Y"
            elif val_str in ("TRUE", "YES", "1", "Y"):
                row[field] = "Y"
            elif val_str in ("FALSE", "NO", "0", "0.0", "N", *_NA_LIKE):
                row[field] = ""
            else:
                row[field] = "Y" if val_str.startswith("Y") else ""

    for cess_field in ("CESS Per", "Cess Rate", "Cess Amount"):
        if row.get(cess_field) in (0, 0.0, "0", "0.0"):
            row[cess_field] = ""

    state_code = row.get("State Code")
    if state_code:
        sc_str = str(state_code).strip()
        num_match = re.search(r'\((\d{1,2})\)', sc_str)
        if num_match:
            row["State Code"] = num_match.group(1)
        elif re.search(r'\b(\d{1,2})\b', sc_str):
            row["State Code"] = re.search(r'\b(\d{1,2})\b', sc_str).group(1)


def _apply_import_domain_rules(row: dict[str, Any], warnings: list[str], row_idx: int) -> None:
    sheet_name = "BOE"

    _trim_hsn_desc_tail(row, "Item_Desc1", sheet_name, warnings, row_idx)

    for field in ("Item_Country_Org", "COO Country", "Transit Country"):
        val = row.get(field)
        if val is not None and str(val).strip().upper() in _NA_LIKE:
            row[field] = ""


_DOMAIN_RULES = {"export": _apply_export_domain_rules, "import": _apply_import_domain_rules}
_DEFAULT_DOMAIN_RULES = _apply_generic_domain_rules


def _drop_packing_list_duplicates(
    items: list[dict[str, Any]], warnings: list[str]
) -> list[dict[str, Any]]:
    priced = [it for it in items if it.get("unit_price") not in (None, "")]
    kept: list[dict[str, Any]] = []
    for it in items:
        if it.get("unit_price") in (None, "") and _is_repeat_of_priced_row(it, priced):
            warnings.append(
                f"[line_items] dropped priceless duplicate of '{it.get('part_number') or it.get('product_description')}' "
                f"(qty {it.get('quantity')}) -- looks like a packing-list repeat, not a new item"
            )
            continue
        kept.append(it)
    return kept


def _norm_desc(s: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", str(s or "").upper())).strip()


def _desc_similarity(a: Any, b: Any) -> float:
    from difflib import SequenceMatcher
    na, nb = _norm_desc(a), _norm_desc(b)
    if not na or not nb:
        return 0.0
    whole = SequenceMatcher(None, na, nb).ratio()
    short, long_ = sorted((na.split(), nb.split()), key=len)
    matched = sum(
        1 for t in short
        if any(t == w or SequenceMatcher(None, t, w).ratio() >= 0.8 for w in long_)
    )
    overlap = matched / len(short) if matched >= 2 else 0.0
    return max(whole, overlap)


_REPEAT_DESC_SIMILARITY = 0.85


def _is_repeat_of_priced_row(it: dict[str, Any], priced: list[dict[str, Any]]) -> bool:
    for p in priced:
        if p is it or p.get("quantity") != it.get("quantity") or it.get("quantity") in (None, ""):
            continue
        if it.get("part_number") and it.get("part_number") == p.get("part_number"):
            return True
        if not it.get("part_number") and _desc_similarity(
            it.get("product_description"), p.get("product_description"),
        ) >= _REPEAT_DESC_SIMILARITY:
            return True
    return False


def _shared_long_words(a: Any, b: Any, min_len: int = 5) -> int:
    from difflib import SequenceMatcher
    ta = [w for w in _norm_desc(a).split() if len(w) >= min_len]
    tb = [w for w in _norm_desc(b).split() if len(w) >= min_len]
    if not ta or not tb:
        return 0
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return sum(
        1 for t in short
        if any(t == w or SequenceMatcher(None, t, w).ratio() >= 0.8 for w in long_)
    )


def _drop_duplicates_that_break_total(
    items: list[dict[str, Any]], header: dict[str, Any], warnings: list[str]
) -> list[dict[str, Any]]:
    from itertools import combinations

    total = header.get("invoice_total")
    if isinstance(total, bool) or not isinstance(total, (int, float)) or total <= 0 or len(items) < 2:
        return items
    amounts = [it.get("line_total") for it in items]
    if any(isinstance(a, bool) or not isinstance(a, (int, float)) for a in amounts):
        return items
    line_sum = sum(amounts)
    if abs(line_sum - total) <= 0.05:
        return items

    def _sig(it: dict[str, Any]):
        vals = (it.get("quantity"), it.get("unit_price"), it.get("line_total"))
        return vals if all(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in vals) else None

    kept: list[dict[str, Any]] = []
    candidates: list[int] = []
    for idx, it in enumerate(items):
        sig = _sig(it)
        twin = next((k for k in kept if sig and _sig(k) == sig and (
            _desc_similarity(it.get("product_description"), k.get("product_description")) >= 0.6
            or _shared_long_words(it.get("product_description"), k.get("product_description")) >= 2
        )), None)
        if twin is not None:
            candidates.append(idx)
        else:
            kept.append(it)

    for r in range(1, len(candidates) + 1):
        for combo in combinations(candidates, r):
            if abs(line_sum - sum(amounts[i] for i in combo) - total) <= 0.05:
                warnings.append(
                    f"[line_items] dropped {r} repeated line(s) (rows {', '.join(str(i + 1) for i in combo)}): "
                    f"identical quantity/rate/amount to an earlier line, and line amounts only reconcile with "
                    f"the printed invoice total ({total}) without them -- the same item appearing in more "
                    f"than one document of a bundled PDF"
                )
                return [it for i, it in enumerate(items) if i not in combo]
    return items


def _renumber_items_if_ser_no_resets(items: list[dict[str, Any]], warnings: list[str]) -> None:
    prev = None
    resets = False
    for it in items:
        v = it.get("item_ser_no")
        if v in (None, ""):
            continue
        try:
            v_num = float(v)
        except (TypeError, ValueError):
            continue
        if prev is not None and v_num <= prev:
            resets = True
            break
        prev = v_num

    if not resets:
        return

    warnings.append(
        "[line_items] item_ser_no reset or repeated partway through the merged "
        "list -- renumbered every item 1..N instead of trusting a partial sequence"
    )
    for i, it in enumerate(items, start=1):
        it["item_ser_no"] = i


def map_extra_sheets(
    raw_extra_sheets: dict[str, list[dict[str, Any]]],
    schema: dict[str, dict[str, Any]],
    item_sheet_name: str,
    warnings: list[str],
) -> dict[str, list[dict[str, Any]]]:
    sheet_models = build_sheet_models(schema)
    mapped: dict[str, list[dict[str, Any]]] = {}
    for sheet_name, rows in raw_extra_sheets.items():
        if sheet_name == item_sheet_name or sheet_name not in schema:
            warnings.append(f"extra-sheets pass returned unknown sheet '{sheet_name}' -- dropped")
            continue
        model = sheet_models.get(sheet_name)
        fields = schema[sheet_name]["fields"]
        validated_rows: list[dict[str, Any]] = []
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            validated = (
                validate_row_against_model(row, model, sheet_name, i, warnings, all_fields=fields)
                if model else row
            )
            if validated is not None:
                validated_rows.append(validated)
        if validated_rows:
            mapped[sheet_name] = validated_rows
    return mapped


def validate_and_coerce(
    raw: dict[str, Any],
    schema: dict[str, dict[str, Any]],
    shipment_type: str,
    raw_extra_sheets: dict[str, list[dict[str, Any]]] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], list[str], dict[str, Any]]:
    if shipment_type not in ITEM_SHEET_NAME:
        raise ValueError(
            f"shipment_type {shipment_type!r} is not a known template -- "
            f"available: {', '.join(sorted(ITEM_SHEET_NAME))}"
        )

    warnings: list[str] = []
    raw_header = raw.get("invoice_header", {}) if isinstance(raw, dict) else {}
    raw_items = raw.get("line_items", []) if isinstance(raw, dict) else []

    cleaned_header = _clean_invoice_header(raw_header, warnings, shipment_type)

    generic_items: list[dict[str, Any]] = []
    ser_no = 1
    for idx, raw_item in enumerate(raw_items):
        cleaned = _clean_generic_item(raw_item, warnings, idx, ser_no)
        if cleaned is None:
            continue
        generic_items.append(cleaned)
        ser_no += 1

    generic_items = _drop_packing_list_duplicates(generic_items, warnings)
    generic_items = _drop_duplicates_that_break_total(generic_items, cleaned_header, warnings)
    _renumber_items_if_ser_no_resets(generic_items, warnings)

    item_sheet_name = ITEM_SHEET_NAME[shipment_type]
    mapper = _MAPPERS.get(shipment_type)
    if mapper is None:
        template_fields = item_fields_for_template(shipment_type)
        mapper = (
            (lambda it, hdr: _map_template_file_item(it, hdr, template_fields))
            if template_fields else _DEFAULT_MAPPER
        )
    domain_rules = _DOMAIN_RULES.get(shipment_type, _DEFAULT_DOMAIN_RULES)

    sheets: dict[str, list[dict[str, Any]]] = {name: [] for name in schema}

    if item_sheet_name not in schema:
        warnings.append(
            f"loaded template has no '{item_sheet_name}' sheet -- line items could not be placed"
        )
    else:
        sheet_models = build_sheet_models(schema)
        item_model = sheet_models.get(item_sheet_name)
        item_fields = schema[item_sheet_name]["fields"]

        mapped_rows: list[dict[str, Any]] = []
        for i, generic in enumerate(generic_items):
            mapped = mapper(generic, cleaned_header)
            domain_rules(mapped, warnings, i)

            if item_model:
                validated = validate_row_against_model(
                    mapped, item_model, item_sheet_name, i, warnings, all_fields=item_fields
                )
            else:
                validated = mapped

            if validated is not None:
                validated["part_number"] = generic["part_number"]
                validated["model_or_type"] = generic["model_or_type"]
                validated["order_number"] = generic["order_number"]
                mapped_rows.append(validated)

        sheets[item_sheet_name] = mapped_rows

    if raw_extra_sheets:
        for sheet_name, rows in map_extra_sheets(raw_extra_sheets, schema, item_sheet_name, warnings).items():
            sheets[sheet_name] = rows

    return sheets, warnings, cleaned_header
