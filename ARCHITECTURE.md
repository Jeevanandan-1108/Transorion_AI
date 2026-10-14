# Architecture & Project Layout

This project extracts structured data from invoice PDFs (import or export
shipments) into India-customs Excel templates, using a 2D layout-aware text
extraction step, a cheap deterministic classifier, and an LLM extraction +
validation + retry pipeline. For the field-level extraction logic itself,
`README.md` is the deeper reference; this file covers how the pieces fit
together and how the folder is laid out.

## Folder layout

```
Transorian-langraph/
├── .env                  # real secrets (gitignored, never commit)
├── .env.example          # template listing every env var the app reads
├── requirements.txt
├── README.md             # extraction logic, caveats, hardcoded rules
├── ARCHITECTURE.md        # this file
│
├── Core pipeline (flat, each module = one pipeline stage)
│   ├── pdf_reader.py      # PDF -> text (2D layout canvas / OCR fallback)
│   ├── scan_validator.py  # tilt + clipped-table detection for the scan
│   ├── detect_type.py     # text -> "import" | "export" | "unknown"
│   ├── schema.py          # reads column headers out of the xlsx templates
│   ├── templates_config.py  # single source of truth: template paths, sheet names
│   ├── models.py          # dynamic Pydantic models generated from schema.py
│   ├── llm_extract.py     # text -> raw header/line-item JSON via OpenAI
│   ├── validate.py        # raw JSON -> template columns, customs cleanup
│   ├── invoice_validator.py  # post-mapping business-rule validation
│   ├── reextraction.py    # single-model extract -> validate -> retry loop
│   ├── orchestrator.py    # LangGraph version: same loop + model-tier selection
│   ├── multi_invoice.py   # splits a combined PDF (several distinct invoices)
│   │                      # into one independently-validated result per invoice
│   └── output_shape.py    # filters the final result JSON (top-level keys /
│                          # header fields / sheet columns) per OUTPUT_CONFIG
│
├── Entry points
│   ├── main.py            # CLI, one PDF
│   ├── batch.py           # CLI, a whole folder (parallel workers + summary CSV)
│   └── api.py             # FastAPI HTTP server (/extract, /extract-batch, /save-pdf)
│
├── templates/             # EXP_TEMPLET.xlsx / IMP_TEMPLET.xlsx (checked-in config, not code)
├── prompts/               # OPTIONAL hand-authored sheets prompts, one per shipment
│                          # type (<type>_sheets_prompt.txt) -- see llm_extract.py's
│                          # has_custom_sheets_prompt. When present, REPLACES the
│                          # dynamically-generated item/extra-sheets prompt for that
│                          # type entirely. Currently has export + import.
├── tests/                 # pytest unit tests, one file per core module
├── tools/                 # benchmark_ocr.py -- dev-only, not imported by the app
│
└── Runtime / data (gitignored — regenerated or externally supplied, not source)
    ├── FILES/             # sample/real client invoices (import/export) — business data
    ├── received_pdfs/     # PDFs uploaded via POST /save-pdf, saved per job_id
    └── results/           # unified output for main.py, batch.py, and api.py:
                           #   validation_success/<pdf>/<pdf>.json
                           #   validation_failed/<pdf>/<pdf>.json
                           # (batch.py also drops results/_summary.csv here)
```

The key organizing idea: **code that defines behavior lives at the root as
flat, single-purpose modules; data that the code reads or writes lives in
its own gitignored folders.** Nothing under "Runtime / data" should ever be
committed — `FILES/`, `received_pdfs/`, and `results/` are all excluded in
`.gitignore`.

## Pipeline flow

```
 PDF file
   │
   ▼
┌─────────────────────────┐
│ scan_validator.py        │  cheap pre-check: tilt angle, clipped table border
│ check_scan_quality()      │  -> refuse early if a table edge is physically cut off
└───────────┬───────────────┘
            ▼
┌─────────────────────────┐
│ pdf_reader.py             │  digital text layer (pdfplumber word coords,
│ extract_text()            │  "│" column markers) OR OCR fallback:
│                           │  Docling (primary) -> RapidOCR (backup)
└───────────┬───────────────┘
            ▼
┌─────────────────────────┐
│ detect_type.py            │  keyword + structural scoring -> import / export / unknown
│ detect_shipment_type()    │  ("unknown" -> caller must pass --type explicitly)
└───────────┬───────────────┘
            ▼
┌─────────────────────────┐
│ schema.py + templates_config.py │  resolve which xlsx template + sheet applies
│ load_template_schema()          │  (ITEM for export, BOE for import)
└───────────┬───────────────┘
            ▼
┌─────────────────────────────────────────────┐
│ orchestrator.py  (default)  OR  reextraction.py (--no-orchestrate) │
│                                                                     │
│  classify_complexity() -> pick model tier (simple/medium/complex)  │
│         │                                                          │
│         ▼                                                          │
│  llm_extract.py: extract_invoice_header() + extract_line_items_chunked() │
│         │  (2 OpenAI calls, both template-driven: header also asks about │
│         │   DRAWBACK/License/AR4/etc.; items asks for the real ITEM/BOE  │
│         │   column names directly, translated back to this pipeline's   │
│         │   internal generic vocabulary right after the call returns)   │
│         ▼                                                          │
│  validate.py: validate_and_coerce()                                │
│         │  (generic JSON -> real template columns, type coercion,  │
│         │   India-customs cleanup rules)                           │
│         ▼                                                          │
│  invoice_validator.py: validate_invoice()                          │
│         │  (business-rule checks: totals reconcile, required       │
│         │   fields present, line items complete, ...)              │
│         ▼                                                          │
│  valid? ──no──> escalate to top-tier model, retry (bounded by      │
│         │        max_retries) with structured feedback on what failed │
│        yes                                                          │
│         ▼                                                          │
│  final JSON: header + sheets + warnings + token_usage + validation  │
└───────────┬───────────────────────────────────────────────────────┘
            ▼
 main.py (writes <out>/validation_success|validation_failed/<file>.json, prints summary)
   or
 batch.py (same, for every PDF under a folder, + results/_summary.csv)
   or
 api.py (same, returned as JSON over HTTP; also saved under
         results/validation_success|validation_failed/ and POSTed to a
         results callback URL)
```

