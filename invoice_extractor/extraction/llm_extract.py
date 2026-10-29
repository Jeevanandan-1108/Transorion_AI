from __future__ import annotations
import json
import logging
import os
import re
from pathlib import Path
from typing import Any
from dotenv import load_dotenv
from openai import (
    AsyncOpenAI,
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from invoice_extractor.pipeline_log import print_llm_input_text

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")

logger = logging.getLogger("invoice_extractor")

_REASONING_MODEL_PREFIXES = ("o1", "o3", "o4", "gpt-5")

_llm_retry = retry(
    retry=retry_if_exception_type(
        (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError)
    ),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    stop=stop_after_attempt(4),
    reraise=True,
)


def _is_reasoning_model(model: str) -> bool:
    m = model.lower()
    return any(m.startswith(p) for p in _REASONING_MODEL_PREFIXES)


_MODEL_PRICING: dict[str, dict[str, float]] = {
    "gpt-5-nano": {
        "input":        0.05,
        "cached_input": 0.005,
        "output":       0.40,
    },
    "gpt-5.4-nano": {
        "input":        0.08,
        "cached_input": 0.008,
        "output":       0.50,
    },
    "gpt-5.4-mini": {
        "input":        0.15,
        "cached_input": 0.015,
        "output":       0.60,
    },
    "gpt-6-luna": {
        "input":        0.10,
        "cached_input": 0.010,
        "output":       0.50,
    },
    "gpt-5-mini": {
        "input":        0.25,
        "cached_input": 0.025,
        "output":       2.00,
    },
    "gpt-4.1-nano": {
        "input":        0.10,
        "cached_input": 0.025,
        "output":       0.40,
    },
    "gpt-4o-mini": {
        "input":        0.15,
        "cached_input": 0.075,
        "output":       0.60,
    },
}

_USD_TO_INR = 96.34


def _calculate_cost(
    model: str,
    input_tokens: int,
    cached_tokens: int,
    output_tokens: int,
) -> dict[str, Any]:
    pricing = _MODEL_PRICING.get(model)
    if not pricing:
        pricing = _MODEL_PRICING["gpt-4.1-nano"]

    non_cached_input = max(0, input_tokens - cached_tokens)

    cost_input_usd = (non_cached_input / 1_000_000) * pricing["input"]
    cost_cached_usd = (cached_tokens / 1_000_000) * pricing["cached_input"]
    cost_output_usd = (output_tokens / 1_000_000) * pricing["output"]
    total_cost_usd = cost_input_usd + cost_cached_usd + cost_output_usd
    total_cost_inr = total_cost_usd * _USD_TO_INR

    return {
        "input_tokens": non_cached_input,
        "cached_input_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cost_usd": round(total_cost_usd, 6),
        "cost_inr": round(total_cost_inr, 4),
    }


def _extract_usage(resp) -> dict[str, int]:
    usage = getattr(resp, "usage", None)
    if not usage:
        return {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0}

    input_tokens = getattr(usage, "prompt_tokens", 0) or 0
    output_tokens = getattr(usage, "completion_tokens", 0) or 0

    cached_tokens = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details:
        cached_tokens = getattr(details, "cached_tokens", 0) or 0

    return {
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "output_tokens": output_tokens,
    }


def _extract_json(raw: str) -> dict[str, Any] | list[Any]:
    if not raw or not raw.strip():
        return {}

    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"```\s*$", "", text).strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    try:
        import json_repair
        repaired = json_repair.loads(text)
        if isinstance(repaired, (dict, list)) and repaired:
            return repaired
    except Exception:
        pass

    start_obj = text.find("{")
    end_obj = text.rfind("}")
    start_arr = text.find("[")
    end_arr = text.rfind("]")

    candidate = text
    if start_obj != -1 and end_obj != -1 and end_obj > start_obj:
        if start_arr == -1 or start_obj < start_arr:
            candidate = text[start_obj:end_obj + 1]
    elif start_arr != -1 and end_arr != -1 and end_arr > start_arr:
        candidate = text[start_arr:end_arr + 1]

    try:
        return json.loads(candidate)
    except Exception:
        pass

    try:
        import json_repair
        repaired = json_repair.loads(candidate)
        if isinstance(repaired, (dict, list)) and repaired:
            return repaired
    except Exception:
        pass

    sanitized = re.sub(r':\s*(?:N/A|n/a|NA|na|None|undefined|NaN|-|\?)\b', ': null', candidate)
    sanitized = re.sub(r':\s*,\s*', ': null, ', sanitized)
    sanitized = re.sub(r',\s*([}\]])', r'\1', sanitized)
    sanitized = re.sub(r'\bTrue\b', 'true', sanitized)
    sanitized = re.sub(r'\bFalse\b', 'false', sanitized)

    try:
        return json.loads(sanitized)
    except Exception:
        pass

    import ast
    try:
        pythonic = (
            candidate.replace("null", "None")
            .replace("true", "True")
            .replace("false", "False")
        )
        eval_res = ast.literal_eval(pythonic)
        if isinstance(eval_res, (dict, list)):
            return eval_res
    except Exception:
        pass

    try:
        return json.loads(sanitized)
    except json.JSONDecodeError as err:
        logger.error(f"Failed to parse LLM JSON output. Raw snippet: {raw[:300]!r}")
        raise err


