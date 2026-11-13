from invoice_extractor.number_format import normalize_decimal_commas as norm

_ITALIAN = "RE3616803 Gruppo Cavi NR 150,000 1,650000 247,50 E8A\nTOTAL 742,50 EUR 742,50\nTax 0,00"


def test_decimal_comma_document_is_rewritten():
    text, changed = norm(_ITALIAN)
    assert changed and "150.000 1.650000 247.50" in text and "742.50" in text


def test_thousands_dots_are_dropped_first():
    text, _ = norm(_ITALIAN + "\nGrand 1.234,50 and 2.500,00")
    assert "1234.50" in text and "2500.00" in text


def test_dot_decimal_document_is_untouched():
    doc = "Qty 2,592 Price 2.07 Amount 5,365.44 Tax 1,000.00 Total 6,365.44"
    assert norm(doc) == (doc, False)


def test_too_few_comma_amounts_is_untouched():
    doc = "Phone 12,34 Total 5,00"
    assert norm(doc) == (doc, False)


def test_each_page_is_judged_on_its_own():
    bar = "=" * 35
    label = f"\n{bar} PAGE 1 OF 2 (a.pdf) {bar}\nCustoms Value: 742.50 EUR weight 11.15 KG\n"
    invoice = f"\n{bar} PAGE 2 OF 2 (a.pdf) {bar}\n" + _ITALIAN + "\n"
    text, changed = norm(label + invoice)
    assert changed and "742.50 EUR weight 11.15 KG" in text and "150.000 1.650000 247.50" in text


# --- real layouts that the old "no point decimals anywhere" rule missed ------

from invoice_extractor.number_format import uses_decimal_commas

_BAR = "=" * 35


def _pages(*pages: str) -> str:
    n = len(pages)
    return "".join(f"\n{_BAR} PAGE {i} OF {n} (a.pdf) {_BAR}\n{p}\n" for i, p in enumerate(pages, 1))


# SAP-style German export: amounts 10.478,22 next to "1.000 EA" (a quantity of
# ONE, printed with a point) and a "7.00" weight.
_AGILENT = (
    "1000 G7980AA 280-DS Mechanical Qualification 90318000 NLR 1.000 EA USD 10.478,22 USD 10.478,22\n"
    "Total Number of Boxes 1 Total Gross Weight in Kgs7.00 Total Amount: USD 10.478,22\n"
)


def test_stray_point_values_do_not_veto_a_decimal_comma_page():
    assert uses_decimal_commas(_AGILENT)
    text, changed = norm(_AGILENT)
    assert changed
    assert "USD 10478.22 USD 10478.22" in text and "Total Amount: USD 10478.22" in text
    assert "1.000 EA" in text and "Kgs7.00" in text  # point values are never touched


def test_a_page_whose_point_amounts_rival_its_comma_amounts_is_left_alone():
    doc = "Amount 1,234.56 Tax 12.50 Total 1,247.06 Ref 10,25 Fee 3,10 Misc 4,20"
    assert norm(doc) == (doc, False)


def test_dates_are_not_counted_as_point_decimals():
    doc = "Date 08.07.2026 Due 20.08.2026 Net 87,70 Gross 526,20 Total 527,20"
    text, changed = norm(doc)
    assert changed and "527.20" in text and "08.07.2026" in text


def test_point_decimal_thousands_amount_keeps_its_comma_on_a_comma_page():
    doc = "Net 87,70 Gross 526,20 Freight 12,00 Customs 1,234.56"
    text, _ = norm(doc)
    assert "1,234.56" in text and "526.20" in text


def test_indian_lakh_grouping_is_not_rewritten():
    doc = "Net 87,70 Gross 526,20 Freight 12,00 Value 51,81,785"
    text, _ = norm(doc)
    assert "51,81,785" in text and "87.70" in text


def test_space_grouped_thousands_are_joined():
    doc = "Item 1 234,56 Item 2 345,60 Total 3 580,16 Tax 0,00"
    text, changed = norm(doc)
    assert changed and "1234.56" in text and "3580.16" in text


def test_four_decimal_unit_price_counts_as_comma_evidence():
    doc = "2,010 2,000 EA RB20X2CF EA 24/08/26 3,6974 7,39\nNet Net Total 7,39"
    text, changed = norm(doc)
    assert changed and "3.6974 7.39" in text


def test_thin_continuation_page_follows_the_document():
    invoice = "Net 87,70 Gross 526,20 Freight 12,00 Total 527,20"
    continuation = "Carried forward 527,20"
    text, changed = norm(_pages(invoice, continuation))
    assert changed and "Carried forward 527.20" in text


def test_thin_page_of_a_point_decimal_document_is_not_converted():
    invoice = "Net 87.70 Gross 526.20 Freight 12.00 Total 1,247.06"
    other = "Phone 12,50 ext"
    doc = _pages(invoice, other)
    assert norm(doc) == (doc, False)


def test_mixed_format_invoice_page_converts_without_changing_point_values():
    page = "Price 75,23 601.84\nNet weight 0,001 KG/ PC\nItems total 601,84\nVAT 0% Export 601.84 0,00"
    text, changed = norm(page)
    assert changed and "Price 75.23 601.84" in text and "Net weight 0.001 KG" in text and "Items total 601.84" in text