`models.py`'s dynamically-generated Pydantic models are used inside
`validate.py` to strictly enforce column names/types per sheet before a row
is accepted.

## Configuration (`.env`)

All environment variables the app reads are documented in `.env.example`.
Copy it to `.env` and fill in real values — `.env` is gitignored and must
never be committed.

| Variable | Required | Default | Used by |
|---|---|---|---|
| `OPENAI_API_KEY` | **yes** | — | `llm_extract.py` — every extraction call |
| `EXTRACTOR_MODEL` | no | `gpt-5-nano` | `api.py` — model when the orchestrator is off |
| `EXTRACTOR_SAVE_DIR` | no | `./chandru_api` | `api.py` — where results get saved |
| `EXTRACTOR_PDF_DIR` | no | `./received_pdfs` | `api.py` — where `/save-pdf` uploads land |
| `EXTRACTOR_CALLBACK_URL` | no | `http://192.168.2.46:8765/process_invoices/` | `api.py` — `/save-pdf` results callback (empty string disables it) |
| `EXTRACTOR_CALLBACK_API_KEY` | no | — | `api.py` — sent as `X-API-KEY` on the callback |
| `EXTRACTOR_CALLBACK_BEARER_TOKEN` | no | — | `api.py` — sent as `Authorization: Bearer ...` on the callback |

**Security note:** the `OPENAI_API_KEY` currently in `.env` is a live key
that was read in plaintext during this cleanup. As good hygiene, rotate it
in the OpenAI dashboard and update `.env` with the new value — treat any
key that has passed through a file, chat log, or screenshot as potentially
exposed.

## What was cleaned up

- Removed `__pycache__/`, `tests/__pycache__/`, `.pytest_cache/` (regenerated
  automatically — never need to be kept around).
- Removed `templates/~$EXP_TEMPLET.xlsx` (an Excel lock file created while
  that template is open — not a real asset).
- Added `.env.example` (referenced by `README.md` but previously missing).
- Rewrote `.gitignore`: the old one had `.env`, `__pycache__/`, `*.pyc`,
  and both venv-folder patterns listed twice; also added `received_pdfs/`,
  `chandru_api/`, `FILES/`, and the `~$*.xlsx` lock-file pattern, none of
  which were excluded before.

## Left as-is (by request)

- **`FILES/`** (216MB of invoice PDFs) stays in place. It's now gitignored
  so it won't end up in version control if/when this becomes a git repo,
  but it hasn't been moved — see "Suggestions" below if you want to revisit
  that.
- **The 14 root-level `.py` modules stay flat**, not moved into a package
  (e.g. `src/invoice_extractor/`). They're already organized by single
  responsibility (one pipeline stage per file — see the table above); the
  main cost of *not* repackaging is that `main.py`/`batch.py`/`api.py` all
  import siblings directly (`from schema import ...`) rather than through a
  package namespace, which is fine at this size but won't scale cleanly
  much past it.

## Suggestions for later (not done — bigger or riskier changes)

- **Initialize git.** Right now there's no version history at all, which
  means every future change (including further reorganizing) is a one-way
  door. `git init && git add -A && git commit -m "baseline"` costs nothing
  and makes everything after it reversible.
- **Move `FILES/` out of the project folder entirely.** It's real client
  invoice data (company names, commercial terms), not test fixtures or
  code — it doesn't really belong inside a source tree even gitignored.
  A dedicated data location outside `D:\Jeeva\Projects\` would separate
  "code I version and deploy" from "business documents I happen to be
  processing" more cleanly.
- **Package the code** (`src/invoice_extractor/` with `core/`, `llm/`,
  `api/`, `cli/` subpackages + `pyproject.toml`) if this grows past its
  current size or gets installed/deployed as a proper package rather than
  run as scripts out of a folder.
- **Rotate the OpenAI key** (see Security note above).