_HEADER_SYSTEM_PROMPT = """\
You are an expert commercial invoice understanding AI.
You receive the complete, layout-preserved 2D spatial text extracted from an invoice / export document.
The text strictly preserves vertical alignment, multi-column separation, horizontal gutters, and word linearity without word collisions.
A "│" character marks a detected column boundary -- text on either side of it belongs to separate fields/columns on that line. Never merge text across a "│" into one value or one sentence.

Extract document-level header and summary metadata into a strict, FLAT JSON object.
Do NOT wrap values in {"value": ..., "confidence": ...}. Return primitive JSON values directly (strings, numbers, null).

Fields to extract:
- invoice_number: Invoice / Bill number ONLY (string or null). Some invoices print the number and date together under one shared column/label like "Invoice No. & Date" (e.g. "KA/2627/I/016909/15-JUN-26"). If so, that trailing date-like token is NOT part of the invoice number -- strip it here and put it in invoice_date instead, so invoice_number for that example is just "KA/2627/I/016909". If the document genuinely states MORE THAN ONE invoice number (e.g. a single combined document covering two paired invoice numbers, such as "KA/2627/I/016909" and "KA/2627/I/016910" both printed in the Invoice No. field), include ALL of them here (each with its own date suffix already stripped) joined by " / " in the order printed -- do NOT pick only one and silently drop the rest. BUT the SAME number printed again on other pages (bank-details sheet, packing list, transporter copy, page header) is ONE invoice -- list it once, even if OCR spelled it slightly differently ("SI3626204603" vs "S13626204603"). Join numbers with " / " only for genuinely DIFFERENT invoices, each with its own items and total.
- invoice_date: Invoice date as printed or in YYYY-MM-DD format (string or null). If multiple invoice numbers were combined into invoice_number above, each with its own date, join their dates here the same way and in the same order, so date N corresponds to invoice number N.
- supplier_name: Exporter / Seller legal company name (string or null)
- supplier_address: Exporter complete multi-line address verbatim (from plot/building number, street, industrial area, city, state, postal PIN code, and country). Do NOT omit address lines or postal codes. (string or null)
- supplier_tax_id: Supplier's tax identification number -- a real Indian GSTIN on an EXPORT invoice (e.g. "27AAACG8030H2ZR"), or the foreign supplier's own tax ID (VAT, EIN, etc.) on an IMPORT invoice (string or null)

*** BUYER vs CONSIGNEE SEPARATION (CRITICAL) ***:
- buyer_name: Buyer / Importer legal company name (under "Buyer", "Buyer (if other than Consignee)", "Bill To", "Sold To") (string or null)
- buyer_address: Address of Buyer ONLY. Capture the entire multi-line address verbatim from building/street to zip/postal code and country. Do NOT truncate. (string or null)
- consignee_name: Consignee / Delivery party legal name (under "Consignee", "Ship To", "Deliver To") (string or null)
- consignee_address: Address of Consignee ONLY. Capture the entire multi-line address verbatim. Do NOT truncate. (string or null)

Notice: Side-by-side columns (e.g. Consignee on the left, Buyer in the middle, Shipping Marks on the right) are cleanly separated by horizontal whitespace. Read each column block independently and DO NOT merge their addresses. Keep reading YOUR column all the way down through every line the address spans, even on a line where the OTHER column's content is clearly unrelated (e.g. a different party's own country name) -- do not stop early just because that line "looks like" it belongs elsewhere. Confirmed real miss: a buyer address's own country line ("United States") sat on the same text row as the ship-to column's country ("France"), separated by a "│" column marker -- every other line of that buyer address was captured correctly, but that one line was skipped, silently dropping the country from the address. Read the full column height, line by line, not just the lines whose other-column content also happens to be blank or related.
IMPORTANT: consignee_name and buyer_name are two DIFFERENT company names printed in two DIFFERENT horizontal positions on the SAME line (e.g. "Consignee: NORTH AMERICAN PRODUCTION" on the left, "Buyer (if other than Consignee)" as a separate label further right, with the actual buyer name appearing on the NEXT line under that right-hand label). NEVER concatenate the buyer's name onto the end of consignee_name (or vice versa) just because they appear on the same or adjacent lines -- each belongs only under its own label, stopping at the start of the next column's horizontal position.

IMPORTANT: supplier_address / buyer_address / consignee_address must contain ONLY physical address lines (building/street/area/city/state/postal code/country) -- NEVER a Tax ID, GSTIN, CIN, Registration Number, or similar identifier, even when it's printed glued to the same line as an address line, or stacked directly below the address in the same column with no visible label separating them. Confirmed real misses: a buyer's own postal code had "TAX ID : 83-2387668" printed right after it on the same line with no separator ("53188-1615  TAX ID : 83-2387668") and that tax ID got wrongly appended into buyer_address; a supplier's address block had "C Ex Reg No:", "GSTIN :", and "CIN:" lines stacked immediately below it in the same left column, and those got wrongly absorbed into supplier_address. Recognize these by their own label/pattern (a "TAX ID"/"GSTIN"/"CIN"/"Reg No" prefix, or a code matching a GSTIN/CIN/registration-number shape) and stop the address there -- route GSTIN/tax-ID specifically to supplier_tax_id if that's what it is, and simply omit the rest (CIN, Reg No) if there is no dedicated field for it, rather than letting it bleed into the address string.

- currency: Invoice Currency code (e.g. "USD", "EUR", "INR", "GBP") (string or null). Take it from the code printed next to the INVOICE's own amounts (e.g. "INR 2,527.70" in the unit price / total columns). The "Currency" box on an air waybill or bill of lading is the transport-charge currency, NOT the invoice currency -- do not use it when the invoice prints its own.
- invoice_total: The actual amount chargeable/payable to the buyer, in the SAME currency you put in `currency` above (numeric or null). ONLY extract a value that is EXPLICITLY PRINTED on the document itself under a label such as "Amount Chargeable", "Grand Total", "Total Value" or "Total Invoice Value". NEVER compute, sum, derive, or otherwise deduce this field from the line items yourself, even as a fallback when no explicit total is printed -- if there is no explicitly labeled total anywhere on the document, leave invoice_total null. A number you calculated is not the same thing as a number the document states, and this field must only ever hold the latter; a separate, deterministic check elsewhere reconciles the printed total against the line items -- that is not your job, and guessing at it yourself only hides a real mismatch instead of surfacing it. CRITICAL: NEVER use a "Total F.O.B Value" / "FOB Value" line for this field. On Indian export invoices that value is routinely printed in INR for customs/RBI declaration purposes even when the invoice's trade currency is USD/EUR/etc -- using it here silently mixes a rupee figure into a field tagged with the wrong currency. If the only total-like number you can find is explicitly labeled "F.O.B Value", leave invoice_total null rather than use it. CRITICAL -- if invoice_number above holds MORE THAN ONE invoice number (a combined document): do NOT sum the invoices' totals into one number. Confirmed real case: two FULLY SEPARATE, independently-numbered invoices (each its own header, its own items, its own explicitly printed "TOTAL INVOICE AMOUNT" line -- 4,416.00 for one, 1,472.00 for the other) were bundled into a single PDF; the correct invoice_total is "4416.00 / 1472.00", NOT "5888" (their sum) -- 5888 is not a real amount printed anywhere and doesn't belong to either invoice. Instead, find EACH sub-invoice's own explicitly printed total (same labels as above: "Amount Chargeable", "Grand Total", "Total Invoice Value", "Total Invoice Amount") and join them with " / " in the SAME order as their invoice numbers in invoice_number, so total N corresponds to invoice number N -- exactly like invoice_date does. If a given sub-invoice has no total of its own explicitly printed, its own position in this " / "-joined value is null, not a computed figure.
- net_realisable_amount: Net Realisable / Net Chargeable Amount after deductions (numeric or null). CRITICAL: Extract ONLY if explicitly labeled as "Net Realisable", "Net Chargeable", or "Realizable Value". NEVER map "Assessable Value", "Taxable Value", or "Total Amount" into this field. If not explicitly labeled as net realisable/chargeable, return null.
- tax_amount: Total Tax / IGST Amount (numeric or null)
- tax_rate: Tax Rate (e.g. "18%") if explicitly stated (string or null)
- payment_terms: Terms of Payment ONLY -- credit/payment condition (e.g. "Net 60 days", "45 DAYS", "IMMEDIATE", "IC60"). There is no separate field for Incoterms/delivery terms, but that does NOT mean they belong here -- NEVER put an Incoterm (e.g. "FCA", "FOB", "CIF", "DAP", "EXW", "EX WORKS-Factory") into payment_terms, even when the invoice prints it right next to or wrapped onto the same value as the real payment term under one shared label like "Terms of Delivery & Payment:". Some invoices print "Incoterms" and "Payment Terms" as two SEPARATE, side-by-side labeled columns (e.g. "Incoterms: DAP" next to "Payment Terms: IC60") -- take only the Payment Terms column there. Others combine both under one label with the value wrapping onto a following line (e.g. "IMMEDIATE" then "EX WORKS-Factory (INCO terms 2000)" on the next line) -- in that case too, take ONLY the payment-term fragment ("IMMEDIATE") for payment_terms and OMIT the Incoterm fragment entirely; there is no field to put it in, so drop it rather than let it dilute payment_terms with a different concept. (string or null)
- country_of_origin: Country of origin of goods (e.g. "INDIA", "IN", "36") (string or null)
- country_of_destination: Country of final destination (e.g. "USA", "GERMANY") (string or null)
- transit_country: Intermediate transit country if specified (string or null)
- port_of_loading: Port of loading / airport (e.g. "NHAVA SHEVA, INDIA", "MUMBAI") (string or null)
- port_of_discharge: Port of discharge / airport (e.g. "HOUSTON, USA", "SANDIEGO") (string or null)
- export_type_status: GST export status: "LUT" (for Bond/LUT exports), "PAID" (if IGST paid), or "NA" (string or null)
- reward_scheme_claim: "Y" if invoice contains RoDTEP / MEIS reward claim statement, otherwise "N"
- state_code: Exporter numeric state code (e.g. "12" from "MH(12)", or "27" from GSTIN "27...") (string or null)
- district_code: District code if printed (string or null)
- ad_code: Authorised Dealer Code (e.g. "0301216-6000009") (string or null)
- iec_number: Importer Exporter Code (IEC) (string or null)
- total_packages: Total number of physical packages -- the innermost physical containers the goods are packed into (cartons, cases, boxes, packages, bags, drums, crates -- whichever word THIS invoice actually uses), a SEPARATE, usually much SMALLER count than the piece/unit quantity, NOT the same number. On these invoices it prints in the item table's own "TOTAL" row, under a "No. of Pkgs" column that sits BEFORE the "Quantity (PCS)" column -- e.g. a row reading "TOTAL   1        336" has 336 as the piece quantity and 1 (the number appearing FIRST, right after "TOTAL") as total_packages; do not take the second, larger number for this field just because it is also on the TOTAL row. Confirmed real misses: this field came back holding the piece quantity itself (e.g. 336 or 1680) when the true package count printed on the same row was a single-digit or low double-digit number (1, 6, 11). If the "No. of Pkgs" column position on the TOTAL row is blank -- nothing printed between "TOTAL" and the quantity figure -- leave this field null; do NOT substitute the quantity or invent a count. Other invoices instead describe packing in a sentence rather than a table row, e.g. "Four Nos Of Pallet Containing 3840 Nos (20 Nos Each in 192 carton boxes)" -- that sentence names THREE different counts (3840 pieces = the quantity, 4 pallets, 192 carton boxes), and total_packages is the carton/case/box/package count (192 here), never the pallet count (a larger consolidation/handling unit, not what this field means) and never the piece quantity. (numeric or null)
- gross_weight: Total gross weight with unit (e.g. "6,375.24 KG", "12.00 Kgs") (string or null)
- net_weight: Total net weight with unit (e.g. "6,240.24 KG", "10.00 Kgs") (string or null)

RULES:
1. Return a FLAT JSON object mapping field names directly to primitive values (strings, numbers, or null).
2. DO NOT wrap fields in {"value": ..., "confidence": ...}.
3. If a field is not present in the document, set value to null or "".
4. NEVER write bare unquoted words like N/A, NA, or None (which are invalid JSON). Use JSON null or "" instead.
5. NEVER guess or calculate unmentioned values.
"""

