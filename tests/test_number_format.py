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
