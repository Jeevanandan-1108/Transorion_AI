"""
templates_config.py
--------------------
Single source of truth for "which template/sheet applies to which
shipment type" -- previously this {"export": ..., "import": ...} mapping
was copy-pasted independently in main.py, api.py, and validate.py, which
is exactly the kind of duplication this project is otherwise trying to
avoid. Everything that needs it now imports from here instead.

Dynamic template discovery: "export" and "import" keep their historical,
explicit filenames and item-sheet names below (so nothing about their
behavior changes). Any OTHER .xlsx file dropped into templates/ is picked
up automatically -- no code change needed -- and registered under a type
name derived from its own filename (see _slugify). Its line-items sheet is
whichever sheet is literally named "ITEMS" (case-insensitive), or the
workbook's first sheet if no sheet is named that -- this is a NAMING
CONVENTION new template authors must follow, since there's no reliable
way to auto-detect "the line-items sheet" from column names alone (most
auxiliary sheets share the same link_field as the main item sheet, see
schema.py).
"""
from __future__ import annotations
import re
from pathlib import Path

import openpyxl

# __file__ is now invoice_extractor/schema/templates_config.py; the actual
# templates/ directory stays at the project root (it's data, not moved as
# part of the invoice_extractor package restructure), three levels up.
TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"

# The two original templates keep their historical filenames and item-sheet
# names exactly as before -- export's EXP_TEMPLET.xlsx calls its line-item
# sheet "ITEM"; import's IMP_TEMPLET.xlsx has no "ITEM" sheet at all and
# calls its line-item sheet "BOE" (Bill of Entry) instead. Neither follows
# the "ITEMS" naming convention new templates use, so both are grandfathered
# in explicitly rather than relying on auto-detection.
_EXPLICIT_TEMPLATE_PATH: dict[str, Path] = {
    "export": TEMPLATES_DIR / "EXP_TEMPLET.xlsx",
    "import": TEMPLATES_DIR / "IMP_TEMPLET.xlsx",
}
_EXPLICIT_ITEM_SHEET: dict[str, str] = {"export": "ITEM", "import": "BOE"}


def _slugify(stem: str) -> str:
    """Turns an arbitrary filename stem into a URL/CLI-safe type name --
    e.g. "Cafeteria Template.xlsx" -> "cafeteria_template"."""
    slug = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    return slug or stem.lower()


def _detect_item_sheet(path: Path) -> str:
    """The sheet literally named "ITEMS" (case-insensitive) if present,
    else the workbook's first sheet -- see the module docstring."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for name in wb.sheetnames:
            if name.strip().upper() == "ITEMS":
                return name
        return wb.sheetnames[0]
    finally:
        wb.close()


def _discover_templates() -> tuple[dict[str, Path], dict[str, str]]:
    template_path = dict(_EXPLICIT_TEMPLATE_PATH)
    item_sheet_name = dict(_EXPLICIT_ITEM_SHEET)

    claimed_paths = set(template_path.values())
    if not TEMPLATES_DIR.exists():
        return template_path, item_sheet_name

    # Excel lock files (e.g. "~$EXP_TEMPLET.xlsx", created while a template
    # is open in Excel) must be skipped -- they're not real workbooks and
    # openpyxl can't read them.
    for path in sorted(TEMPLATES_DIR.glob("*.xlsx")):
        if path in claimed_paths or path.name.startswith("~$"):
            continue
        type_name = _slugify(path.stem)
        template_path[type_name] = path
        item_sheet_name[type_name] = _detect_item_sheet(path)

    return template_path, item_sheet_name


TEMPLATE_PATH, ITEM_SHEET_NAME = _discover_templates()
