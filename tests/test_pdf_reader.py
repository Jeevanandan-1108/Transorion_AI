from __future__ import annotations

from unittest.mock import patch

from invoice_extractor.pdf.reader import (
    _dedupe_overlapping_chars,
    _looks_garbled,
    _word_quality_ratio,
    _maybe_recover_garbled_ocr,
)


def _char(text, top, x0, height=5.16):
    return {"text": text, "top": top, "bottom": top + height, "x0": x0}


def test_tight_space_does_not_eat_the_following_letter():
    chars = [
        _char("S", top=97.36, x0=19.56),
        _char("H", top=97.36, x0=22.32),
        _char("I", top=97.36, x0=25.69),
        _char("P", top=97.36, x0=27.11),
        _char(" ", top=97.36, x0=29.87),
        _char("F", top=97.36, x0=30.83),
        _char("R", top=97.36, x0=33.34),
        _char("O", top=97.36, x0=36.22),
        _char("M", top=97.36, x0=39.58),
    ]
    kept = _dedupe_overlapping_chars(chars)
    assert "".join(c["text"] for c in kept) == "SHIP FROM"


def test_genuine_duplicate_glyph_still_removed():
    chars = [
        _char("i", top=100.0, x0=50.0, height=5.26),
        _char("i", top=100.4, x0=50.2, height=6.89),
    ]
    kept = _dedupe_overlapping_chars(chars)
    assert len(kept) == 1


def test_two_distinct_letters_far_enough_apart_both_kept():
    chars = [
        _char("A", top=100.0, x0=50.0),
        _char("B", top=100.0, x0=55.0),
    ]
    kept = _dedupe_overlapping_chars(chars)
    assert len(kept) == 2


_REAL_PROSE_SAMPLE = """
COMMERCIAL INVOICE
Invoice No: 455024201          Issued Date: 14-July-2026
Invoice to: Aptiv Components India Private Ltd.
Unit 1, S.No. 19, SH 48
VARANAVASI VILLAGE & KUNNAVAKKAM VILLAGE
KANCHIPURAM 631604 IN
Item  Description        Part Name      Qty   Unit Price   Total
1     8PGU1592-A          Core Anvil     1     9.5          9.5
"""

_SHREDDED_SAMPLE = """
N                    £
Bed 0
:                 =301
0              W
1 Y
I
]
00A 8
S
V
X
6 0
A
0 1             T7RW1
E
X
d
II              9 1 wnow 0 0
"""


def test_word_quality_ratio_scores_real_prose_highly():
    count, ratio = _word_quality_ratio(_REAL_PROSE_SAMPLE)
    assert count >= 20
    assert ratio > 0.8


def test_word_quality_ratio_scores_shredded_ocr_low():
    count, ratio = _word_quality_ratio(_SHREDDED_SAMPLE)
    assert ratio < 0.5


def test_looks_garbled_false_for_real_prose():
    assert _looks_garbled(_REAL_PROSE_SAMPLE) is False


def test_looks_garbled_true_for_shredded_ocr():
    assert _looks_garbled(_SHREDDED_SAMPLE) is True


def test_looks_garbled_false_when_too_little_text_to_judge():
    assert _looks_garbled("N d 0 A") is False


def test_maybe_recover_garbled_ocr_skips_clean_text():
    with patch("invoice_extractor.pdf.reader._ocr_best_rotation") as mock_recover:
        text, method, ocr_time = _maybe_recover_garbled_ocr(
            "fake.pdf", _REAL_PROSE_SAMPLE, "ocrmypdf_docker", 5.0,
        )
    mock_recover.assert_not_called()
    assert text == _REAL_PROSE_SAMPLE
    assert method == "ocrmypdf_docker"
    assert ocr_time == 5.0


def test_maybe_recover_garbled_ocr_adopts_better_recovery():
    with patch("invoice_extractor.pdf.reader._ocr_best_rotation", return_value=(_REAL_PROSE_SAMPLE, 0.9, 12.0)):
        text, method, ocr_time = _maybe_recover_garbled_ocr(
            "fake.pdf", _SHREDDED_SAMPLE, "ocrmypdf_docker", 5.0,
        )
    assert text == _REAL_PROSE_SAMPLE
    assert method == "ocrmypdf_docker+rotation_recovered"
    assert ocr_time == 17.0


def test_maybe_recover_garbled_ocr_keeps_original_when_recovery_no_better():
    with patch("invoice_extractor.pdf.reader._ocr_best_rotation", return_value=(_SHREDDED_SAMPLE, 0.1, 12.0)):
        text, method, ocr_time = _maybe_recover_garbled_ocr(
            "fake.pdf", _SHREDDED_SAMPLE, "ocrmypdf_docker", 5.0,
        )
    assert text == _SHREDDED_SAMPLE
    assert method == "ocrmypdf_docker"
    assert ocr_time == 17.0


def test_check_scan_quality_warns_when_the_check_itself_fails(monkeypatch, caplog):
    import logging
    import invoice_extractor.pdf.reader as pdf_reader

    def boom(*a, **k):
        raise TypeError("simulated OpenCV incompatibility")

    monkeypatch.setattr(pdf_reader.scan_validator, "validate_pdf", boom)
    with caplog.at_level(logging.WARNING, logger="invoice_extractor"):
        assert pdf_reader.check_scan_quality("whatever.pdf") is None
    assert any("clipped-table check SKIPPED" in r.getMessage() for r in caplog.records)


