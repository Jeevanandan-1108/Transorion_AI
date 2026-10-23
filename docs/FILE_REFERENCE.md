# File Reference — Deep Dive

Companion to [`PROJECT_FLOW.md`](PROJECT_FLOW.md), which explains the *process*
end to end. This document goes file by file: what each one contains, its key
functions, how it's actually used, and the non-obvious design decisions
behind it.

---

## Entry points

### `api.py`

The FastAPI HTTP server — the entry point most integrations use.

- **`lifespan()`** — runs once at startup. Loops `TEMPLATE_PATH.items()`
  (from `templates_config.py`) and calls `load_template_schema()` for each,
  storing the result in the module-level `_schemas` dict. This is why
  editing a template file requires a server **restart** — schemas are
  never re-read per request.
- **`_TYPE_PATTERN`** — a regex built once at import time from
  `sorted(TEMPLATE_PATH)`, used as the `pattern=` on every `type=`/
  `shipment_type=` query parameter across all three endpoints. This is
  what makes the API automatically accept any newly-discovered template
  without a code change.
- **`_process_pdf()`** — the real workhorse, called by all three endpoints.
  Runs `extract_text()` in a worker thread (so a slow OCR pass doesn't
  block the event loop), resolves the template (explicit `type=` or
  `detect_shipment_type()` fallback), calls the orchestrator, and
  assembles the final result dict.
- **`/extract`** — one PDF in, one JSON result out.
- **`/extract-batch`** — multiple PDFs, same pipeline per file, returned as
  a list.
- **`/save-pdf`** — saves uploaded PDFs under `received_pdfs/<job_id>/`
  first, then extracts; also POSTs results to an external callback URL
  (`_send_results_callback`).
- **`/health`** — reports every currently-loaded template and its sheets;
  useful for confirming a new template registered correctly after a
  restart.
- **`_save_result()`** — writes every extraction (success or failure) to
  `results/validation_success|validation_failed/<pdf name>/<pdf name>.json`,
  using the FULL (unshaped) result for the success/failure routing
  decision, but the `output_shape`-filtered result for what's actually
  written to disk.

### `main.py`

CLI for a single PDF. Mirrors `_process_pdf()`'s logic but prints a
human-readable summary instead of just returning JSON — `_SUMMARY_FIELDS`
holds curated columns to print per template, falling back to printing
every non-empty column for a template with no curated entry.

### `batch.py`

CLI for a whole folder. Runs `main.run()` per file with a bounded worker
pool (`--workers`), writes `results/_summary.csv` with one row per PDF
(method, time, item count, cost, warnings). `--type` forces every file in
the batch to one template instead of per-file auto-detection.

### `db_worker.py`

A long-running process that `LISTEN`s on a Postgres channel (`job_queued`)
and also polls every `POLL_SECONDS` as a fallback. On a queued job: claims
it, looks up its documents, resolves file paths relative to `MEDIA_ROOT`,
runs the same `_process_pdf()` pipeline as `api.py` (imported directly from
it), saves results, and POSTs the same callback `api.py`'s `/save-pdf`
would. `lookup_shipment_type()` maps the job's `shipment_type_id` to a
template name via a configurable lookup table, falling back to
auto-detection if unset or unrecognized.

---

## PDF → text

### `invoice_extractor/pdf/reader.py`

- **`extract_text(path, force_ocr=None)`** — the single entry point every
  caller uses. Tries the digital text layer first; falls back to OCR only
  if that comes back too sparse (or `force_ocr=True`).
- **`extract_page_layout_canvas()`** — the core of the "2D layout canvas"
  approach: uses `pdfplumber`'s word-level coordinates to reconstruct the
  page as a grid of text, preserving column alignment with literal
  whitespace runs, and inserting a `│` character at detected column
  boundaries. This is what lets `detect_type.py` and the extraction
  prompts reason about "this value is in the same column as that label"
  without ever seeing the PDF visually.
- OCR fallback: Docling first (better table structure), RapidOCR as
  backup if Docling fails or isn't available.

### `invoice_extractor/pdf/scan_validator.py`

