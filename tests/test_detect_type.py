from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from invoice_extractor.detect_type import (
    _BUYER_LABELS,
    _SUPPLIER_LABELS,
    _COLUMN_BOUNDARY_MARKER as C,
    _label_positions,
    _label_proximity_hits,
    detect_shipment_type,
)


def test_gst_supply_type_expwp_detected_as_export():
    text = "Tax Scheme  Supply Type  Reverse Charge  Doc Type Code\nGST   EXPWP   N   INV\n"
    result = detect_shipment_type(text)
    assert result.shipment_type == "export"
    assert result.signal == "keyword"


def test_export_keywords_detected():
    text = "SHIPPING BILL NO. 1234567 dated 01-01-2026\nLET EXPORT ORDER granted\nExporter: ACME India Pvt Ltd"
    result = detect_shipment_type(text)
    assert result.shipment_type == "export"


def test_import_keywords_detected():
    text = "BILL OF ENTRY NO. 9988776 dated 01-01-2026\nInto Bond clearance\nImporter: ACME India Pvt Ltd"
    result = detect_shipment_type(text)
    assert result.shipment_type == "import"


def test_neutral_text_is_unknown():
    text = "Thank you for your business. Please remit payment within 30 days."
    result = detect_shipment_type(text)
    assert result.shipment_type == "unknown"


def test_structural_signal_corroborates_export():
    text = (
        "COMMERCIAL INVOICE\n"
        "Country of Origin: India\n"
        "Country of Destination: Germany\n"
        "Shipping Bill No. 55512345\n"
    )
    result = detect_shipment_type(text)
    assert result.shipment_type == "export"
    assert result.signal == "both"


def _two_column(*rows: tuple[str, str]) -> str:
    return "\n".join(f"        {left:<70}{C}{' ' * 70}{right}" for left, right in rows)


def test_supplier_side_ignores_buyer_column_india_on_shared_rows():
    text_lower = _two_column(
        ("seller/ supplier:", "zip code 700054"),
        ("hyve solutions europe limited:", "india"),
        ("technology park, telford", "gstn: 19aaoca5897l1zn"),
        ("shropshire tf3 3ah gb", ""),
    )
    buyer_starts = [s for s, _ in _label_positions(text_lower, _BUYER_LABELS)]
    supplier_starts = [s for s, _ in _label_positions(text_lower, _SUPPLIER_LABELS)]
    supplier_hits = _label_proximity_hits(text_lower, _SUPPLIER_LABELS, other_side_starts=buyer_starts)
    assert supplier_hits == 0


def test_buyer_side_still_finds_its_own_india_in_two_column_layout():
    text_lower = _two_column(
        ("seller/ supplier:", "bill to:"),
        ("hyve solutions europe limited:", "amazon data services india pvt ltd"),
        ("technology park, telford", "kolkata, west bengal"),
        ("shropshire tf3 3ah gb", "india"),
    )
    supplier_starts = [s for s, _ in _label_positions(text_lower, _SUPPLIER_LABELS)]
    buyer_starts = [s for s, _ in _label_positions(text_lower, _BUYER_LABELS)]
    buyer_hits = _label_proximity_hits(text_lower, _BUYER_LABELS, other_side_starts=supplier_starts)
    assert buyer_hits == 1


def test_single_column_multiline_address_still_matches():
    text_lower = (
        "buyer:\n"
        "acme imports pvt ltd\n"
        "123 msg road\n"
        "chennai, tamil nadu\n"
        "india\n"
    )
    buyer_hits = _label_proximity_hits(text_lower, _BUYER_LABELS)
    assert buyer_hits == 1


def test_label_and_its_own_value_across_a_pipe_on_the_same_row_still_matches():
    text_lower = f"        ship to messrs:{C}lenovo india pvt ltd.{C}fcr#\n"
    buyer_hits = _label_proximity_hits(text_lower, _BUYER_LABELS)
    assert buyer_hits == 1


def test_label_word_embedded_inside_an_unrelated_value_is_not_a_hit():
    text_lower = (
        f"        delivery terms:{C}fca seller' premises{C}"
        f"stt global data centres india private limited\n"
    )
    supplier_hits = _label_proximity_hits(text_lower, _SUPPLIER_LABELS)
    assert supplier_hits == 0


def test_ship_from_to_india_route_phrase_is_not_a_supplier_india_hit():
    text_lower = "ship from: changzhou,cn to india\n"
    supplier_hits = _label_proximity_hits(text_lower, _SUPPLIER_LABELS)
    assert supplier_hits == 0


def test_buyers_order_no_reference_field_is_not_treated_as_a_buyer_label():
    text_lower = "buyer's order no. & date: po-84920 dated 01-01-2026\nsupplier: acme exports, chennai, india\n"
    positions = _label_positions(text_lower, _BUYER_LABELS)
    assert positions == []