_LINE_ITEMS_SYSTEM_PROMPT = """\
You are an expert commercial invoice table extractor.
You receive the complete, layout-preserved 2D spatial text extracted from an invoice.
The table rows and columns strictly maintain vertical alignment, linear gutters, and horizontal spacing without word collisions.
A "│" character marks a detected column boundary -- text on either side of it is a separate table cell/column. Never merge text across a "│" into one field (e.g. never combine a description with the next column's code/qty/price just because they're on the same row).

Extract EVERY distinct line item from the invoice table into a JSON object containing a FLAT array of line item objects under the key "line_items".

Return format:
{
  "line_items": [
    {
      "item_ser_no": 1,
      "product_description": "ROTOR SHAFT ASY",
      "part_number": "1004128",
      "model_or_type": "F2",
      "order_number": "PO-84920",
      "hsn_code": "84831099",
      "quantity": 100,
      "unit_of_measurement": "NOS",
      "unit_price": 50.0,
      "line_total": 5000.0,
      "end_use_code": null,
      "fta_code": null,
      "packages": null,
      "country_of_origin": null
    }
  ]
}

CRITICAL RULES FOR LINE ITEMS:
1. Extract EVERY distinct item row individually across all pages. Do NOT miss any rows and NEVER collapse multiple items into one!
2. Return flat primitive values directly (strings, numbers, null). DO NOT wrap values in {"value": ..., "confidence": ...}.
3. item_ser_no: Extract the EXACT Item Serial / Line Number as printed in the invoice table (e.g. 1, 2, 3... or 10, 20, 30... or 0010, 0020...). If no SNo column is printed on the invoice, set to null. Confirmed real case: an invoice's items were grouped under repeating section headers like "PO No 240300" / "PO No 240400", each followed by a handful of item rows with columns Part No / Description / Qty / Uom / Unit / Amount -- there was no serial-number column anywhere. A "PO No" (or similar) heading that groups rows underneath it is NOT an SNo column, and the row's position under that heading is NOT a serial number either -- do NOT invent your own row-count that increments within each group and resets to 1 at the next heading. In that case item_ser_no must be null on every single row; the caller assigns a real, non-resetting sequence number afterward.
4. Do NOT include subtotal, deduction, or total rows (e.g. "TOTAL", "SUBTOTAL", "LESS: RAW MATERIALS", "NET REALISABLE") as line items. Likewise a FREIGHT / SHIPPING / INSURANCE / HANDLING / PACKING charge printed once below the table as "FREIGHT: <amount>" (one label, one amount, no quantity or unit price) is a charge, NOT a line item -- skip it.
5. product_description: The commercial item name (e.g. "ROTOR SHAFT ASY", "TO-5 COILS", "ELECTRONIC COMPONENTS"). Omit generic tariff chapter descriptions. The description column frequently WRAPS across multiple physical rows -- both ABOVE and BELOW the specific row that carries the item's own quantity/rate/amount (per rule 17). Read the description column's text on every row belonging to this item (the row with the quantity/rate/amount, plus any row directly above or below it that has no quantity/rate/amount of its own but has description-column text) and CONCATENATE all of it in reading order into one complete description -- do not take only the fragment that happens to share a row with the quantity/rate/amount. Example: "Discovery" on the row above, "IQ 2Ring-Gen2 India" on the row with the quantity/rate/amount -> full description is "Discovery IQ 2Ring-Gen2 India". Example: "AXIAL HEADHOLDER FOR HSA AND NP" on rows above the quantity/rate/amount row, "TABLES-RoHS" on the row below it -> full description is "AXIAL HEADHOLDER FOR HSA AND NP TABLES-RoHS". IMPORTANT -- this vertical (above/below) concatenation is NEVER a license to concatenate HORIZONTALLY across a "│" column boundary: some invoices print the description TWICE side by side as two SEPARATE, fully-labeled columns in different languages (e.g. "Description Produit" in French immediately followed by "Description of Goods" in English, each its own column, separated by "│"). That is not a wrapped/continued value -- it is the SAME field printed twice for two different readers. In that case use ONLY the English-labeled column ("Description of Goods" or equivalent) for product_description and DISCARD the other language's column entirely; do not merge "COSSE LUG" (French "COSSE" + English "LUG") or "RACCORD COUDE BACKSHELL" (French "RACCORD COUDE" + English "BACKSHELL") into one string -- the correct value in both cases is just the English word ("LUG", "BACKSHELL").
6. part_number: Part number, catalog number, drawing code, or item code (from "PART NO", "ITEM CODE", etc.). Do NOT put order numbers here.
7. model_or_type: Short model designation, type, or physical/electrical specification only (e.g. "F2", "CLASS 150", "TYPE-B", "B270"). DO NOT copy full product description sentences or text into model_or_type. If there is no distinct short model/type designation, set to null. Do NOT put order numbers here.
8. order_number: Buyer Order No, Purchase Order (PO) number, or Order item reference (e.g. from "(ORDER NO)" or "PO #"). There is no separate field for a PO's date, so if a PO is printed together with its own date (e.g. under a shared "Buyer's Order No. & Date" column, as "446297.1 / 20-APR-26"), keep that number and its date together as-is -- do not split them apart. If a SINGLE line item (per rule 17 below) has more than one PO/order reference tied to it -- e.g. it belongs to a combined document covering two paired invoices, each with its own PO -- include ALL of them here, each kept as its own number+date unit, joined by " / " in the order printed (e.g. "446297.1 / 20-APR-26  /  450044.2 / 15-JUN-26" would be wrong and ambiguous -- instead separate the two units clearly, e.g. "446297.1 (20-APR-26) / 450044.2 (15-JUN-26)"). Never flatten multiple PO+date pairs into one list where it's unclear which date belongs to which PO.
9. hsn_code: ONLY the HSN / SAC / tariff-code column: a 4-, 6- or 8-digit code printed like "8431 49 90" or "84314980". Tables often have a separate short "Item No" / "Line No" column (10, 120, 160...) right next to it -- that is NOT the HSN: never use it, and never join it onto the code. Null if no HSN is printed. If the table prints both an exporter-country tariff column ("HTS") and an "INDIA HTS" column, the HSN is the INDIA HTS value (e.g. "8537.10.10", not the longer "8537.10.9199").
10. quantity: Exact quantity for that specific line item (numeric or null). Watch for a "." used as a THOUSANDS separator rather than a decimal point (common on European-format invoices) -- e.g. "2.592" printed in a whole-count unit's (NOS/PCS/EA) quantity column means 2,592, not 2.592. You can tell which reading is right by checking it against the row's own rate and amount: if reading the number as a plain decimal makes `quantity x unit_price` come nowhere close to the printed amount, but reading the "." as a thousands separator makes it match (see rule 12's confirmed example: quantity "2.592", rate "207.00/100 NOS", amount "5,365.44" -- only 2,592 x 2.07 reproduces that amount, not 2.592 x 2.07), use the thousands-separator reading.
11. unit_of_measurement: Unit of measure (e.g. "PC", "PCS", "NOS", "KG", "SET").
12. unit_price: Price / rate per SINGLE unit of `quantity` for that line item (numeric or null). Some invoices instead quote the rate per a BATCH of units, printed like "207.00/100 NOS", "15.50/1000 PCS", or "Rate: 42.00 per 100" -- i.e. the price for N units, not one. When you see a rate printed with its own "/ N <unit>" or "per N" batch size, set unit_price to the EFFECTIVE price per ONE unit -- (the printed rate) divided by N -- NOT the raw printed number, so that `quantity x unit_price` still equals the invoice's own printed amount for that row. Confirmed real case: quantity 2592, rate printed as "207.00/100 NOS", amount printed as "5,365.44" -- the correct unit_price is 207.00 / 100 = 2.07 (since 2592 x 2.07 = 5365.44), NOT 207.00 (2592 x 207.00 would be 536,544.00, which is not what the document says and is 100x too large). This is normalizing which number represents "per one unit," not computing or correcting a figure -- rule 22 below (copy every digit as printed) is about line_total/amount, not about resolving an explicitly stated batch-rate basis into a per-unit price.
13. line_total: Taxable / total amount for that line item (numeric or null).
14. end_use_code: Extract "EU Code" / "End Use Code" ONLY if explicitly printed on the document. If not printed, set to null. NEVER invent codes.
15. fta_code: Free Trade Agreement code ONLY if explicitly claimed. If not claimed, set to null. NEVER put "AD CODE" here.
16. Use JSON null or "" for missing values, NEVER bare unquoted N/A or None.
17. A row starts a NEW line item ONLY if that row has its OWN quantity and amount/rate printed on it. If a later row only adds another reference/invoice/serial number (e.g. in the leftmost marks-and-numbers column) or continues a wrapped value (like an HSN code split across two lines) WITHOUT a quantity and amount of its own, it is a CONTINUATION of the immediately preceding item -- do not create a new line item for it, and do not duplicate the preceding item's quantity/rate/amount onto it. Example: if row 1 shows "INV-A | description | HSN: | 1 | 500.00 | 500.00" and row 2 (directly below, same item block) shows only "INV-B | 84831099" with no quantity/amount, that is ONE line item, not two. This is independent of whether values match -- do NOT merge two rows just because their quantity/rate/amount happen to be equal: if row 1 and row 2 EACH print their own quantity and amount (even identical ones, e.g. two separate parts that are each qty=1 at the same price), they are two distinct line items and must both be kept.
18. Some invoices bundle a PACKING LIST alongside the commercial invoice, on the same or a later page -- a second table repeating the same part number/description with columns like "Net Wt.(Kgs)" / "Gross Wt.(Kgs)" / "Box No." / "Qty per box" / "Sr. No. of Box" instead of a rate and monetary amount. Do NOT treat a row from a packing-list-style table as a new line item, even though it has its own "quantity" and two numbers that LOOK like they could fill unit_price/line_total by position -- a weight in kilograms is not a price. Recognize this by the column headers (Net Wt., Gross Wt., Box No., Pkgs, Qty/per box) rather than by the row's position on the page. If the same part number/description already appears as a line item from the main pricing table, skip its packing-list repetition entirely rather than emitting a second line item with an empty or weight-derived price.
19. This is a SINGLE, non-interactive call -- there is no follow-up turn, no human waiting to say "yes, continue", and no way for you to send a second message. You MUST return the COMPLETE "line_items" array, covering every row on every page, in this ONE response, no matter how many rows there are (tables with 100+ rows are normal for this task). NEVER truncate the array, NEVER summarize or sample only some rows, and NEVER end your response by asking whether to continue, offering to send the rest in batches, or describing what you would do next -- any such text is not valid JSON and breaks the caller, which cannot see or answer it. If the table is very large, work through it efficiently and tersely, but still emit every row's object in the final JSON.
20. packages: THIS ITEM's OWN physical package count (cartons/cases/boxes/pallets/bags/drums -- whichever word this invoice uses), ONLY if a count is printed specifically within this item's own row or block, separately from any other item's count. Confirmed real case: an invoice's two line items each had their own package-count line (e.g. a "Total no. of Cartons" style label) printed right next to that item's own row -- 108 for item 1, 504 for item 2 (packed on different, item-specific pallet ranges) -- and these must stay 108 and 504 on their respective items, NEVER the same number on both, and NEVER their sum (612) on either. Do not confuse this with a single document-wide package count that covers ALL items together (that one is a header-level concept, not this field) -- if the ONLY package count on the invoice is a single whole-shipment figure with no item-specific breakdown, leave this field null on every item rather than guess or copy that one number onto every row. (numeric or null)
21. If the PDF bundles other documents behind the invoice (purchase order, certificates of analysis, a transporter copy of the invoice, terms), take line items ONLY from the invoice's own item table, once; the same line repeated in those documents is not another item. BUT one item table that continues over several pages is ONE table: extract the rows of EVERY page, and do not skip a page because it repeats the invoice header.
22. Copy every digit of quantity, rate and amount as printed; never compute or "correct" a figure; null if not printed.
23. country_of_origin: ONLY a per-row country-of-origin value printed in that row's own column (headed "COO", "Country of Origin" or "Origin"), copied as printed (e.g. "CN", "VN", "MX"). The shipper's / ship-from country is NOT an item's country of origin. Null when the table has no such column.
"""