- **`check_scan_quality()`** — Hough-transform-based tilt detection and a
  clipped-table-edge check. Returns `{valid, reasons, skew_deg, clipped,
  clipped_edges, n_lines_used}`. A PDF failing this check still gets
  processed (it doesn't block extraction) — the result just carries
  `scan_warnings` so a human can judge whether a failed extraction was
  caused by a bad scan.

---

## Templates and schema

### `invoice_extractor/schema/templates_config.py`

The single source of truth for "which templates exist."

- **`_EXPLICIT_TEMPLATE_PATH`** / **`_EXPLICIT_ITEM_SHEET`** — hardcoded
  entries for `export`/`import` only, since their filenames
  (`EXP_TEMPLET.xlsx`/`IMP_TEMPLET.xlsx`) don't slugify to anything
  sensible, and their item sheets (`ITEM`/`BOE`) don't follow the
  naming convention new templates use.
- **`_FILENAME_TO_TYPE`** — friendly overrides for new templates whose
  filename would otherwise slugify to something long/ugly (e.g.
  `"Import- LOGYSIS TEMPLATE"` → `logi` instead of
  `import_logysis_template`).
- **`_discover_templates()`** — runs once at import time. Scans
  `templates/*.xlsx`, `*.xls`, `*.csv` (skipping `~$*.xlsx` Excel lock
  files), and for each un-claimed file: derives its type name (friendly
  override, or auto-slugified filename) and calls `_detect_item_sheet()`
  to find its line-items sheet — a sheet literally named `ITEM`/`ITEMS`
  (case-insensitive), or the first sheet if none matches.
- Produces the two module-level dicts everything else imports:
  `TEMPLATE_PATH` (type name → file path) and `ITEM_SHEET_NAME` (type
  name → line-items sheet name).

### `invoice_extractor/schema/schema.py`

Reads a template's own column headers into a schema — the bridge between
"a column exists in an Excel file" and "the extraction pipeline knows
about it."

- **`_MANUAL_ALIASES`** — hand-curated alternate labels per *column name*
  (e.g. `"Item_RITC"` → `["HSN Code", "RITC Code", ...]`). Global, not
  per-template — a column name reused across templates shares its alias
  list automatically.
- **`_MANUAL_DESCRIPTIONS`** — hand-written *meanings* for cryptic columns
  (e.g. `"CTH"` → "Customs Tariff Heading — the HS/tariff classification
  code for this item."). Same global-by-name scoping as aliases.
- **`_MANUAL_DESCRIPTIONS_BY_SHEET`** — the fix for when a column name is
  reused across templates with a *genuinely different* meaning (confirmed
  real cases: `BE_No`, `Doc_Type`, `Notification_No`, `Quantity`). Keyed
  by `(sheet_name, field_name)`, checked *before* the name-only dict.
- **`_build_sheet_entry(headers, sheet_name)`** — builds one sheet's
  `{fields, types, aliases, descriptions, link_field}`. `link_field`
  detection is a simple name match: `item_no`/`item_ser_no`/`sl_no`
  (after lowercasing and stripping spaces).
- **`field_description_text()`** — the one place a field's meaning and its
  alias list get combined into the single string actually shown to the
  LLM. Used identically by `pydantic_models.py`'s model builders,
  `chunked_extraction.py`'s prompt builder, and
  `generate_template_models.py`'s generated-file output.
- **`load_template_schema(path)`** — the public entry point; dispatches to
  `_load_xlsx_schema`/`_load_xls_schema`/`_load_csv_schema` by file
  extension (`.xls` needs `xlrd` since `openpyxl` can't open the legacy
  binary format; `.csv` has no sheets at all, so it returns a single
  entry under the fixed name `"ITEMS"`).

### `invoice_extractor/schema/field_naming.py`

One function: **`to_python_name(real_name, taken=None)`** — turns an
arbitrary real column name into a valid Python identifier (strips/replaces
non-identifier characters, handles a leading digit, dodges Python
keywords, de-duplicates within a `taken` set). Used only by the generator
script, to produce readable attribute names in generated files while
keeping the real column name as that field's `alias`.

### `invoice_extractor/schema/pydantic_models.py`

The single place every Pydantic model in the whole pipeline gets built —
added specifically to stop type-mapping logic from drifting out of sync
across files (it already had, once, before this module existed).

- **`pydantic_type_for(schema_type)`** — `"number"` → `Optional[float |
  str]`; anything else → `Optional[str | int | float]` (deliberately
  lenient both directions: the LLM sometimes returns a number as a quoted
  string, and a text-shaped ID field often comes back as a bare int).
- **`row_model_for_fields()`** — the shared row-model constructor,
  parameterized by `forbid_extra` (True only for validation models, as a
  safety net; False for extraction models, since OpenAI's structured
  outputs already guarantee no extra keys).
- **`build_sheet_models(schema)`** — one *validation* model per sheet,
  covering every column. Never sent to the LLM.
- **`row_model_for_chunk(chunk)`** / **`rows_model_for_chunk()`** — one
  small model per chunk (≤12 fields), wrapped in a `{"rows": [...]}`
  container. This is what's actually sent to OpenAI via `response_format=`.
- **`group_rows_model(chunks, shipment_type)`** — combines several chunks'
  row models into one object (one key per chunk) for the grouped-call
  path.
- **`generated_chunk_models(shipment_type)`** (`@lru_cache`) — imports
  `invoice_extractor.schema.generated.<type>` if it exists and returns its
  `CHUNK_MODELS` dict; `{}` for the overwhelming majority of templates
  with no generated file.

### `invoice_extractor/schema/models.py`

Now a thin layer on top of `pydantic_models.py` (re-exports
`build_sheet_models` for backward compatibility). Its own real content:

- **`validate_row_against_model(row, model_class, sheet_name, row_idx,
  warnings, all_fields)`** — the actual post-extraction validation step.
  Strips any key not in the model (warning logged), validates via Pydantic
  (catching type errors), then **constructs the full row from
  `all_fields`** — every column the template defines, in schema order,
  `""` for anything not populated. A row that's entirely empty (nothing
  filtered, nothing in the raw values) returns `None` and gets dropped.

### `invoice_extractor/schema/generated/*.py`

Not hand-written logic — generated data files, one per template that has
one. Each holds one Pydantic class per chunk (`<Sheet>Chunk<N>Row`), with
a Python-safe attribute name per field, the real column name as that
field's `alias`, and a `description=` combining the field's manual meaning
and alias list. The one thing meant to be hand-edited here is
`description=` text; everything else gets overwritten the next time the
generator runs. A `CHUNK_MODELS` dict at the bottom maps `(sheet, index) →
class`, which is what `pydantic_models.generated_chunk_models()` reads.

---

## Extraction

### `invoice_extractor/detect_type.py`

- **`detect_shipment_type(text)`** — a deterministic, non-LLM classifier.
  Weighted keyword scoring (`_EXPORT_STRONG`/`_WEAK`,
  `_IMPORT_STRONG`/`_WEAK`) plus two structural signals:
  `_origin_destination_signal()` (country-of-origin/destination label
  proximity to "India") and buyer/supplier address-block proximity
  (`_label_proximity_hits()`). Returns `import`/`export`/`unknown` — never
  guesses, and has no concept of any other template. This is why every
  other discovered template must always be requested explicitly via
  `type=`.
- Extensively comment-documented with "confirmed real case" notes — nearly
  every regex/heuristic here exists because a specific real invoice broke
  an earlier, simpler version.

### `invoice_extractor/extraction/llm_extract.py`

The oldest file in the pipeline, carrying the two hand-tuned calls that
predate the chunked/structured-output redesign.

- **`extract_invoice_header()`** — ~30 fixed, universal header concepts.
  Plain JSON mode (`response_format={"type": "json_object"}`), not
  Pydantic. Supports `feedback`/`previous_result` for the retry loop
  (shows the model its own prior answer plus what was wrong).
- **`extract_line_items()`** / **`extract_line_items_chunked()`** — the
  core ~11-15 item fields. `_build_line_items_prompt()` rewrites the
  prompt's generic field names to the template's real column names via
  `_GENERIC_TO_REAL_ITEM_FIELDS` (export/import only — any other template
  keeps the generic wording, since its real data comes from
  `chunked_extraction.py` instead). `extract_line_items_chunked()` adds
  page-splitting for very large tables (`_LINE_ITEMS_CHUNK_PAGE_THRESHOLD`)
  — a different kind of "chunking" than `chunked_extraction.py`'s field
  chunking; this one splits by *pages* of the invoice, not by *columns*.
- **`_translate_real_columns_to_generic()`** / **`_translate_generic_to_real_for_display()`**
  — the bidirectional bridge between the template's real column names and
  the pipeline's internal generic vocabulary, letting the same
  hand-written extraction rules work across templates with different real
  names for the same concept.
- **`apply_header_guardrails()`** / **`apply_line_item_guardrails()`** —
  cross-checks after both passes return: OCR-duplicate-invoice-number
  detection, HSN-code repair, part-number sanity checks.
- **`item_core_real_fields(shipment_type)`** — exposes which real columns
  the core call already owns, so `chunked_extraction.py` can exclude them
  from its own chunking.

### `invoice_extractor/extraction/chunked_extraction.py`

Everything the two core calls don't cover — the long tail.

- **`FieldChunk`** (dataclass) — `sheet`, `link_field`, `fields`,
  `aliases`, `types`, `descriptions`, `index`, `multi_row`.
- **`build_field_chunks(schema, exclude, chunk_size, item_sheet_name)`** —
  splits each sheet's remaining columns into groups of `CHUNK_SIZE` (12).
  Marks a chunk `multi_row=True` only when it belongs to the item sheet
  *and* that sheet has no link column — meaning it must return one row per
  line item, matched back by position instead of by ID.
- **`_build_chunk_prompt()`** / **`_build_group_prompt()`** — builds the
  actual prompt text, with different framing for `multi_row` chunks
  ("return one row per item, never skip one, even an all-null placeholder")
  versus ordinary auxiliary-sheet chunks ("the default, correct answer is
  an empty list").
- **`extract_chunk()`** — one real OpenAI structured-outputs call for one
  chunk. Keeps an empty placeholder row for a `multi_row` chunk's
  null position, so position stays aligned across chunks.
- **`_group_chunk_indices(chunks, max_groups)`** — greedy bin-packing:
  sorts chunks largest-first, always drops the next one into whichever
  bucket currently has the fewest total fields. Only runs when
  `len(chunks) > MAX_LLM_CALLS_PER_TEMPLATE` (5).
- **`extract_chunk_group()`** — one combined call covering several chunks
  (often from different sheets) at once, each kept in its own labeled
  prompt section and its own key in the response.
- **`extract_all_chunks()`** — the top-level entry point: decides
  individual-call vs. grouped-call mode, runs everything concurrently via
  `asyncio.gather`, tracks token usage correctly (once per *real* API
  call, not once per chunk bundled inside a group), and merges results
  back into `{sheet_name: [rows]}` — by link-field value, or by position
  for `multi_row` sheets.

### `invoice_extractor/extraction/orchestrator.py`

The LangGraph state machine — what `api.py` actually calls.

- **`classify_complexity()`** — picks a model tier from page count/text
  density; `_select_model()` maps `(tier, attempt)` to an actual model
  name, always escalating to `TOP_TIER_MODEL` on any retry.
- **`_extract_node()`** — runs the header call, items call, and (on
  attempt 1 only) the chunked long-tail calls; merges everything; calls
  `validate_and_coerce()` and `validate_invoice()`; handles the
  multi-invoice split via `multi_invoice.py` if needed.
- **`_route_after_extract()`** — `END` if valid or retries exhausted;
  otherwise checks whether `build_retry_feedback()` found anything
  actually retryable (a run of only non-retryable errors also ends here,
  rather than looping pointlessly).
- **`_prepare_retry_node()`** — builds the feedback text/structured
  failed-part info, decides whether this retry should also trigger the
  "rows went missing" fine-grained per-page re-extraction.
- **`extract_and_validate_orchestrated()`** — the public entry point,
  same return shape as `reextraction.extract_and_validate()` plus
  `complexity`/`models_used`.

### `invoice_extractor/extraction/reextraction.py`

A parallel, simpler implementation of the same retry policy, against a
single caller-supplied model (no automatic tiering) — used when
`orchestrate=False`. Owns the actual retry *policy* details that
`orchestrator.py`'s docstring explicitly says it reuses: targeted
per-pass retry, the one-time "fine_chunks" completeness retry, and never
looping on a purely non-retryable failure.

### `invoice_extractor/extraction/multi_invoice.py`

- **`is_combined_document(header_raw)`** — detects whether a single PDF
  actually bundles more than one genuinely distinct invoice (as opposed to
  one invoice's line items spanning several pages).
- **`split_header()`** / **`split_items()`** — separate the merged
  header/item data back into one set per invoice.
- **`combine_validations()`** — ANDs each sub-invoice's own
  `ValidationResult` together into one top-level result, so the retry loop
  above doesn't need to know or care that the document was combined.

---

## Validation

### `invoice_extractor/validation/validate.py`

Turns the LLM's generic output into the template's real, final rows.

- **`_clean_invoice_header()`** — normalizes header fields (state code
  shape, currency formatting, RoDTEP Y/N).
- **`_clean_generic_item()`** — per line item: drops subtotal/deduction
  rows, coerces qty/price/total to numbers, cross-checks qty×price against
  the printed total (warning only), resolves part number/model from
  whichever key the LLM used. The drop-check for "a row with nothing
  found" was specifically relaxed to also check for a non-empty
  `_extra_template_fields` — otherwise every row for a template with no
  hand-tuned mapping would get silently dropped.
- **`_map_export_item()`** / **`_map_import_item()`** (hand-tuned) /
  **`_map_generic_item()`** (fallback, just passes through
  `_extra_template_fields` as-is) — the only functions that know a
  template's real column names.
- **`_apply_export_domain_rules()`** / **`_apply_import_domain_rules()`** /
  **`_apply_generic_domain_rules()`** (no-op) — final per-column India-
  customs cleanup, only meaningful once a row is in its final template
  shape.
- **`map_extra_sheets()`** — same `validate_row_against_model()` treatment
  for every auxiliary sheet's rows, using the real column names the LLM
  already returned directly (no hand-written mapping needed there).
- **`validate_and_coerce()`** — the top-level entry point tying all of the
  above together; raises if `shipment_type` isn't a known template
  (checked against `ITEM_SHEET_NAME`, which is guaranteed complete for
  every discovered template).

### `invoice_extractor/validation/invoice_validator.py`

- **`validate_invoice()`** — runs every business rule (arithmetic
  reconciliation, required-field presence, GSTIN/AD-code shape checks) and
  returns a `ValidationResult` (`valid`, `status`, `checks`, with each
  check carrying a `retryable` flag).
- **`build_retry_feedback()`** — turns a failed `ValidationResult` into
  human-readable feedback text the next attempt's prompt can act on.
- **`build_header_failed_part()`** — the structured (not just text)
  version, naming exactly which header fields failed, for
  `extract_invoice_header()`'s `failed_part` parameter.
- **`_ITEM_FIELDS`** — the export/import concept→real-column map used
  specifically for these business-rule checks (separate from, but
  conceptually parallel to, `llm_extract.py`'s own
  `_GENERIC_TO_REAL_ITEM_FIELDS`); `.get()`-safe, so a template with no
  entry here just has those specific checks skipped rather than erroring.

---

## Output and logging

### `invoice_extractor/output_shape.py`

- **`apply_output_shape(result, config)`** — filters the final result dict
  down to `top_level_keys`/`header_fields`/`sheet_fields` named in
  `OUTPUT_CONFIG` (or an external JSON file via `OUTPUT_SHAPE_CONFIG`).
  Every field defaults to `None` (no filtering) — the shipped default is
  the full, untouched result. Explicitly **not** applied to the
  `/save-pdf` results callback or to the internal success/failure routing
  decision — only to what a human (or downstream consumer) actually sees.

### `invoice_extractor/pipeline_log.py`

Pure console-output helpers — `print_pdf_received`,
`print_extraction_result`, `print_llm_input_text`, `print_pydantic_model`.
No effect on any actual result; safe to ignore when reasoning about
behavior.

---

## Tools (not part of the live pipeline)

### `tools/generate_template_models.py`

- **`generate(shipment_type)`** — loads that template's schema, computes
  its chunks via the *exact same* `build_field_chunks()` the runtime uses,
  and writes one `BaseModel` class per chunk to
  `invoice_extractor/schema/generated/<type>.py`, using
  `field_naming.to_python_name()` for attribute names and
  `schema.field_description_text()` for descriptions — the same
  description-combining logic the live pipeline itself uses, so a
  generated file always matches what the dynamic path would have built
  anyway, just reviewable and hand-editable.
- Re-run whenever a template's own columns change; it overwrites the file
  from scratch (field list, types, aliases) but is the one place
  hand-edited `description=` text can get lost on regeneration if not
  reapplied.

### `tools/benchmark_ocr.py`

Dev-only CLI comparing PDFplumber's native text layer, RapidOCR, and
Docker-based OCRmyPDF on one page of one PDF — latency, word/char counts,
confidence. Never imported by `main.py`/`batch.py`/`api.py`; safe to
delete without affecting the product.
