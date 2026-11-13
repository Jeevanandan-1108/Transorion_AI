"""Run every PDF under a folder against SEVERAL templates and report what came
out -- per-template validation status, and every row's part numbers.

    python tools/overall_test.py --root "FILES/IMPORT" --out overall_test_result --sample 20

Each (pdf, template) pair is one full pipeline run, so the JSON written under
<out>/<template>/validation_{success,failed}/ is exactly what production would
have written for that template. The PDF's text is extracted ONCE and reused
across that same file's other template runs (OCR is deterministic and
template-independent); only the LLM extraction repeats per template.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import csv
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main as pipeline_main  # noqa: E402
from main import run  # noqa: E402
from invoice_extractor.schema.templates_config import (  # noqa: E402
    ITEM_SHEET_NAME, TEMPLATE_PATH, TYPE_ALIASES,
)
from invoice_extractor.part_numbers import all_part_numbers  # noqa: E402

DEFAULT_TEMPLATES = [
    "SEZ import Template",
    "SEZ export Template",
    "Focus import Template",
    "Focus export Template",
]


def _resolve(name: str) -> str:
    """Friendly template name -> the canonical type the pipeline dispatches on."""
    return TYPE_ALIASES.get(name, name)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


# -- one text extraction per PDF, shared by that PDF's template runs ---------
_TEXT_CACHE: dict[tuple[str, object], tuple] = {}
_REAL_EXTRACT_TEXT = pipeline_main.extract_text


def _cached_extract_text(pdf_path, force_ocr=None):
    key = (str(pdf_path), force_ocr)
    if key not in _TEXT_CACHE:
        _TEXT_CACHE[key] = _REAL_EXTRACT_TEXT(pdf_path, force_ocr=force_ocr)
    return copy.deepcopy(_TEXT_CACHE[key])


def _invoice_blocks(result: dict) -> list[dict]:
    if "invoices" in result:
        return result["invoices"]
    return [{"header": result.get("header", {}), "sheets": result.get("sheets", {})}]


def _item_rows(block: dict, resolved_type: str) -> list[dict]:
    sheets = block.get("sheets") or {}
    rows = sheets.get(ITEM_SHEET_NAME.get(resolved_type, ""))
    if rows is None:  # unexpected sheet name -- fall back to the longest list
        rows = max((v for v in sheets.values() if isinstance(v, list)), key=len, default=[])
    return [r for r in rows if isinstance(r, dict)]


def _description(row: dict) -> str:
    """The row's description, whatever this template calls the column."""
    for key in ("product_description", "Item_Desc1", "Item_Description", "description"):
        if row.get(key):
            return str(row[key])
    for key, value in row.items():
        if "desc" in key.lower() and value:
            return str(value)
    return ""


async def _one(pdf: Path, rel: str, template: str, out_dir: Path, sem, idx: int, total: int):
    resolved = _resolve(template)
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", rel.replace("/", "__")) + ".json"
    out_path = out_dir / _slug(template) / name
    label = f"[{idx}/{total}] {template} | {pdf.name}"
    t0 = time.perf_counter()
    async with sem:
        try:
            result = await run(str(pdf), "gpt-6-luna", str(out_path),
                               shipment_type=resolved, max_retries=1)
        except Exception as e:  # noqa: BLE001 -- recorded in the report, never fatal
            print(f"{label} FAILED: {type(e).__name__}: {e}", flush=True)
            return {
                "summary": {
                    "file": rel, "template": template,
                    "status": f"FAILED: {type(e).__name__}: {e}", "valid": "",
                    "item_rows": 0, "rows_without_part_number": "",
                    "validation_errors": "", "error_rules": "", "cost_inr": 0.0,
                    "seconds": round(time.perf_counter() - t0, 1), "method": "",
                },
                "parts": [], "issues": [],
            }

    seconds = round(time.perf_counter() - t0, 1)
    parts: list[dict] = []
    issues: list[dict] = []
    n_rows = n_empty = 0
    for block in _invoice_blocks(result):
        for n, row in enumerate(_item_rows(block, resolved), start=1):
            n_rows += 1
            pns = all_part_numbers(row)
            if not pns:
                n_empty += 1
            parts.append({
                "file": rel, "template": template, "row": n,
                "part_number": row.get("part_number") or "",
                "part_numbers": " | ".join(pns),
                "count": len(pns),
                "order_number": row.get("order_number") or "",
                "description": _description(row)[:80],
            })

    validation = result.get("validation") or {}
    for err in validation.get("errors", []):
        issues.append({"file": rel, "template": template, "rule": err.get("rule", ""),
                       "item": err.get("item", ""), "message": err.get("message", "")})
    rules = sorted({e.get("rule", "") for e in validation.get("errors", [])})
    usage = result.get("token_usage") or {}
    status = "ok" if result.get("is_invoice", True) else "not an invoice"
    print(f"{label} -> {status}, valid={validation.get('valid')}, {n_rows} rows, "
          f"{n_empty} without a part number, {seconds}s", flush=True)
    return {
        "summary": {
            "file": rel, "template": template, "status": status,
            "valid": validation.get("valid", ""), "item_rows": n_rows,
            "rows_without_part_number": n_empty,
            "validation_errors": len(validation.get("errors", [])),
            "error_rules": ", ".join(rules), "cost_inr": usage.get("cost_inr", 0.0),
            "seconds": seconds, "method": result.get("extraction_method", ""),
        },
        "parts": parts, "issues": issues,
    }


