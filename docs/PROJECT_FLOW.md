# Project Flow — Invoice Extraction Pipeline

This document explains what this project does, end to end: the objective,
the overall process, how extraction actually happens (templates → chunks →
LLM calls), what the orchestrator is responsible for, and how the final
JSON gets assembled. It reflects the current state of the code (the
`invoice_extractor/` package), not the older flat-file layout described in
the root `ARCHITECTURE.md`, which predates the dynamic-template work this
document covers.

---

## 1. Objective

Turn a scanned or digital invoice **PDF** into a **structured JSON** shaped
exactly like a specific customs/logistics **Excel or CSV template** — the
same columns, in the same sheets, under the same names the template
defines — so that JSON can be fed straight into a customs filing system
(a Bill of Entry, a Shipping Bill, a logistics booking) without manual
re-typing.

The system is **template-driven, not hardcoded to one document shape**.
Two templates (`export`, `import`) have years of hand-tuned extraction
rules behind them. Every other template is **discovered automatically**
from whatever `.xlsx`/`.xls`/`.csv` file is dropped into `templates/` —
no code change required to add a new one (see §9).

---

## 2. The process, at a glance

```
 PDF upload                                                 (api.py / main.py / batch.py)
    │
    ▼
 Scan quality check  ───────────────────────────  invoice_extractor/pdf/scan_validator.py
    │  (tilt angle, clipped table edges — refuses early if the table is physically cut off)
    ▼
 PDF → text  ────────────────────────────────────  invoice_extractor/pdf/reader.py
    │  digital text layer (layout-preserving "2D canvas", "│" marks column boundaries)
    │  OR OCR fallback (Docling → RapidOCR) when the digital layer is too sparse
    ▼
 Resolve template  ───────────────────────────────  invoice_extractor/schema/templates_config.py
    │  explicit ?type=<name>  →  use that template directly
    │  type omitted           →  invoice_extractor/detect_type.py guesses import vs export only
    │                             (any OTHER template must always be requested explicitly)
    ▼
 Load schema  ─────────────────────────────────────  invoice_extractor/schema/schema.py
    │  reads the template's own column headers, one dict per sheet:
    │  {fields, types, aliases, descriptions, link_field}
    ▼
 Extraction + validation + retry  ─────────────────  invoice_extractor/extraction/orchestrator.py
    │  (see §4, §5 below for what actually happens here)
    ▼
 Final JSON  ───────────────────────────────────────  {header, sheets, warnings, validation, ...}
    │
    ▼
 Saved + returned  ────────────────────────────────  api.py (HTTP response + results/ folder)
                                                       main.py / batch.py (results/ folder + CLI summary)
```

---

## 3. Templates: the single source of truth

[`templates_config.py`](../invoice_extractor/schema/templates_config.py) scans
`templates/` at startup and builds two dicts:

- `TEMPLATE_PATH` — `{type_name: file_path}`
- `ITEM_SHEET_NAME` — `{type_name: "the sheet holding line items"}`

`export`/`import` keep their historical hardcoded filenames
(`EXP_TEMPLET.xlsx`/`IMP_TEMPLET.xlsx`) because those names don't match a
clean slug. Every other file gets a `type_name` derived from its filename
(or a friendly override in `_FILENAME_TO_TYPE`), and its item sheet is
whichever sheet is literally named `"ITEM"`/`"ITEMS"`, or the first sheet
if none matches.

[`schema.py`](../invoice_extractor/schema/schema.py)'s `load_template_schema()`
then reads every sheet's header row and builds, per sheet:

```python
{
  "fields":       ["Inv No", "Date", "Quantity", ...],   # real column names, in order
  "types":        {"Quantity": "number", "Date": "string", ...},
  "aliases":      {"Inv No": ["Inv Number"], ...},        # alternate labels the LLM may see printed
  "descriptions": {"CTH": "Customs Tariff Heading -- ...", ...},  # hand-written meanings, optional
  "link_field":   "Item_No" | "Item_Ser_No" | "SL_No" | None,
}
```

`aliases` and `descriptions` come from `_MANUAL_ALIASES` / `_MANUAL_DESCRIPTIONS`
in `schema.py` — hand-curated, keyed by the exact real column name, shared
by every template that happens to use that name. A column with no manual
entry falls back to an auto-generated alias from its own name.

---

## 4. Extraction: three kinds of LLM calls

A single invoice goes through **up to three separate kinds of calls**, not
one big call asking about everything at once (a single-call design was
tried, confirmed to produce low accuracy, and was replaced by this split):