_GENERIC_TO_REAL_ITEM_FIELDS: dict[str, dict[str, str]] = {
    "export": {
        "item_ser_no": "Item_Ser_No",
        "hsn_code": "Item_RITC",
        "product_description": "Item_Desc",
        "unit_of_measurement": "Item_Unit1",
        "quantity": "Item_Qty",
        "unit_price": "Item_Unit_Price",
        "line_total": "ItmTaxableVal",
        "end_use_code": "Item_End_Use",
        "fta_code": "FTA Code",
        "packages": "ItmTotpkg",
        "country_of_origin": "Itm_Source_Cntry",
    },
    "import": {
        "item_ser_no": "SL_No",
        "hsn_code": "Item_RITC",
        "product_description": "Item_Desc1",
        "unit_of_measurement": "Item_Unit",
        "quantity": "Item_Qty",
        "unit_price": "Item_Unit_Price",
        "line_total": "Item_Taxable_Val",
        "end_use_code": "Item_End_Use",
        "country_of_origin": "Item_Country_Org",
    },
}

def item_core_real_fields(shipment_type: str) -> set[str]:
    return set(_GENERIC_TO_REAL_ITEM_FIELDS.get(shipment_type, {}).values())


_GENERIC_ITEM_CONCEPTS: frozenset[str] = frozenset(
    generic for mapping in _GENERIC_TO_REAL_ITEM_FIELDS.values() for generic in mapping
)


_LINE_ITEM_PASSTHROUGH_FIELDS = ("part_number", "model_or_type", "order_number")

_LINE_ITEM_ROUTING_FIELDS = ("invoice_ref",)


def _build_line_items_prompt(
    schema: dict[str, Any] | None, shipment_type: str | None, item_sheet_name: str | None,
    include_extra_fields: bool = True,
) -> str:
    if not schema or not shipment_type or not item_sheet_name:
        return _LINE_ITEMS_SYSTEM_PROMPT

    mapping = _GENERIC_TO_REAL_ITEM_FIELDS.get(shipment_type, {})
    prompt = _LINE_ITEMS_SYSTEM_PROMPT
    for generic, real in mapping.items():
        prompt = prompt.replace(generic, real)

    if not include_extra_fields:
        return prompt

    item_fields = schema.get(item_sheet_name, {}).get("fields", [])
    aliases = schema.get(item_sheet_name, {}).get("aliases", {})
    covered = set(mapping.values())
    extra_fields = [f for f in item_fields if f not in covered]
    if extra_fields:
        lines = [
            "\n\nADDITIONAL TEMPLATE FIELDS -- this specific template also defines the columns below. "
            "Include them too, using the EXACT names given here as JSON keys directly in each line item "
            "object, ONLY when a value is explicitly printed for that specific row; omit a field "
            "entirely (do not include it as null) when it is not printed. Never guess or invent a value "
            "for any of these:",
        ]
        for field in extra_fields:
            als = aliases.get(field) or []
            if als:
                lines.append(f"- {field} (also printed as: {', '.join(als)})")
            else:
                lines.append(f"- {field}")
        prompt += "\n".join(lines)

    return prompt