def _pick(pdfs: list[Path], root: Path, sample: int) -> list[Path]:
    """A spread sample: take from the vendor folders round-robin, so one large
    folder cannot swallow the whole quota."""
    if not sample or sample >= len(pdfs):
        return pdfs
    by_folder: dict[str, list[Path]] = defaultdict(list)
    for p in pdfs:
        by_folder[p.relative_to(root).parts[0]].append(p)
    picked: list[Path] = []
    depth = 0
    while len(picked) < sample:
        added = False
        for folder in sorted(by_folder):
            if depth < len(by_folder[folder]):
                picked.append(by_folder[folder][depth])
                added = True
                if len(picked) == sample:
                    break
        if not added:
            break
        depth += 1
    return picked


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_report(out_dir: Path, args, pdfs, pairs, elapsed, summary, issues) -> None:
    per_template: dict[str, dict] = defaultdict(
        lambda: {"runs": 0, "valid": 0, "failed": 0, "rows": 0, "empty_pn": 0, "cost": 0.0})
    for s in summary:
        t = per_template[s["template"]]
        t["runs"] += 1
        t["valid"] += 1 if s["valid"] is True else 0
        t["failed"] += 1 if str(s["status"]).startswith("FAILED") else 0
        t["rows"] += s["item_rows"] or 0
        t["empty_pn"] += s["rows_without_part_number"] or 0
        t["cost"] += s["cost_inr"] or 0.0

    rule_counts: dict[str, int] = defaultdict(int)
    for i in issues:
        rule_counts[i["rule"]] += 1

    lines = [
        "# Overall test result", "",
        f"- source: `{args.root}` ({len(pdfs)} PDFs)",
        f"- templates: {', '.join(args.templates)}",
        f"- runs: {len(pairs)} in {elapsed}s", "",
        "## Per template", "",
        "| Template | Runs | Validation passed | Crashed | Item rows | Rows with NO part number | Cost (Rs) |",
        "|---|---|---|---|---|---|---|",
    ]
    for t, v in per_template.items():
        lines.append(f"| {t} | {v['runs']} | {v['valid']} | {v['failed']} | {v['rows']} | "
                     f"{v['empty_pn']} | {round(v['cost'], 2)} |")
    lines += ["", "## Validation errors by rule", "", "| Rule | Count |", "|---|---|"]
    for rule, n in sorted(rule_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {rule} | {n} |")
    lines += ["",
              "Files: `_summary.csv` (one row per pdf x template), `_part_numbers.csv` "
              "(one row per line item), `_validation_issues.csv`.", ""]
    (out_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


async def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="FILES/IMPORT")
    ap.add_argument("--out", default="overall_test_result")
    ap.add_argument("--templates", nargs="*", default=DEFAULT_TEMPLATES)
    ap.add_argument("--sample", type=int, default=0, help="0 = every PDF")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    for name in args.templates:
        if _resolve(name) not in TEMPLATE_PATH:
            sys.exit(f"unknown template {name!r}; known: {', '.join(sorted(TEMPLATE_PATH))}")

    pipeline_main.extract_text = _cached_extract_text

    root, out_dir = Path(args.root), Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    pdfs = sorted({*root.rglob("*.pdf"), *root.rglob("*.PDF")})
    pdfs = _pick(pdfs, root, args.sample)
    pairs = [(p, p.relative_to(root).as_posix(), t) for p in pdfs for t in args.templates]
    print(f"[info] {len(pdfs)} PDF(s) x {len(args.templates)} template(s) = {len(pairs)} runs",
          flush=True)

    sem = asyncio.Semaphore(args.workers)
    t0 = time.perf_counter()
    results = await asyncio.gather(*[
        _one(pdf, rel, tpl, out_dir, sem, i + 1, len(pairs))
        for i, (pdf, rel, tpl) in enumerate(pairs)
    ])
    elapsed = round(time.perf_counter() - t0, 1)

    summary = [r["summary"] for r in results]
    parts = [p for r in results for p in r["parts"]]
    issues = [i for r in results for i in r["issues"]]

    if summary:
        _write_csv(out_dir / "_summary.csv", summary, list(summary[0]))
    _write_csv(out_dir / "_part_numbers.csv", parts,
               ["file", "template", "row", "part_number", "part_numbers", "count",
                "order_number", "description"])
    _write_csv(out_dir / "_validation_issues.csv", issues,
               ["file", "template", "rule", "item", "message"])
    _write_report(out_dir, args, pdfs, pairs, elapsed, summary, issues)

    print(f"\n[done] {len(pairs)} runs in {elapsed}s -> {out_dir}/REPORT.md", flush=True)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(_main())
