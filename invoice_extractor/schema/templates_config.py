"""
templates_config.py
--------------------
Single source of truth for "which template/sheet applies to which
shipment type". export/import keep their historical hardcoded filenames;
any OTHER .xlsx dropped into templates/ is discovered automatically and
registered under a --type/`type=` name of its own.
"""
from __future__ import annotations
import re
from pathlib import Path

import openpyxl

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"

_EXPLICIT_TEMPLATE_PATH: dict[str, Path] = {
    "export": TEMPLATES_DIR / "EXP_TEMPLET.xlsx",
    "import": TEMPLATES_DIR / "IMP_TEMPLET.xlsx",
}
_EXPLICIT_ITEM_SHEET: dict[str, str] = {"export": "ITEM", "import": "BOE"}

# Friendly --type names for the new files you dropped into templates/ --
# auto-slugifying the raw filename would otherwise give ugly names (e.g.
# "Import- LOGYSIS TEMPLATE.xlsx" -> "import_logysis_template"). Map each
# filename's exact stem (no .xlsx) to the name you want here.
_FILENAME_TO_TYPE: dict[str, str] = {
    "ExportXLSXTemplate": "export_xlsx",
    "iCaffeExcel_Export": "icaffe_export",
    "Import- LOGYSIS TEMPLATE": "logysis_import",
    "ImportExcel (3)": "import_excel_3",
    "MATERS DETAILS": "masters_details",
}


def _slugify(stem: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    return slug or stem.lower()


def _detect_item_sheet(path: Path) -> str:
    """The sheet literally named "ITEMS" (case-insensitive) if present,
    else the workbook's first sheet."""
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
    claimed = set(template_path.values())

    if not TEMPLATES_DIR.exists():
        return template_path, item_sheet_name

    for path in sorted(TEMPLATES_DIR.glob("*.xlsx")):
        if path in claimed or path.name.startswith("~$"):
            continue  # already claimed above, or an open-in-Excel lock file
        type_name = _FILENAME_TO_TYPE.get(path.stem, _slugify(path.stem))
        template_path[type_name] = path
        item_sheet_name[type_name] = _detect_item_sheet(path)

    return template_path, item_sheet_name


TEMPLATE_PATH, ITEM_SHEET_NAME = _discover_templates()