from __future__ import annotations

from decimal import Decimal as D

from invoice_extractor.charges import (
    charge_candidates,
    closest_reconciliation,
    find_charge_lines,
    is_charge_name,
    reconcile,
)


def _amounts(text: str) -> list[D]:
    return [amount for _, amount in find_charge_lines(text)]


def test_add_and_less_lines_are_signed_and_a_label_ending_in_colon_dash_stays_positive():
    text = (
        "LESS TRADE DISCOUNT 0.25% IN EURO:-   │   119.05\n"
        "ADD  AIR FREIGHT  50% IN EURO:-       │   1663.69\n"
    )
    assert _amounts(text) == [D("-119.05"), D("1663.69")]


def test_taxes_customs_and_european_labels_are_charges():
    text = "\n".join([
        "IGST @ 18%            │    1,800.00",
        "Customs   Clearance   │   55.00  EUR",
        "MwSt 19%              │      204.25",
        "Versandkosten         │       12.50",
        "Frais de port         │       30.00",
        "Spese di trasporto    │       15.00",
        "Portes                │       20.00",
        "Remise 5%             │      -50.00",
        "Rabatt                │       10.00",
    ])
    assert _amounts(text) == [
        D("1800.00"), D("55.00"), D("204.25"), D("12.50"), D("30.00"), D("15.00"), D("20.00"),
        D("-50.00"), D("-10.00"),
    ]


def test_unsigned_round_off_is_tried_both_ways_and_a_printed_sign_is_kept():
    assert _amounts("Round Off   │   0.48") == [D("0.48"), D("-0.48")]
    assert _amounts("Round off (-)   │   0.40") == [D("-0.40")]
    assert _amounts("Rounding   │   -0.40") == [D("-0.40")]


def test_whole_amount_counts_only_next_to_a_currency():
    assert _amounts("Freight   │   USD 250") == [D("250")]
    assert _amounts("Freight: 250/-") == [D("250")]
    assert _amounts("Customs Clearance   55 EUR") == [D("55")]
    assert _amounts("Freight   250") == []


def test_codes_ids_and_dates_are_never_read_as_charges():
    text = "\n".join([
        "Zolltarif-Nr.: 39269097",
        "VAT-ID:              DE304456151",
        "GST NO: 33AAAFH0373B1Z6 │ CUS:No.33105",
        "Delivery note No. 308114   (19.08.2026)",
        "Tax free export into third country according to § 4 No. 1a",
        "IBAN DE12 3456 7890 1234 5678 90 bank charges",
    ])
    assert find_charge_lines(text) == []


def test_item_row_whose_description_holds_a_charge_word_is_not_a_charge():
    assert find_charge_lines("7  │ HEAVY DUTY FILTER   │ 10 │ 5.00 │ 50.00") == []


def test_charge_printed_with_its_base_amount_is_still_a_charge():
    assert _amounts("IGST 18% on 10,000.00   │   1,800.00") == [D("1800.00")]


def test_agent_commission_is_not_a_charge():
    assert find_charge_lines("FOREIGN   AGENT  COMM   8% IN EURO:3933.04") == []


def test_extracted_tax_amount_counts_only_when_the_text_prints_it():
    table = "Taxable Value │ IGST Rate │ IGST Amount\n10,000.00 │ 18% │ 1,800.00\n"
    assert charge_candidates(table, {"tax_amount": 1800.0}) == [("tax_amount", D("1800"))]
    assert charge_candidates(table, {"tax_amount": 1900.0}) == []
    assert charge_candidates("", {"tax_amount": 1800.0}) == []


def test_extracted_tax_amount_is_not_counted_twice():
    assert len(charge_candidates("IGST @ 18%  │  1,800.00", {"tax_amount": "1,800.00"})) == 1


def test_reconcile_picks_the_lines_that_close_the_gap():
    lines = [("Packing", D("25.00")), ("Customs Clearance", D("55.00")), ("Mahngebühr", D("5.00"))]
    assert reconcile(D("6363.00"), D("6443.00"), lines, D("0.05")) == lines[:2]
    assert reconcile(D("6363.00"), D("6500.00"), lines, D("0.05")) is None


def test_reconcile_gives_up_beyond_the_cap():
    lines = [(f"Fee {i}", D(i)) for i in range(1, 14)]
    assert reconcile(D("100"), D("101"), lines, D("0.05")) is None


def test_closest_reconciliation_counts_the_printed_charges_in():
    lines = [("Packing", D("25.00")), ("Customs Clearance", D("55.00"))]
    assert closest_reconciliation(D("6363.00"), D("6443.00"), lines) == 0
    assert closest_reconciliation(D("6400.00"), D("6443.00"), lines) == D("12.00")
    assert closest_reconciliation(D("6363.00"), D("6443.00"), []) == D("80.00")


def test_charge_names():
    for name in ("FREIGHT", "Air Freight", "Customs Clearance", "Freight & Insurance", "IGST @ 18%",
                 "Versandkosten", "Bank Charges", "Add: Freight", "Round Off"):
        assert is_charge_name(name), name
    for name in ("Freight container lock", "HEAVY DUTY FILTER", "PACKING SEAL", "TAX INVOICE", "Total", ""):
        assert not is_charge_name(name), name