def test_detect_page_skew_warns_when_it_fails(monkeypatch, caplog):
    import logging
    import invoice_extractor.pdf.reader as pdf_reader

    def boom(*a, **k):
        raise TypeError("simulated OpenCV incompatibility")

    monkeypatch.setattr(pdf_reader.scan_validator, "validate_pdf", boom)
    with caplog.at_level(logging.WARNING, logger="invoice_extractor"):
        assert pdf_reader._detect_page_skew("whatever.pdf", 0) is None
    assert any("tilt NOT corrected" in r.getMessage() for r in caplog.records)


def _glyph(text, x0, x1, top=368.26, height=4.44):
    return {"text": text, "top": top, "bottom": top + height, "x0": x0, "x1": x1}


def test_pad_space_overlapping_the_first_digit_does_not_split_the_number():
    digits = [("2", 548.64, 550.89), ("3", 550.92, 553.17), (",", 553.2, 554.35), ("1", 554.39, 556.64),
              ("5", 556.67, 558.92), ("0", 558.95, 561.2), (".", 561.22, 562.38), ("3", 562.42, 564.67),
              ("1", 564.7, 566.95)]
    pads = [(" ", 540.75 + i * 0.84, 541.58 + i * 0.84) for i in range(11)]
    chars = [_glyph(t, a, b) for t, a, b in digits + pads]
    kept = _dedupe_overlapping_chars(chars)
    assert "".join(c["text"] for c in sorted(kept, key=lambda c: c["x0"])).strip() == "23,150.31"


def test_real_gap_between_words_is_kept():
    chars = [_glyph("A", 10.0, 12.0), _glyph(" ", 12.0, 13.0), _glyph("B", 13.2, 15.0)]
    kept = _dedupe_overlapping_chars(chars)
    assert "".join(c["text"] for c in sorted(kept, key=lambda c: c["x0"])) == "A B"


# ── extract_text(): confirmed-bad scan quality rejected before OCR/Docling ─

def _forbid(name):
    def _raise(*a, **k):
        raise AssertionError(f"{name}() must NOT be called once scan quality is confirmed bad")
    return _raise


def _lock_down_extraction_paths(monkeypatch):
    """Every heavier extraction path extract_text() could fall into --
    asserts none of them ever runs once the pre-check rejects the page."""
    import invoice_extractor.pdf.reader as pdf_reader
    monkeypatch.setattr(pdf_reader, "is_scanned_pdf", _forbid("is_scanned_pdf"))
    monkeypatch.setattr(pdf_reader, "extract_full_pdf", _forbid("extract_full_pdf"))
    monkeypatch.setattr(pdf_reader, "_extract_via_docling", _forbid("_extract_via_docling"))
    monkeypatch.setattr(pdf_reader, "_ocr", _forbid("_ocr"))


def test_confirmed_tilt_is_rejected_before_any_extraction_path_runs(monkeypatch):
    import invoice_extractor.pdf.reader as pdf_reader
    _lock_down_extraction_paths(monkeypatch)
    bad_quality = {
        "skew_deg": 12.5, "skew_ok": False, "clipped_edges": [], "clipped": False,
        "valid": False, "reasons": ["Page is tilted +12.50°, exceeding the 2.0° limit."],
        "n_lines_used": 6,
    }
    monkeypatch.setattr(pdf_reader, "check_scan_quality", lambda path: bad_quality)

    text, tables_text, method, ocr_time, scan_quality = pdf_reader.extract_text("whatever.pdf")
    assert text == ""
    assert tables_text == ""
    assert method == pdf_reader.SCAN_QUALITY_REJECTED
    assert ocr_time == 0.0
    assert scan_quality is bad_quality


def test_confirmed_clipped_table_is_rejected_before_any_extraction_path_runs(monkeypatch):
    import invoice_extractor.pdf.reader as pdf_reader
    _lock_down_extraction_paths(monkeypatch)
    bad_quality = {
        "skew_deg": 0.4, "skew_ok": True, "clipped_edges": ["right"], "clipped": True,
        "valid": False, "reasons": ["Table border line(s) touch the page edge at: right."],
        "n_lines_used": 9,
    }
    monkeypatch.setattr(pdf_reader, "check_scan_quality", lambda path: bad_quality)

    text, tables_text, method, ocr_time, scan_quality = pdf_reader.extract_text("whatever.pdf")
    assert method == pdf_reader.SCAN_QUALITY_REJECTED
    assert scan_quality is bad_quality


def test_inconclusive_no_grid_detected_does_not_get_rejected(monkeypatch):
    """"Could not detect a clear table grid" sets valid=False but leaves
    skew_ok=True and clipped=False -- it's inconclusive, not confirmed-bad,
    so a normal invoice with no ruled table borders must still go through
    extraction instead of being wrongly rejected."""
    import invoice_extractor.pdf.reader as pdf_reader
    inconclusive_quality = {
        "skew_deg": None, "skew_ok": True, "clipped_edges": [], "clipped": False,
        "valid": False,
        "reasons": ["Could not detect a clear table grid (too few long straight lines); "
                    "unable to confirm tilt/clipping."],
        "n_lines_used": 0,
    }
    monkeypatch.setattr(pdf_reader, "check_scan_quality", lambda path: inconclusive_quality)
    monkeypatch.setattr(pdf_reader, "is_scanned_pdf", lambda path: False)
    monkeypatch.setattr(pdf_reader, "extract_full_pdf", lambda path, **k: "a digital text layer with plenty of characters in it")

    text, tables_text, method, ocr_time, scan_quality = pdf_reader.extract_text("whatever.pdf")
    assert text == "a digital text layer with plenty of characters in it"
    assert method == "2d_layout_canvas"
