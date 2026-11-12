# Architecture & Project Layout

This project extracts structured data from invoice PDFs (import or export
shipments, plus any other customs/logistics template dropped into
`templates/`) into India-customs Excel templates, using a 2D layout-aware
text extraction step, two deterministic "is this even an invoice" gates, an
optional LLM-based page-content check, and a template-driven LLM extraction
+ validation + retry pipeline.

This file covers the big picture: how the pieces fit together, how the
folder is laid out, and current configuration. For depth:

- **[docs/PROJECT_FLOW.md](docs/PROJECT_FLOW.md)** — the extraction logic
  itself, end to end (templates, the three kinds of LLM calls, chunking,
  the orchestrator, validation, the final JSON shape).
- **[docs/FILE_REFERENCE.md](docs/FILE_REFERENCE.md)** — file-by-file: what
  each module contains, its key functions, non-obvious design decisions.
- `README.md` is an older, narrower write-up of the extraction logic and
  predates the `invoice_extractor/` package below — treat it as historical
  context, not a current reference.

## Folder layout

```
Transorian-langraph-uv/
├── .env                        # real secrets (gitignored, never commit)
├── .env.example                 # template listing every env var the app reads
├── requirements.txt
├── README.md                    # older extraction-logic write-up (pre-package; see note above)
├── ARCHITECTURE.md               # this file
├── docs/
│   ├── PROJECT_FLOW.md           # extraction logic, end to end (current)
│   └── FILE_REFERENCE.md         # file-by-file deep reference (current)
│
├── Entry points (root-level, each imports invoice_extractor/ as a package)
│   ├── main.py                  # CLI, one PDF at a time
│   ├── batch.py                  # CLI, a whole folder (parallel workers + summary CSV)
│   ├── api.py                    # FastAPI HTTP server (/extract, /extract-batch, /save-pdf, /health)
│   └── db_worker.py               # LISTENs on a Postgres job queue, runs the same pipeline as api.py
│
├── invoice_extractor/            # the actual pipeline package
│   ├── pdf/
│   │   ├── reader.py              # PDF -> text (2D layout canvas / OCR fallback)
│   │   ├── scan_validator.py      # tilt + clipped-table detection for the scan
│   │   └── device.py              # OCR_DEVICE selection (auto/cuda/cpu) for RapidOCR
│   ├── number_format.py           # detects/normalizes decimal-comma number formats (742,50 -> 742.50)
│   ├── invoice_classifier.py      # deterministic, non-LLM "is this even an invoice" gate
│   ├── invoice_llm_check.py       # optional LLM page-content check (multi-page docs, non-invoice pages)
│   ├── detect_type.py             # text -> "import" | "export" | "unknown" (only these two, ever)
│   ├── schema/
│   │   ├── schema.py               # reads column headers out of the xlsx/xls/csv templates
│   │   ├── templates_config.py    # single source of truth: template paths, sheet names, aliases
│   │   ├── template_item_fields.py # generic item-field concept map for non-hand-tuned templates
│   │   ├── field_naming.py         # real column name -> valid Python identifier (generator use only)
│   │   ├── pydantic_models.py      # the one place every Pydantic model gets built
│   │   ├── models.py               # thin layer on pydantic_models.py + validate_row_against_model()
│   │   └── generated/              # generated, hand-editable-description Pydantic models, one file
│   │                                per template that has one (see tools/generate_template_models.py)
│   ├── extraction/
│   │   ├── llm_extract.py          # header + core line-items calls; guardrails; cost/pricing table
│   │   ├── chunked_extraction.py   # long-tail: every column the core calls don't already own
│   │   ├── orchestrator.py         # LangGraph version: classify -> extract -> [done | retry]
│   │   ├── reextraction.py         # simpler parallel entry point, single caller-supplied model
│   │   └── multi_invoice.py        # splits a combined PDF (several distinct invoices) into one
│   │                                 independently-validated result per invoice
│   ├── validation/
│   │   ├── validate.py             # raw JSON -> template columns, customs cleanup
│   │   └── invoice_validator.py    # post-mapping business-rule validation
│   ├── concurrency.py              # llm_retry / llm_slot -- shared retry + concurrency limiting for
│   │                                 every OpenAI call in the pipeline
│   ├── output_shape.py             # filters the final result JSON per OUTPUT_CONFIG / OUTPUT_SHAPE_CONFIG
│   └── pipeline_log.py             # console-output helpers only, no effect on behavior
│
├── templates/                     # one .xlsx/.xls/.csv per template (checked-in config, not code):
│                                    #   EXP_TEMPLET.xlsx / IMP_TEMPLET.xls  (hand-tuned "export"/"import")
│                                    #   SEZ EXPORT.xlsx / SEZ IMPORT.xlsx    (generic mapper, see below)
│                                    #   ExportXLSXTemplate.xlsx, iCaffeExcel_Export.xls,
│                                    #   "Import- LOGYSIS TEMPLATE".csv, "ImportExcel (3)".xls,
│                                    #   "MASTERS DETAILS".xlsx (reference data, not an invoice template)
├── prompts/                        # OPTIONAL hand-authored sheets prompts, one per shipment type
│                                    #   (<type>_sheets_prompt.txt) -- replaces the dynamically-generated
│                                    #   item/extra-sheets prompt for that type entirely, when present
├── tests/                          # pytest unit tests
├── tools/
│   ├── generate_template_models.py  # regenerates invoice_extractor/schema/generated/<type>.py
│   └── benchmark_ocr.py             # dev-only OCR engine comparison, not imported by the app
│
└── Runtime / data (gitignored — regenerated or externally supplied, not source)
    ├── FILES/                      # sample/real client invoices (import/export) — business data
    ├── received_pdfs/              # PDFs uploaded via POST /save-pdf, saved per job_id
    └── results/                    # unified output for main.py, batch.py, and api.py:
                                     #   validation_success/<pdf>/<pdf>.json
                                     #   validation_failed/<pdf>/<pdf>.json
                                     # (batch.py also drops results/_summary.csv here)
```