### (a) Header call — fixed, universal fields
[`llm_extract.py: extract_invoice_header()`](../invoice_extractor/extraction/llm_extract.py)
asks for ~30 fixed concepts (`invoice_number`, `invoice_date`,
`supplier_name`, `country_of_origin`, ...) that exist **regardless of which
template is selected** — this is not schema-driven. Plain JSON mode, no
Pydantic model sent to the LLM.

### (b) Core line-items call — the hand-tuned fields (export/import only)
[`llm_extract.py: extract_line_items()`](../invoice_extractor/extraction/llm_extract.py)
asks for ~11–15 fields with years of extraction rules behind them
(HSN disambiguation, batch-rate math, thousands-separator detection,
packing-list detection, ...). For `export`/`import`, these generic names
get rewritten to the template's own real column names before the prompt is
sent. For any other template (no hand-tuned mapping), the model just
answers with the plain generic names — this call's only real job there is
correctly counting/ordering the line items, since the long-tail call below
covers every real column anyway.

### (c) Chunked long-tail call(s) — everything else, including every other sheet
[`chunked_extraction.py`](../invoice_extractor/extraction/chunked_extraction.py)
covers every column (a) and (b) don't already own: the item sheet's own
remaining columns, plus every auxiliary sheet (License, AR4, Drawback,
Job Work, Single Window declarations, ...).

1. **Chunking**: `build_field_chunks()` splits each sheet's remaining
   columns into groups of ≤12 fields (`CHUNK_SIZE`) — small enough that the
   model isn't juggling a hundred unrelated columns at once.
2. **One Pydantic model per chunk**: `_row_model_for_chunk()` builds a
   model with one field per column (`Optional[str]`/`Optional[float|str]`),
   each with a `description=` combining its hand-written meaning and alias
   list (`field_description_text()`, shared by every consumer). This model
   is sent directly to OpenAI via structured outputs
   (`response_format=<model>`) — the one place a field's description
   actually reaches the LLM.
