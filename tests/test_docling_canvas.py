"""Docling canvas layout: wrapped multi-line cells must not overwrite neighbours."""
from invoice_extractor.pdf.reader import _docling_render_canvas, _docling_split_wrapped


def _w(x0, x1, top, bottom, text):
    return {"x0": x0, "x1": x1, "top": top, "bottom": bottom, "text": text}


def _table_words():
    # One wrapped description cell (3 printed lines, one tall box) between a
    # code column and the price/amount columns, as Docling returns a scan.
    return [
        _w(30, 100, 428, 439, "5191724473-00030"),
        _w(244, 394, 416, 453,
           "Emax 2 R.P. IEC/UL 4000A - M4 Communication Module controller accessory"),
        _w(403, 419, 429, 440, "PC"),
        _w(458, 487, 429, 440, "¥12.39"),
        _w(506, 546, 428, 440, "¥3,717.00"),
        _w(30, 100, 467, 477, "5191717631-00007"),
        _w(244, 394, 455, 490, "Ekip Com Modbus RS-485 E1.2..E6.2 Communication Module accessory"),
        _w(456, 490, 467, 478, "¥113.86"),
        _w(504, 550, 466, 478, "¥34,158.00"),
    ]


def test_wrapped_cell_does_not_overwrite_price_and_amount():
    text = _docling_render_canvas(_table_words(), 5.0, 4.0)
    for expected in ("¥12.39", "¥3,717.00", "¥113.86", "¥34,158.00"):
        assert expected in text
    # Full description survives, only broken across lines.
    assert "Emax 2 R.P." in text and "accessory" in text


def test_price_and_amount_stay_on_same_row_as_codes():
    text = _docling_render_canvas(_table_words(), 5.0, 4.0)
    row = next(r for r in text.splitlines() if "5191724473-00030" in r)
    assert "¥12.39" in row and "¥3,717.00" in row


def test_split_wrapped_gives_each_line_its_own_box_slice():
    words = _table_words()
    out = _docling_split_wrapped(words, 5.0)
    pieces = [w for w in out if w["x0"] == 244 and w["top"] >= 416 and w["bottom"] <= 453]
    assert len(pieces) >= 2
    assert " ".join(p["text"] for p in pieces) == words[1]["text"]
    for a, b in zip(pieces, pieces[1:]):
        assert a["bottom"] <= b["top"] + 1e-6


def test_single_line_items_are_untouched():
    words = [
        _w(10, 100, 10, 21, "Invoice No: 123"),
        _w(200, 260, 10, 21, "2026/08/20"),
        _w(10, 100, 30, 41, "Net weight 5 kg"),
    ]
    assert _docling_split_wrapped(words, 5.0) == words


def test_tall_single_line_title_is_not_split():
    # Large-font title: tall box but its text exactly fills the box width.
    words = [_w(10, 100, 10, 21, "x y z"), _w(10, 100, 40, 51, "x y z"),
             _w(300, 396, 189, 218, "COMMERCIAL INVOICE")]
    out = _docling_split_wrapped(words, 5.0)
    assert [w["text"] for w in out].count("COMMERCIAL INVOICE") == 1


def test_unspaced_tall_text_is_kept_whole():
    words = [_w(0, 50, 0, 11, "a"), _w(0, 50, 20, 31, "b"),
             _w(100, 130, 0, 40, "单一长文本没有空格可以拆分的内容")]
    out = _docling_split_wrapped(words, 5.0)
    assert any(w["text"] == "单一长文本没有空格可以拆分的内容" for w in out)
