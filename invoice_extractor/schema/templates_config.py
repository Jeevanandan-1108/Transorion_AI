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

_CSV_SHEET_NAME = "ITEMS"

_FILENAME_TO_TYPE: dict[str, str] = {
    "ExportXLSXTemplate": "Logysis export Template",
    "iCaffeExcel_Export": "iCaffe export Template",
    "Import- LOGYSIS TEMPLATE": "Logysis import Template",
    "ImportExcel (3)": "iCaffe import Template",

    "MASTERS DETAILS": "masters_details",

}


def _slugify(stem: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    return slug or stem.lower()


_ITEM_SHEET_NAMES = ("ITEM", "ITEMS")


def _detect_item_sheet(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _CSV_SHEET_NAME
    if suffix == ".xls":
        import xlrd
        wb = xlrd.open_workbook(str(path))
        for name in wb.sheet_names():
            if name.strip().upper() in _ITEM_SHEET_NAMES:
                return name
        return wb.sheet_names()[0]
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for name in wb.sheetnames:
            if name.strip().upper() in _ITEM_SHEET_NAMES:
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

    paths = sorted(
        p for pattern in ("*.xlsx", "*.xls", "*.csv")
        for p in TEMPLATES_DIR.glob(pattern)
    )
    for path in paths:
        if path in claimed or path.name.startswith("~$"):
            continue
        type_name = _FILENAME_TO_TYPE.get(path.stem, _slugify(path.stem))
        template_path[type_name] = path
        item_sheet_name[type_name] = _detect_item_sheet(path)

    return template_path, item_sheet_name


TEMPLATE_PATH, ITEM_SHEET_NAME = _discover_templates()