3. **One call per chunk, run concurrently** — unless the template needs
   more calls than the configured budget (`MAX_LLM_CALLS_PER_TEMPLATE = 5`),
   in which case multiple chunks (often from different sheets) are bundled
   into one combined call instead, each chunk still keeping its own
   section, field list, and row-count rule — a deliberate cost/accuracy
   tradeoff (see the module's own comments on `MAX_LLM_CALLS_PER_TEMPLATE`).
4. **The item sheet is special when it has no link column**
   (`Item_No`/`Item_Ser_No`/`SL_No`): its chunks are marked `multi_row=True`
   and must return **one row per line item**, matched back to the right
   item by **position** (same order as the invoice's own item table) since
   there's no ID to join on — rather than "at most one row", which is the
   rule for genuine single-record auxiliary sheets.
5. **Static, hand-editable models**: a template can have a generated file
   under `invoice_extractor/schema/generated/<type_name>.py`
   ([`tools/generate_template_models.py`](../tools/generate_template_models.py))
   with one explicit Pydantic class per chunk — a real, readable file you
   can hand-edit (mainly to improve `description=` text). When one exists,
   `chunked_extraction.py` uses it instead of building the model on the
   fly; re-run the generator whenever the template's own columns change.

---

## 5. The orchestrator

[`orchestrator.py`](../invoice_extractor/extraction/orchestrator.py) is a
LangGraph state machine (`classify → extract → [done | retry]`) that drives
calls (a), (b), and (c) above for one invoice and decides **which model
tier** runs each attempt:

- **`classify_complexity()`** picks a model tier (simple/medium/complex)
  from the PDF's own text — cheap, clean invoices use a cheaper model.
- **Attempt 1** runs all three call types.
- **Merging**: the core call's real-column answers get translated back to
  this pipeline's internal generic vocabulary
  (`_translate_real_columns_to_generic`); the long-tail chunks' rows get
  folded into each item's `_extra_template_fields`, matched by link field
  or by position.
- **Validation** (§6) runs on the merged result.
- **If invalid and retries remain**: escalates to the top-tier model, and
  — critically — only **re-runs the pass(es) that actually failed**
  (header and/or items), carrying structured feedback about what was
  wrong. The long-tail chunks never re-run; there's no per-field retry
  loop for that data.
- **If the PDF bundles multiple distinct invoices**: splits into several
  independently-validated results instead of one flat merge
  (`multi_invoice.py`).

[`reextraction.py`](../invoice_extractor/extraction/reextraction.py) is a
parallel, simpler entry point with the same retry policy but a single,
caller-supplied model (no tiering) — used when the LangGraph orchestrator
is explicitly disabled.

---

## 6. Validation

### Shaping into the template's real rows
[`validate.py: validate_and_coerce()`](../invoice_extractor/validation/validate.py):
1. `_clean_invoice_header()` — normalizes the header dict (state codes,
   country codes, currency formatting).
2. `_clean_generic_item()` — cleans each line item (drops subtotal rows,
   coerces numbers, cross-checks qty×price against the printed total).
3. `_map_export_item()` / `_map_import_item()` (hand-tuned) or
   `_map_generic_item()` (fallback — just passes through whatever real
   columns the long-tail call found) — places values onto the template's
   real column names.
4. `_apply_*_domain_rules()` — final per-column cleanup.
5. `validate_row_against_model()` ([`models.py`](../invoice_extractor/schema/models.py))
   — checks the row against that sheet's own Pydantic model (validation
   only, never sent to the LLM) and **guarantees every column the template
   defines appears in the row**, filled with `""` if nothing was found. A
   row that's entirely empty is dropped; a found value never disappears.

### Business-rule validation
[`invoice_validator.py: validate_invoice()`](../invoice_extractor/validation/invoice_validator.py)
checks things like qty×price=amount, required fields present, GSTIN shape
— producing a `ValidationResult` that drives the orchestrator's retry
decision.

---

## 7. The final JSON

```jsonc
{
  "source_file": "invoice.pdf",
  "shipment_type": "export",          // or any discovered template name
  "detection": { "export_score": 6, "import_score": 0, "signal": "keyword" },  // null if type was explicit
  "extraction_method": "2d_layout_canvas",  // or "docling" / "rapidocr" for OCR
  "model": "gpt-5-nano",
  "complexity": "simple",
  "token_usage": { "input_tokens": 9474, "output_tokens": 3586, "cost_inr": 0.18 },
  "header": { "invoice_number": "...", "invoice_date": "...", "...": "..." },
  "sheets": {
    "ITEMS": [ { "Inv No": "...", "Quantity": "2", "...": "" } ],
    "License": [],                    // every sheet the template defines always appears
    "...": []
  },
  "warnings": [ "..." ],
  "validation": { "valid": true, "status": "VALID", "errors": [], "warnings": [] },
  "attempts": 1,
  "extraction_status": "validated"
}
```

Every key's presence is determined by the **template's own schema**, not
by what the PDF happened to contain — a column with nothing found still
appears, as `""`; a sheet with no matched rows still appears, as `[]`.
Nothing is silently dropped, and nothing is guessed or invented.

An optional [`output_shape.py`](../invoice_extractor/output_shape.py) filter
can trim this down (fewer top-level keys, fewer header fields, fewer sheet
columns) via `OUTPUT_CONFIG` or the `OUTPUT_SHAPE_CONFIG` env var — off by
default (full output).

---

## 8. Entry points

| Entry point | Use case |
|---|---|
| [`api.py`](../api.py) | FastAPI HTTP server — `/extract`, `/extract-batch`, `/save-pdf`, `/health`. The one most integrations use. |
| [`main.py`](../main.py) | CLI, one PDF at a time, prints a human-readable summary. |
| [`batch.py`](../batch.py) | CLI, a whole folder of PDFs, parallel workers, a summary CSV. |
| [`db_worker.py`](../db_worker.py) | Watches a Postgres job queue, runs the same pipeline as `api.py` per queued job. |

Every result (any entry point) is also saved to
`results/validation_success|validation_failed/<pdf name>/<pdf name>.json`.

---

## 9. Adding a new template (no code change needed)

1. Drop a `.xlsx`/`.xls`/`.csv` file into `templates/`.
2. Its line-items sheet should be named `"ITEM"` or `"ITEMS"` (or it falls
   back to the first sheet).
3. Restart the running process (`api.py`/`db_worker.py`) — templates are
   loaded once at startup, not re-read per request.
4. Request it with `type=<slugified-filename>` — check `/health` for the
   exact name the server assigned it.

That's enough to extract real data into every one of that template's
columns. Two optional follow-ups improve quality:

- **Add descriptions**: edit `_MANUAL_DESCRIPTIONS`/`_MANUAL_ALIASES` in
  `schema.py` for any cryptic column names, so the LLM gets real context
  instead of just the column name itself.
- **Generate a static, hand-editable model file**:
  `python tools/generate_template_models.py <type_name>` — gives you a
  real `.py` file per chunk to review and refine descriptions in directly.

Auto-detection (`type` omitted) only ever distinguishes `import` vs
`export` — any other template must always be requested explicitly.