def _translate_real_columns_to_generic(
    items: list[dict[str, Any]], shipment_type: str | None,
) -> list[dict[str, Any]]:
    if not shipment_type:
        return [r for r in items if isinstance(r, dict)]
    mapping = _GENERIC_TO_REAL_ITEM_FIELDS.get(shipment_type, {})
    real_to_generic = {real: generic for generic, real in mapping.items()}
    kept_as_is = _LINE_ITEM_PASSTHROUGH_FIELDS + _LINE_ITEM_ROUTING_FIELDS

    translated: list[dict[str, Any]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        out: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        for key, value in row.items():
            if key in real_to_generic:
                out[real_to_generic[key]] = value
            elif key in kept_as_is:
                out[key] = value
            elif key in _GENERIC_ITEM_CONCEPTS:
                out[key] = value
            else:
                extra[key] = value
        if extra:
            out["_extra_template_fields"] = extra
        translated.append(out)
    return translated


def _translate_generic_to_real_for_display(
    items: list[dict[str, Any]], shipment_type: str | None,
) -> list[dict[str, Any]]:
    if not shipment_type:
        return items
    mapping = _GENERIC_TO_REAL_ITEM_FIELDS.get(shipment_type, {})
    out = []
    for row in items:
        if not isinstance(row, dict):
            continue
        extra = row.get("_extra_template_fields") or {}
        translated = {mapping.get(k, k): v for k, v in row.items() if k != "_extra_template_fields"}
        translated.update(extra)
        out.append(translated)
    return out


@_llm_retry
async def _call_header_model(client: AsyncOpenAI, request_kwargs: dict[str, Any]):
    return await client.chat.completions.create(**request_kwargs)


@_llm_retry
async def _call_line_items_model(client: AsyncOpenAI, request_kwargs: dict[str, Any]):
    return await client.chat.completions.create(**request_kwargs)


async def extract_invoice_header(
    invoice_text: str,
    tables_text: str = "",
    model: str = "gpt-4.1-nano",
    api_key: str | None = None,
    feedback: str | None = None,
    previous_result: dict[str, Any] | None = None,
    failed_part: dict[str, Any] | None = None,
    extra_instruction: str | None = None,
    schema: dict[str, Any] | None = None,
    item_sheet_name: str | None = None,
    include_sheets_block: bool = True,
) -> tuple[dict[str, Any], dict[str, int]]:
    client = AsyncOpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))
    system_prompt = (
        _build_header_prompt(schema, item_sheet_name, include_sheets_block=include_sheets_block)
        if schema else _HEADER_SYSTEM_PROMPT
    )

    user_content = "INVOICE TEXT:\n" + invoice_text
    if tables_text:
        user_content = "EXTRACTED TABLES:\n" + tables_text + "\n\n" + user_content
    if feedback or failed_part:
        retry_prompt_text = (
            "\n\n================================================================================\n"
            ">>> RETRY PROMPT: CORRECTION REQUIRED FOR FAILED PARTS <<<\n"
            "================================================================================\n"
        )
        if failed_part:
            retry_prompt_text += (
                "--- FAILED PART (SPECIFIC FIELDS THAT FAILED) ---\n"
                + json.dumps(failed_part, indent=2, ensure_ascii=False)
                + "\n\n"
            )
        retry_prompt_text += (
            "--- YOUR PREVIOUS EXTRACTION (for reference) ---\n"
            + json.dumps(previous_result or {}, ensure_ascii=False)
            + "\n\n--- VALIDATION FEEDBACK ---\n" + (feedback or "Please fix the failing fields.")
            + "\n\n--- RETRY INSTRUCTIONS ---\n"
            "1. Carefully check the source INVOICE TEXT above for the correct values.\n"
            "2. Fix ONLY the failed fields indicated in the FAILED PART / VALIDATION FEEDBACK.\n"
            "3. Do not modify or invent other fields that were not flagged.\n"
            "4. Return the complete corrected JSON following the schema strictly.\n"
            "================================================================================"
        )
        user_content += retry_prompt_text
    if extra_instruction:
        user_content += "\n\n" + extra_instruction

    request_kwargs = dict(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    )
    if not _is_reasoning_model(model):
        request_kwargs["response_format"] = {"type": "json_object"}

    logger.info("Calling OpenAI (Pass 1 - Header): model=%s", model)
    print_llm_input_text("HEADER PASS", user_content)
    resp = await _call_header_model(client, request_kwargs)
    usage = _extract_usage(resp)
    raw = resp.choices[0].message.content
    return _extract_json(raw), usage


async def extract_line_items(
    invoice_text: str,
    tables_text: str = "",
    model: str = "gpt-4.1-nano",
    api_key: str | None = None,
    feedback: str | None = None,
    previous_result: list[dict[str, Any]] | None = None,
    reasoning_effort: str | None = None,
    extra_instruction: str | None = None,
    schema: dict[str, Any] | None = None,
    shipment_type: str | None = None,
    item_sheet_name: str | None = None,
    known_invoice_numbers: list[str] | None = None,
    include_extra_fields: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    client = AsyncOpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))
    system_prompt = _build_line_items_prompt(
        schema, shipment_type, item_sheet_name, include_extra_fields=include_extra_fields,
    )

    user_content = "INVOICE TEXT:\n" + invoice_text
    if tables_text:
        user_content = "EXTRACTED TABLES:\n" + tables_text + "\n\n" + user_content
    if known_invoice_numbers and len(known_invoice_numbers) > 1:
        user_content += (
            "\n\nThis document bundles MULTIPLE separate invoices, numbered: "
            + ", ".join(known_invoice_numbers)
            + ". Add an \"invoice_ref\" field to EVERY line item: whichever of these numbers "
            "that row's own invoice block belongs to (copy it exactly as given above). "
            "Never split one invoice's own items across two different invoice_ref values."
        )
    if feedback:
        display_previous = _translate_generic_to_real_for_display(previous_result or [], shipment_type)
        user_content += (
            "\n\n--- YOUR PREVIOUS EXTRACTION (for reference) ---\n"
            + json.dumps({"line_items": display_previous}, ensure_ascii=False)
            + "\n\n--- VALIDATION FEEDBACK ---\n" + feedback
        )
    if extra_instruction:
        user_content += "\n\n" + extra_instruction

    request_kwargs = dict(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    )
    if not _is_reasoning_model(model):
        request_kwargs["response_format"] = {"type": "json_object"}
    else:
        request_kwargs["reasoning_effort"] = reasoning_effort or "minimal"
        request_kwargs["verbosity"] = "low"

    logger.info("Calling OpenAI (Pass 2 - Line Items): model=%s", model)
    print_llm_input_text("ITEMS PASS", user_content)
    resp = await _call_line_items_model(client, request_kwargs)
    usage = _extract_usage(resp)
    raw = resp.choices[0].message.content
    data = _extract_json(raw)

    items: list[dict[str, Any]] = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for key in ("line_items", "items", "ITEM", "items_list"):
            if key in data and isinstance(data[key], list):
                items = data[key]
                break

    return _translate_real_columns_to_generic(items, shipment_type), usage


def _render_sheets_block(schema: dict[str, Any], item_sheet_name: str) -> str:
    lines = [
        "Most invoices do NOT print customs-filing-stage data (license registrations, AR4/drawback claim numbers, job-work references, scheme codes, ...) at all -- that data is usually added later by whoever files the shipping bill or bill of entry. The fields below are listed in case a FEW of them genuinely ARE printed on this specific document; do NOT invent, guess, or derive any value that isn't explicitly there.",
        "",
        "For each sheet below that has at least one field you can find an explicit value for on THIS document, return an array of row objects under that sheet's own key (exact key name as given below). Every row MUST include its own item-link field (\"Item_No\" or \"Item No\", whichever this sheet lists) holding the SAME item serial number the line-items table uses for the row this data belongs to. Omit a sheet's key entirely if you found nothing for any of its fields on this document -- do not include it as an empty array.",
        "",
        "SHEETS AND THEIR COLUMNS:",
    ]
    for sheet_name, info in schema.items():
        if sheet_name == item_sheet_name:
            continue
        lines.append(f"\n### {sheet_name}")
        for field in info["fields"]:
            aliases = info.get("aliases", {}).get(field) or []
            if aliases:
                lines.append(f"- {field} (also printed as: {', '.join(aliases)})")
            else:
                lines.append(f"- {field}")
    return "\n".join(lines)


def _build_header_prompt(
    schema: dict[str, Any] | None, item_sheet_name: str | None,
    include_sheets_block: bool = True,
) -> str:
    if not schema or not item_sheet_name or not include_sheets_block:
        return _HEADER_SYSTEM_PROMPT
    sheets_block = _render_sheets_block(schema, item_sheet_name)
    if not sheets_block.strip():
        return _HEADER_SYSTEM_PROMPT
    return (
        _HEADER_SYSTEM_PROMPT
        + "\n\n================================================================================\n"
        "ADDITIONALLY, this document may be covered by the India-customs template below -- "
        "extract any of ITS fields too, if explicitly printed:\n"
        "================================================================================\n\n"
        + sheets_block
        + "\n\nReturn these, if anything was found, under an ADDITIONAL top-level \"extra_sheets\" key "
        "in the SAME JSON object as the header fields above, e.g.:\n"
        "{\"invoice_number\": ..., \"supplier_name\": ..., ..., "
        "\"extra_sheets\": {\"<SheetName>\": [ {\"<ColumnName>\": value, ...}, ... ]}}\n"
        "Omit \"extra_sheets\" entirely (or any sheet within it) if nothing was found for it."
    )


_PAGE_MARKER_RE = re.compile(r"^=+\s*PAGE\s+\d+\s+OF\s+\d+", re.MULTILINE)

_LINE_ITEMS_CHUNK_PAGE_THRESHOLD = 3

_LINE_ITEMS_CHUNK_CONCURRENCY = 4

_LINE_ITEMS_PAGES_PER_CHUNK = 2

_LINE_ITEMS_PER_PAGE_THRESHOLD = 5

_FINE_RETRY_REASONING_EFFORT = "medium"


def _split_text_by_page(text: str) -> list[str]:
    starts = [m.start() for m in _PAGE_MARKER_RE.finditer(text)]
    if len(starts) <= 1:
        return [text]
    starts.append(len(text))
    return [text[starts[i]:starts[i + 1]] for i in range(len(starts) - 1)]


def _group_pages_into_chunks(pages: list[str], pages_per_chunk: int) -> list[str]:
    if pages_per_chunk <= 1:
        return pages
    return ["\n\n".join(pages[i:i + pages_per_chunk]) for i in range(0, len(pages), pages_per_chunk)]