The organizing idea is unchanged from before: **code that defines behavior
lives under `invoice_extractor/` (or the four root entry points); data the
code reads or writes lives in its own gitignored folders.** Nothing under
"Runtime / data" should ever be committed.

## Pipeline flow

```
 PDF file
   │
   ▼
┌───────────────────────────────┐
│ pdf/scan_validator.py          │  cheap pre-check: tilt angle, clipped table border.
│ check_scan_quality()            │  A confirmed failure short-circuits here -- a normal
└───────────┬─────────────────────┘  (REJECTED) result is returned, nothing else runs.
            ▼
┌───────────────────────────────┐
│ pdf/reader.py                   │  digital text layer (pdfplumber word coords,
│ extract_text()                  │  "│" column markers) OR OCR fallback:
│                                  │  Docling (primary) -> RapidOCR (backup)
└───────────┬─────────────────────┘
            ▼
┌───────────────────────────────┐
│ number_format.py                 │  detects decimal-comma formatting (742,50) and
│ normalize_decimal_commas()        │  normalizes it before anything downstream reads numbers
└───────────┬─────────────────────┘
            ▼
┌───────────────────────────────┐
│ invoice_classifier.py             │  deterministic, non-LLM gate: blank/unreadable page,
│ classify_document()                │  or confidently-not-an-invoice document (packing list,
└───────────┬─────────────────────┘  certificate, PO, ...) -> REJECTED, no LLM call spent.
            ▼  (only if is_invoice)
┌───────────────────────────────┐
│ invoice_llm_check.py (optional,   │  one cheap LLM call classifying every page
│ INVOICE_LLM_CHECK=1 by default)   │  (invoice / packing_list / transport / certificate / ...);
│ check_contains_invoice()           │  REJECTED if no page is actually an invoice page.
└───────────┬─────────────────────┘  Can be disabled entirely via INVOICE_LLM_CHECK=0.
            ▼
┌───────────────────────────────┐
│ detect_type.py                    │  keyword + structural scoring -> import / export / unknown
│ detect_shipment_type()             │  (only when `type=`/`--type` wasn't given explicitly;
└───────────┬─────────────────────┘   any OTHER template must always be requested by name)
            ▼
┌───────────────────────────────┐
│ schema/schema.py + schema/templates_config.py │  resolve which template + sheet applies,
│ load_template_schema()                         │  read its real column headers/aliases/descriptions
└───────────┬─────────────────────────────────────┘
            ▼
┌─────────────────────────────────────────────────────────────────┐
│ extraction/orchestrator.py (default) OR extraction/reextraction.py (--no-orchestrate) │
│                                                                                         │
│  classify_complexity() -> pick model tier (simple/medium/complex)                       │
│         │                                                                               │
│         ▼                                                                               │
│  llm_extract.py: extract_invoice_header()  (~30 fixed, universal header concepts)      │
│         +         extract_line_items_chunked() / extract_line_items()                  │
│                    (~11-15 core item fields; for export/import these are the hand-tuned │
│                     real column names -- see "Two kinds of templates" below)            │
│         +         chunked_extraction.py: extract_all_chunks()                           │
│                    (long-tail: every remaining real column, on EVERY sheet the schema   │
│                     allows -- bounded to MAX_LLM_CALLS_PER_TEMPLATE calls total)        │
│         ▼                                                                               │
│  validate.py: validate_and_coerce()                                                     │
│         │  (generic JSON -> real template columns, type coercion, India-customs cleanup) │
│         ▼                                                                               │
│  invoice_validator.py: validate_invoice()                                               │
│         │  (business-rule checks: totals reconcile, required fields present, ...)        │
│         ▼                                                                               │
│  multi_invoice.py: split if the PDF bundles several distinct invoices                    │
│         ▼                                                                               │
│  valid? ──no──> escalate to top-tier model, retry (bounded by MAX_REEXTRACTION_ATTEMPTS) │
│         │        re-running only the pass(es) that actually failed, with structured      │
│        yes       feedback on what was wrong                                              │
│         ▼                                                                               │
│  final JSON: is_invoice + header + sheets + warnings + token_usage + validation          │
└───────────┬───────────────────────────────────────────────────────────────────────────┘
            ▼
 main.py / batch.py / api.py / db_worker.py
   write <out>/validation_success|validation_failed/<file>/<file>.json
   (api.py also returns the JSON over HTTP and, for /save-pdf, POSTs it to a callback URL)
```

