from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from invoice_extractor.schema.schema import load_template_schema

_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def test_export_template_has_item_sheet():
    schema = load_template_schema(_TEMPLATES / "EXP_TEMPLET.xlsx")
    assert "ITEM" in schema
    assert "Item_Ser_No" in schema["ITEM"]["fields"]
    assert "Item_Desc" in schema["ITEM"]["fields"]


def test_import_template_has_boe_not_item():
    # c99366b replaced IMP_TEMPLET.xlsx with the "focus" IMP_TEMPLET.xls; only
    # its BOE sheet has a "from invoice" annotation row, so only BOE is loaded.
    schema = load_template_schema(_TEMPLATES / "IMP_TEMPLET.xls")
    assert "BOE" in schema
    assert "ITEM" not in schema
    assert list(schema) == ["BOE"]
    assert "SL_No" in schema["BOE"]["fields"]


def test_known_manual_aliases_resolve():
    schema = load_template_schema(_TEMPLATES / "EXP_TEMPLET.xlsx")
    aliases = schema["ITEM"]["aliases"]
    assert "HSN Code" in aliases["Item_RITC"]
    assert "Country of Origin" in aliases["Itm_Source_Cntry"]


def test_field_types_inferred():
    schema = load_template_schema(_TEMPLATES / "EXP_TEMPLET.xlsx")
    types = schema["ITEM"]["types"]
    assert types["Item_Qty"] == "number"
    assert types["Item_Desc"] == "string"
    # "Printed" is unannotated in the focus template, so it is no longer loaded;
    # PER is another forced-string ITEM column that still is.
    assert types["PER"] == "string"