_HEADER_QTY_RE = re.compile(r"\b(?:qty|quantity|qnty)\b", re.IGNORECASE)
_HEADER_ITEM_RE = re.compile(r"\b(?:description|hsn|part\s*no|item)\b", re.IGNORECASE)
_DATA_ROW_RE = re.compile(r"^\s*\d+\s*[│|]")
_DATA_LINE_HINT_RE = re.compile(r"\d[\d,]*\.\d{2,}|\b\d{6,}\b|\b\d{1,2}-[A-Za-z]{3}-\d{2,4}\b")

_PACKING_LIST_RE = re.compile(
    r"\bpacking\s*list\b"
    r"|\bnet\s*(?:wt\.?|weight)\b"
    r"|\bgross\s*(?:wt\.?|weight)\b"
    r"|\bbox\s*no\.?\b"
    r"|\bqty\s*per\s*box\b"
    r"|\bpkgs?\.?\b"
    r"|\btotal\s*quantity\b"
    r"|\bno\.?\s*of\s*pallet\b"
    r"|\bdimensions?\b",
    re.IGNORECASE,
)


def _is_packing_list_only_page(page_text: str) -> bool:
    return bool(_PACKING_LIST_RE.search(page_text)) and not _table_header_context(page_text)


def _table_header_context(page_text: str) -> str:
    lines = page_text.splitlines()
    for i, line in enumerate(lines):
        if _HEADER_QTY_RE.search(line) and _HEADER_ITEM_RE.search(line) and not re.match(r"^\s*\d", line):
            block = [line]
            for nxt in lines[i + 1:i + 6]:
                if not nxt.strip():
                    continue
                if _DATA_ROW_RE.match(nxt) or _DATA_LINE_HINT_RE.search(nxt):
                    break
                block.append(nxt)
            return "\n".join(x.rstrip() for x in block)
    return ""


_TRANSPORT_DOC_RE = re.compile(r"\bair\s*waybill\b|\bbill\s+of\s+lading\b", re.IGNORECASE)
_TRANSPORT_FORM_RE = re.compile(
    r"shipper'?s\s+(?:name\s+and\s+address|account\s+number)|consignee'?s\s+(?:name\s+and\s+address|account\s+number)"
    r"|nature\s+and\s+quantity\s+of\s+goods|notify\s+part(?:y|ies)",
    re.IGNORECASE,
)


def _is_transport_document_page(page_text: str) -> bool:
    return bool(_TRANSPORT_DOC_RE.search(page_text)) and len(_TRANSPORT_FORM_RE.findall(page_text)) >= 2


def _drop_transport_document_pages(invoice_text: str) -> str:
    pages = _split_text_by_page(invoice_text)
    if len(pages) < 2:
        return invoice_text
    kept = [pg for pg in pages if not _is_transport_document_page(pg)]
    if not kept or len(kept) == len(pages):
        return invoice_text
    logger.info("Line-items pass: skipping %d air-waybill / bill-of-lading page(s)", len(pages) - len(kept))
    return "".join(kept)