## Two kinds of templates

- **`export` / `import`** (`EXP_TEMPLET.xlsx` / `IMP_TEMPLET.xls`) have years
  of hand-tuned extraction rules: `_GENERIC_TO_REAL_ITEM_FIELDS` in
  `llm_extract.py` rewrites the core item-field prompt to their exact real
  column names, and `validate.py`'s `_map_export_item()` /
  `_map_import_item()` place values with full, bespoke per-column logic
  (HSN disambiguation, batch-rate math, part-number/PO-number
  disambiguation, ...). `TYPE_ALIASES` in `templates_config.py` lets a
  caller request these under friendlier public names (e.g. `"Focus export
  Template"`) that resolve straight back to `"export"`/`"import"` before
  anything else sees them.
- **Every other template** (including the newer `SEZ EXPORT` / `SEZ
  IMPORT`, and any `.xlsx`/`.xls`/`.csv` dropped into `templates/`) goes
  through the **generic mapper**: `schema/template_item_fields.py`'s
  `ITEM_FIELDS_BY_TEMPLATE_FILE` maps that template's own real column names
  onto a small set of shared concepts (`ser_no`, `description`, `quantity`,
  `unit_price`, `hsn`, `tariff_qty`, ...), and `validate.py` uses that
  mapping (`_map_template_file_item()`) instead of a hand-written
  per-template function. A template with no entry there at all still works
  — its line items just pass through whatever real columns the long-tail
  chunked call found, unmapped (`_map_generic_item()`).
  - `schema.py`'s `_EXPLICIT_ITEM_FIELD_ALLOWLIST` can additionally scope a
    generic template down to only the fields (and only the sheet) it
    actually needs, *before* chunking happens — used by `SEZ EXPORT`/`SEZ
    IMPORT` to extract only their `ITEM`/`BOE` sheet's named columns and
    skip every other sheet (DRAWBACK, License, STR, ...) entirely, instead
    of chunking and then discarding irrelevant results. This is a
    deliberate latency/cost reduction: it cut each SEZ document from ~7 LLM
    calls (every sheet chunked) down to ~3 (header + items + one long-tail
    chunk), without dropping any of the fields the template actually wants.
  - Separately, some templates mark each column "from invoice" vs. "from
    master data" in an annotation row; `schema.py`'s
    `_filter_from_invoice_headers()` keeps only "from invoice" columns for
    those templates automatically. The explicit allow-list above is used
    instead, for templates whose annotation row doesn't match that pattern
    cleanly or has none at all.

`llm_extract.py`'s `_LINE_ITEMS_SYSTEM_PROMPT` also carries a strict
priority rule for `part_number`: when the item table has an explicit
column header naming it ("Part Number", "Part No", "P/N", ...), that
column's own per-row value is authoritative and is never overridden by a
differently-labeled code printed nearby (a second reference number, an
internal tag, a continuation line) — this exists specifically because an
earlier version let a stray "CPT#:"-labeled continuation line override an
already-correct, explicitly-labeled part number.

## Configuration (`.env`)

All environment variables the app reads are documented in `.env.example`.
Copy it to `.env` and fill in real values — `.env` is gitignored and must
never be committed.

| Variable | Required | Default | Used by |
|---|---|---|---|
| `OPENAI_API_KEY` | **yes** | — | every extraction/LLM-check call |
| `HF_HUB_DISABLE_SYMLINKS` | Windows only | — | Docling model downloads (`pdf/reader.py`) |
| `MODEL_TIER_SIMPLE` / `_MEDIUM` / `_COMPLEX` | no | `gpt-5-nano` / `gpt-6-luna` / `gpt-4o-mini` | `orchestrator.py`'s `classify_complexity()` + `_select_model()` |
| `MAX_REEXTRACTION_ATTEMPTS` | no | `1` | `reextraction.py` / `orchestrator.py` (also overridable per-request via `?max_retries=`) |
| `CHUNK_SIZE` | no | `12` | `chunked_extraction.py` — real columns per long-tail LLM call |
| `MAX_LLM_CALLS_PER_TEMPLATE` | no | `5` | `chunked_extraction.py` — hard cap on long-tail calls per document |
| `INVOICE_LLM_CHECK` | no | `1` (on) | `api.py` — set `0`/`false`/`no`/`off` to skip the optional per-page LLM invoice check |
| `EXTRACTOR_MODEL` | no | `gpt-5-nano` | `api.py`/`main.py`/`batch.py` — model when the orchestrator is off |
| `EXTRACTOR_SAVE_DIR` | no | `./results` | where results get saved |
| `EXTRACTOR_PDF_DIR` | no | `./received_pdfs` | where `/save-pdf` uploads land |
| `OUTPUT_SHAPE_CONFIG` | no | — | `output_shape.py` — path to a JSON file shaping the final result; blank = full output |
| `EXTRACTOR_CALLBACK_URL` | no | `http://192.168.2.46:8765/process_invoices/` | `/save-pdf` results callback (empty string disables it) |
| `EXTRACTOR_CALLBACK_API_KEY` / `_BEARER_TOKEN` | no | — | sent as `X-API-KEY` / `Authorization: Bearer ...` on the callback |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` / `DB_HOST` / `DB_PORT` | `db_worker.py` only | `DB_PORT=5432` | Postgres connection |
| `POLL_SECONDS` | no | `10` | `db_worker.py` fallback poll interval (also LISTENs on `job_queued`) |
| `QUEUED_STATUS` / `PROCESSING_STATUS` / `FAILED_STATUS` / `DOCUMENT_EXTRACTED_STATUS` / `DOCUMENT_FAILED_STATUS` | no | see `.env.example` | `db_worker.py` job/document status text |
| `JOB_SHIPMENT_TYPE` | no | — | force every `db_worker.py` job to one template, skipping the DB lookup |
| `SHIPMENT_TYPE_TABLE` / `_ID_COLUMN` / `_VALUE_COLUMN` | no | see `.env.example` | `db_worker.py`'s `shipment_type_id -> template name` lookup |
| `MEDIA_ROOT` | no | current directory | `db_worker.py` — base path for `job_app_document`'s relative file paths |

**Security note:** if an `OPENAI_API_KEY` has ever been read in plaintext
during a cleanup, pasted into a chat log, or shown in a screenshot, treat it
as potentially exposed and rotate it in the OpenAI dashboard.

## Adding a new template

See [docs/PROJECT_FLOW.md §9](docs/PROJECT_FLOW.md#9-adding-a-new-template-no-code-change-needed)
for the full walkthrough. In short: drop the file into `templates/`,
restart the process, and it's extractable immediately through the generic
mapper — hand-tuning (a `template_item_fields.py` entry, an
`_EXPLICIT_ITEM_FIELD_ALLOWLIST` scope, a generated model file) is optional,
added only when the generic path isn't accurate enough on its own.