async def extract_line_items_chunked(
    invoice_text: str,
    tables_text: str = "",
    model: str = "gpt-4.1-nano",
    api_key: str | None = None,
    feedback: str | None = None,
    previous_result: list[dict[str, Any]] | None = None,
    fine_chunks: bool = False,
    extra_instruction: str | None = None,
    schema: dict[str, Any] | None = None,
    shipment_type: str | None = None,
    item_sheet_name: str | None = None,
    known_invoice_numbers: list[str] | None = None,
    include_extra_fields: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    invoice_text = _drop_transport_document_pages(invoice_text)
    pages = _split_text_by_page(invoice_text)
    completeness_retry = fine_chunks and len(pages) >= 2
    proactive_fine = len(pages) > _LINE_ITEMS_PER_PAGE_THRESHOLD and len(pages) >= 2
    fine = completeness_retry or proactive_fine
    if not fine and len(pages) <= _LINE_ITEMS_CHUNK_PAGE_THRESHOLD:
        return await extract_line_items(
            invoice_text, tables_text=tables_text, model=model, api_key=api_key,
            feedback=feedback, previous_result=previous_result,
            extra_instruction=extra_instruction,
            schema=schema, shipment_type=shipment_type, item_sheet_name=item_sheet_name,
            known_invoice_numbers=known_invoice_numbers,
            include_extra_fields=include_extra_fields,
        )

    import asyncio
    if fine:
        header_ctx = _table_header_context(pages[0])
        packing_list_warning = (
            "WARNING: if THIS page is a PACKING LIST (columns like \"Net Wt.\", \"Gross Wt.\", "
            "\"Box No.\", \"Qty per box\", \"Pkgs\" instead of a rate and monetary amount), it is "
            "NEVER a new line item, even though it repeats a part number/description and has its "
            "own quantity -- a weight or box count is not a price. A page like this was already "
            "priced on an earlier page; return an EMPTY line_items list for THIS page rather than "
            "inventing a rate or amount for it.\n\n"
        )
        prefix = packing_list_warning + (
            "COLUMN HEADERS OF THE ITEM TABLE (copied from page 1 as context only -- extract rows "
            "ONLY from the page text below this block, never from the headers themselves. The page "
            "below may contain NO item rows at all (e.g. a final totals, terms or packing page) -- "
            "in that case return an empty line_items list):\n"
            + header_ctx + "\n\n"
        ) if header_ctx else packing_list_warning
        groups = [pages[0]]
        for page in pages[1:]:
            if _is_packing_list_only_page(page):
                groups[-1] = groups[-1] + "\n\n" + page
            else:
                groups.append(page)
        chunks = [groups[0]] + [prefix + g for g in groups[1:]]
        chunk_feedback = None if completeness_retry else feedback
        logger.info(
            "Line-items pass (%s): %d pages grouped into %d call(s) "
            "(packing-list-only pages merged with the page before them)",
            "completeness retry" if completeness_retry else "proactive -- exceeds "
            f"{_LINE_ITEMS_PER_PAGE_THRESHOLD}-page threshold",
            len(pages), len(groups),
        )
    else:
        chunks = _group_pages_into_chunks(pages, _LINE_ITEMS_PAGES_PER_CHUNK)
        chunk_feedback = feedback
        logger.info(
            "Line-items pass: %d pages exceeds the %d-page single-call threshold -- "
            "splitting into %d calls of up to %d page(s) each (max %d concurrent)",
            len(pages), _LINE_ITEMS_CHUNK_PAGE_THRESHOLD, len(chunks), _LINE_ITEMS_PAGES_PER_CHUNK,
            _LINE_ITEMS_CHUNK_CONCURRENCY,
        )
    semaphore = asyncio.Semaphore(_LINE_ITEMS_CHUNK_CONCURRENCY)

    chunk_effort = _FINE_RETRY_REASONING_EFFORT if fine else None

    async def _bounded_extract(chunk_text: str):
        async with semaphore:
            return await extract_line_items(
                chunk_text, model=model, api_key=api_key, feedback=chunk_feedback,
                reasoning_effort=chunk_effort, extra_instruction=extra_instruction,
                schema=schema, shipment_type=shipment_type, item_sheet_name=item_sheet_name,
                known_invoice_numbers=known_invoice_numbers,
                include_extra_fields=include_extra_fields,
            )

    page_results = await asyncio.gather(*(_bounded_extract(chunk_text) for chunk_text in chunks))

    items: list[dict[str, Any]] = []
    total_usage = {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0}
    for page_items, usage in page_results:
        items.extend(page_items)
        for k in total_usage:
            total_usage[k] += usage.get(k, 0)

    return items, total_usage


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


_COMBINED_SEP_RE = re.compile(r"\s+/\s*|\s*/\s+")
_OCR_CONFUSABLE = str.maketrans({"I": "1", "L": "1", "O": "0"})


def _split_combined_parts(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    return [p.strip() for p in _COMBINED_SEP_RE.split(str(value)) if p.strip()]


def _ocr_key(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper().translate(_OCR_CONFUSABLE))


def collapse_duplicate_invoice_refs(header_raw: dict[str, Any], invoice_text: str) -> list[str]:
    numbers = _split_combined_parts(header_raw.get("invoice_number"))
    if len(numbers) < 2:
        return []
    keys = [_ocr_key(n) for n in numbers]
    if len(set(keys)) == len(keys):
        return []

    dates = _split_combined_parts(header_raw.get("invoice_date"))
    totals = _split_combined_parts(header_raw.get("invoice_total"))
    dates_aligned = len(dates) == len(numbers)
    totals_aligned = len(totals) == len(numbers)

    groups: dict[str, list[int]] = {}
    for i, k in enumerate(keys):
        groups.setdefault(k, []).append(i)

    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        if dates_aligned and len({dates[i] for i in idxs}) > 1:
            return []
        if totals_aligned:
            vals = {_to_float(totals[i]) for i in idxs}
            if len(vals) > 1 or None in vals:
                return []

    def _best_spelling(idxs: list[int]) -> int:
        return max(
            idxs,
            key=lambda i: (
                sum(c.isalpha() for c in numbers[i]),
                invoice_text.count(numbers[i]),
                -i,
            ),
        )

    kept: list[tuple[int, int]] = []
    for idxs in groups.values():
        kept.append((_best_spelling(idxs), idxs[0]))
    kept.sort(key=lambda pair: pair[1])

    old = header_raw.get("invoice_number")
    header_raw["invoice_number"] = " / ".join(numbers[best] for best, _ in kept)
    if dates_aligned:
        header_raw["invoice_date"] = " / ".join(dates[first] for _, first in kept)
    if totals_aligned:
        header_raw["invoice_total"] = " / ".join(totals[first] for _, first in kept)
    return [
        f"invoice_number '{old}' collapsed to '{header_raw['invoice_number']}' -- the same "
        f"invoice number read differently by OCR on different pages, not separate invoices"
    ]


_SUPPLIER_INVOICE_FORMATS = (
    {
        "name": "Hetero",
        "supplier": re.compile(r"\bHETER[O0]\b", re.IGNORECASE),
        "valid": re.compile(r"^SI\d{10}$"),
        "misread": re.compile(r"^[S5B8][I1l!|]1?(?P<core>\d{10})$", re.IGNORECASE),
        "canonical": "SI{core}",
    },
)


def repair_invoice_number_by_supplier_format(header_raw: dict[str, Any], invoice_text: str) -> list[str]:
    supplier = str(header_raw.get("supplier_name") or "")
    fmt = next((f for f in _SUPPLIER_INVOICE_FORMATS if f["supplier"].search(supplier)), None)
    parts = _split_combined_parts(header_raw.get("invoice_number"))
    if fmt is None or not parts:
        return []

    notes: list[str] = []
    repaired: list[str] = []
    for part in parts:
        new = part
        if not fmt["valid"].match(part):
            m = fmt["misread"].match(part)
            if m and m.group("core") in (invoice_text or ""):
                new = fmt["canonical"].format(core=m.group("core"))
        if new != part:
            notes.append(
                f"invoice_number '{part}' repaired to '{new}': {fmt['name']} invoice numbers are "
                f"'SI' + 10 digits and OCR misread the prefix (the digits are unchanged)"
            )
        repaired.append(new)
    if notes:
        header_raw["invoice_number"] = " / ".join(repaired)
    return notes


_NUMBER_TOKEN_RE = re.compile(r"(?<![\d.])\d[\d,]*(?:\.\d+)?(?!\d)")


def _numbers_in_text(text: str) -> set:
    from decimal import Decimal, InvalidOperation
    found = set()
    for tok in _NUMBER_TOKEN_RE.findall(text or ""):
        try:
            found.add(Decimal(tok.replace(",", "")))
        except InvalidOperation:
            pass
    return found


_HSN_SPACED_RE = re.compile(r"(?<![\d.,/-])\d{4} \d{2}(?: \d{2})?(?![\d.,])")
_HSN_PLAIN_RE = re.compile(r"(?<![\d.,/-])\d{8}(?![\d.,])")


def _squash_row(s: str) -> str:
    return re.sub(r"[│|\s]+", " ", s).strip().lower()


def _row_keys(item: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    part = str(item.get("part_number") or "").strip()
    if len(part) >= 4:
        keys.append(part.lower())
    desc = _squash_row(str(item.get("product_description") or ""))
    if len(desc) >= 8:
        keys.append(desc[:20])
    return keys


def _source_row_lines(item: dict[str, Any], invoice_text: str) -> list[str]:
    keys = _row_keys(item)
    if not keys:
        return []
    return [ln for ln in (invoice_text or "").splitlines() if any(k in _squash_row(ln) for k in keys)]


_ROW_START_RE = re.compile(r"^\s*\d{1,3}\s*[│|]\s*\d{1,4}\b")
_BLOCK_STOP_RE = re.compile(r"^\s*(?:total|basic|grand|amount|=)", re.IGNORECASE)


def _source_row_blocks(item: dict[str, Any], invoice_text: str) -> list[str]:
    keys = _row_keys(item)
    if not keys:
        return []
    lines = (invoice_text or "").splitlines()
    blocks: list[str] = []
    for i, ln in enumerate(lines):
        if not any(k in _squash_row(ln) for k in keys):
            continue
        block = [ln]
        for nxt in lines[i + 1:i + 3]:
            if (not nxt.strip() or _ROW_START_RE.match(nxt) or _BLOCK_STOP_RE.match(nxt)
                    or _HSN_SPACED_RE.search(nxt) or _HSN_PLAIN_RE.search(nxt)):
                break
            block.append(nxt)
        blocks.append("\n".join(block))
    return blocks


def _source_row_block(item: dict[str, Any], invoice_text: str) -> str:
    return "\n".join(_source_row_blocks(item, invoice_text))


def _hsn_on_source_row(item: dict[str, Any], invoice_text: str) -> str | None:
    lines = _source_row_lines(item, invoice_text)
    if not lines:
        return None
    spaced: dict[str, str] = {}
    plain: dict[str, str] = {}
    for line in lines:
        for m in _HSN_SPACED_RE.finditer(line):
            spaced.setdefault(m.group(0).replace(" ", ""), m.group(0))
        for m in _HSN_PLAIN_RE.finditer(line):
            plain.setdefault(m.group(0), m.group(0))
    found = spaced or plain
    return next(iter(found.values())) if len(found) == 1 else None


def _repair_hsn_code(item: dict[str, Any], invoice_text: str, row_no: int) -> str | None:
    raw = item.get("hsn_code")
    if raw is None or raw == "" or isinstance(raw, bool):
        return None
    s = re.sub(r"\s+", " ", re.sub(r"[^\d ]", " ", str(raw))).strip()
    digits = s.replace(" ", "")
    if not digits or len(digits) in (4, 6, 8):
        return None
    tokens = s.split(" ")
    if len(tokens) >= 2 and len(tokens[0]) <= 4:
        rest = " ".join(tokens[1:])
        if len(rest.replace(" ", "")) in (4, 6, 8) and rest in re.sub(r"\s+", " ", invoice_text or ""):
            item["hsn_code"] = rest
            return (f"[line_items][row {row_no}] hsn_code '{raw}' -> '{rest}': a leading item number "
                    f"was fused onto the HSN code printed in the source")
    if len(digits) <= 3:
        found = _hsn_on_source_row(item, invoice_text)
        if found:
            item["hsn_code"] = found
            return (f"[line_items][row {row_no}] hsn_code '{raw}' is an item number, not an HSN; replaced "
                    f"with '{found}', the only HSN printed on that item's row")
    if len(digits) == 10:
        india = {m for ln in (invoice_text or "").splitlines() if str(raw).strip() in ln
                 for m in re.findall(r"(?<![\d.])\d{4}\.\d{2}\.\d{2}(?![\d.])", ln)
                 if m.replace(".", "")[:6] == digits[:6]}
        if len(india) == 1:
            found = next(iter(india))
            item["hsn_code"] = found
            return (f"[line_items][row {row_no}] hsn_code '{raw}' has 10 digits; the same line prints the "
                    f"8-digit INDIA HTS '{found}', which is the Indian HSN; replaced")
    return None


def _to_decimal(v: Any):
    from decimal import Decimal
    f = _to_float(v)
    return None if f is None else Decimal(str(f))


_MONEY_RE = re.compile(r"(?<![\d.,])\d[\d.,]*[.,]\d{2}(?!\d|[.,]\d)")


def _money_counts(text: str):
    from collections import Counter
    from decimal import Decimal, InvalidOperation
    counts: Counter = Counter()
    for tok in _MONEY_RE.findall(text or ""):
        try:
            counts[Decimal(re.sub(r"[.,]", "", tok[:-3]) + "." + tok[-2:])] += 1
        except InvalidOperation:
            pass
    return counts


def _repair_shifted_price_columns(item: dict[str, Any], invoice_text: str, row_no: int) -> str | None:
    from decimal import Decimal
    qty, price, total = _to_decimal(item.get("quantity")), _to_decimal(item.get("unit_price")), _to_decimal(item.get("line_total"))
    if qty is None or price is None or total is None or qty <= 0:
        return None
    blocks = _source_row_blocks(item, invoice_text)
    if not blocks:
        return None
    decimal_one_cent = Decimal("0.01")
    per_block = [(blk, _money_counts(blk)) for blk in blocks]
    if any(price in c and total in c and abs(qty * price - total) <= decimal_one_cent for _, c in per_block):
        return None
    def _price_printed(p, counts, block) -> bool:
        if p in counts:
            return True
        digits = str((p * 100).quantize(Decimal("1")))
        return re.search(r"(?<![\d.,])" + re.escape(digits) + r"(?![\d.,])", block) is not None

    pairs = {
        (p, a)
        for block, counts in per_block
        for a, n in counts.items() if n >= 2
        for p in [(a / qty).quantize(decimal_one_cent)]
        if (p != a or qty == 1) and (p * qty).quantize(decimal_one_cent) == a and _price_printed(p, counts, block)
    }
    if len(pairs) != 1:
        return None
    new_price, new_total = next(iter(pairs))
    if float(new_price) == float(price) and float(new_total) == float(total):
        return None
    item["unit_price"], item["line_total"] = float(new_price), float(new_total)
    return (f"[line_items][row {row_no}] unit_price {price} / line_total {total} are not both printed on the "
            f"row; the row prints quantity {qty} x {new_price} = {new_total} (the amount appears twice, as "
            f"Basic Value and Total); corrected to unit_price {new_price}, line_total {new_total}")


_CHARGE_ROW_RE = re.compile(
    r"^(?:(?:AIR|SEA|OCEAN|INLAND)\s+)?(?:FREIGHT|SHIPPING|INSURANCE|HANDLING|PACKING|PACKAGING|FORWARDING)"
    r"(?:\s+(?:CHARGES?|COST|COSTS|FEES?))?$"
)


def _drop_charge_rows(items_raw: list[dict[str, Any]], invoice_text: str) -> list[str]:
    notes: list[str] = []
    keep: list[dict[str, Any]] = []
    source_lines = [re.sub(r"[│" + chr(92) + "s]+", " ", ln).strip() for ln in (invoice_text or "").splitlines()]
    for idx, it in enumerate(items_raw, start=1):
        desc = re.sub(r"[^A-Za-z ]+", " ", str(it.get("product_description") or "")) if isinstance(it, dict) else ""
        desc = re.sub(r"\s+", " ", desc).strip().upper()
        if not _CHARGE_ROW_RE.match(desc):
            keep.append(it)
            continue
        first_word = desc.split()[0]
        printed_once = [
            ln for ln in source_lines
            if re.match(r"(?i)^" + re.escape(first_word) + r"\b", ln) and len(_MONEY_RE.findall(ln)) == 1
        ]
        if printed_once:
            notes.append(
                f"[line_items][row {idx}] '{desc.title()}' is a charge printed once under the table "
                f"('{printed_once[0][:60]}'), not an item; removed"
            )
        else:
            keep.append(it)
    if len(keep) != len(items_raw):
        items_raw[:] = keep
    return notes


def apply_line_item_guardrails(items_raw: list[dict[str, Any]] | None, invoice_text: str) -> list[str]:
    from decimal import Decimal
    if not isinstance(items_raw, list) or not items_raw:
        return []

    def _dec(v: Any):
        f = _to_float(v)
        return None if f is None else Decimal(str(f))

    printed = _numbers_in_text(invoice_text)
    notes: list[str] = _drop_charge_rows(items_raw, invoice_text)
    for idx, it in enumerate(items_raw, start=1):
        if not isinstance(it, dict):
            continue
        hsn_note = _repair_hsn_code(it, invoice_text, idx)
        if hsn_note:
            notes.append(hsn_note)
        shift_note = _repair_shifted_price_columns(it, invoice_text, idx)
        if shift_note:
            notes.append(shift_note)
        qty, price, total = _dec(it.get("quantity")), _dec(it.get("unit_price")), _dec(it.get("line_total"))
        if qty is None or price is None or total is None or qty <= 0 or price <= 0:
            continue
        expected = (qty * price).quantize(Decimal("0.01"))
        if abs(expected - total) <= Decimal("0.01") or total in printed:
            continue
        if expected in printed:
            it["line_total"] = float(expected)
            notes.append(
                f"[line_items][row {idx}] line_total {total} is not printed anywhere on the "
                f"document; replaced with {expected} (= quantity {qty} x unit price {price}), "
                f"which IS printed"
            )
    return notes


def apply_header_guardrails(
    header_raw: dict[str, Any] | None,
    items_raw: list[dict[str, Any]] | None,
    invoice_text: str,
    shipment_type: str | None = None,
    notes: list[str] | None = None,
) -> None:
    if not isinstance(header_raw, dict):
        return
    low_text = invoice_text.lower()

    collapse_notes = repair_invoice_number_by_supplier_format(header_raw, invoice_text)
    collapse_notes += collapse_duplicate_invoice_refs(header_raw, invoice_text)
    if notes is not None:
        notes.extend(collapse_notes)

    if "net realis" not in low_text and "net charge" not in low_text and "realisable" not in low_text:
        if header_raw.get("net_realisable_amount") is not None:
            header_raw["net_realisable_amount"] = None

    invoice_total = _to_float(header_raw.get("invoice_total"))
    if invoice_total is not None and (header_raw.get("currency") or "").strip().upper() != "INR":
        items_for_sum = items_raw if isinstance(items_raw, list) else []
        line_amounts = [_to_float(it.get("line_total")) for it in items_for_sum if isinstance(it, dict)]
        line_sum_known = bool(line_amounts) and all(a is not None for a in line_amounts)
        line_sum = sum(line_amounts) if line_sum_known else None
        items_contradict_total = (
            line_sum is not None and abs(line_sum - invoice_total) > max(1.0, invoice_total * 0.01)
        )

        fob_value_re = (
            r"f\.?\s*o\.?\s*b\.?\s*value"
            r"(?:\s*\(?\s*(?:in\s+)?(?:inr|rs\.?|₹|usd|eur|gbp)\s*\)?)?"
            r"\s*[:\-]?\s*(?:inr|rs\.?|₹)?\s*([\d,]+\.?\d*)"
        )
        for fob_match in re.finditer(fob_value_re, low_text):
            fob_value = _to_float(fob_match.group(1))
            if not fob_value or fob_value <= 0:
                continue
            if abs(invoice_total - fob_value) > max(1.0, fob_value * 0.01):
                continue
            line_start = low_text.rfind("\n", 0, fob_match.start()) + 1
            line_end = low_text.find("\n", fob_match.end())
            fob_line = low_text[line_start:line_end if line_end != -1 else len(low_text)]
            fob_line_is_inr = re.search(r"\binr\b|\brs\b\.?|\brupees?\b|₹", fob_line) is not None
            if fob_line_is_inr or items_contradict_total:
                net_realisable = header_raw.get("net_realisable_amount")
                header_raw["invoice_total"] = net_realisable if net_realisable is not None else None
            break

    items_for_qty = items_raw if isinstance(items_raw, list) else []
    total_qty = sum(
        q for it in items_for_qty
        if isinstance(q := it.get("quantity"), (int, float))
    ) if items_for_qty else None
    if total_qty:
        candidates = []
        for ln in invoice_text.splitlines():
            if not re.match(r"^\s*TOTAL\b", ln, re.IGNORECASE):
                continue
            nums = [float(n) if "." in n else int(n) for n in re.findall(r"\d+(?:\.\d+)?", ln)]
            if total_qty not in nums:
                continue
            candidates.append(nums)
        with_pkgs = [nums for nums in candidates if nums.index(total_qty) > 0]
        if with_pkgs:
            header_raw["total_packages"] = with_pkgs[0][0]
        elif candidates:
            header_raw["total_packages"] = None

    if total_qty:
        current = header_raw.get("total_packages")
        looks_wrong = current in (None, "") or (
            isinstance(current, (int, float)) and float(current) == float(total_qty)
        )
        if looks_wrong:
            unit_matches = re.findall(
                r"(\d[\d,]*)\s*(?:nos\.?)?\s*(?:cartons?|ctns?|cases?|boxes?|"
                r"packages?|pkgs?|crates?|bags?|drums?)\b",
                invoice_text, re.IGNORECASE,
            )
            pkg_candidates = {
                int(n) for m in unit_matches
                if (n := m.replace(",", "")).isdigit()
            }
            pkg_candidates = {c for c in pkg_candidates if 0 < c < total_qty}
            if len(pkg_candidates) == 1:
                header_raw["total_packages"] = pkg_candidates.pop()

    if shipment_type != "export":
        return

    def _country_norm(s: Any) -> str:
        s = str(s or "").strip().upper()
        if s in ("INDIA", "IND", "IN"):
            return "IN"
        return s

    supplier_addr = str(header_raw.get("supplier_address") or "").upper()
    origin = _country_norm(header_raw.get("country_of_origin"))
    dest = _country_norm(header_raw.get("country_of_destination"))
    supplier_is_indian = re.search(r"\bINDIA\b", supplier_addr) is not None
    if supplier_is_indian and origin and origin != "IN" and (origin == dest or dest == "IN"):
        header_raw["country_of_origin"] = "IN"

