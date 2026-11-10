from __future__ import annotations

import numpy as np
import cv2

import invoice_extractor.pdf.scan_validator as sv

_W, _H = 600, 800


def _blank_image():
    return np.full((_H, _W, 3), 255, dtype=np.uint8)


def _draw_line(img, x1, y1, x2, y2, thickness=2):
    cv2.line(img, (x1, y1), (x2, y2), (0, 0, 0), thickness)


def _cut_text(img, side, count=14):
    """Character-sized marks running into one edge: text cut off there."""
    for i in range(count):
        a = 40 + i * 30
        if side == "left":
            cv2.rectangle(img, (0, a), (5, a + 10), (0, 0, 0), -1)
        elif side == "top":
            cv2.rectangle(img, (a, 0), (a + 6, 9), (0, 0, 0), -1)


def test_line_running_along_left_edge_is_not_clipping():
    img = _blank_image()
    _draw_line(img, 2, 50, 2, 400)
    _draw_line(img, 2, 450, 2, 750)
    result = sv.validate_scan(img)
    assert result["clipped"] is False
    assert "left" not in result["clipped_edges"]


def test_horizontal_lines_terminating_at_left_edge_is_clipping():
    img = _blank_image()
    _draw_line(img, 2, 100, 300, 100)
    _draw_line(img, 2, 300, 300, 300)
    _draw_line(img, 2, 500, 300, 500)
    _cut_text(img, "left")
    result = sv.validate_scan(img)
    assert result["clipped"] is True
    assert "left" in result["clipped_edges"]


def test_line_running_along_top_edge_is_not_clipping():
    img = _blank_image()
    _draw_line(img, 50, 2, 250, 2)
    _draw_line(img, 300, 2, 550, 2)
    result = sv.validate_scan(img)
    assert result["clipped"] is False
    assert "top" not in result["clipped_edges"]


def test_vertical_lines_terminating_at_top_edge_is_clipping():
    img = _blank_image()
    _draw_line(img, 100, 2, 100, 300)
    _draw_line(img, 300, 2, 300, 300)
    _draw_line(img, 500, 2, 500, 300)
    _cut_text(img, "top")
    result = sv.validate_scan(img)
    assert result["clipped"] is True
    assert "top" in result["clipped_edges"]


def test_find_line_segments_accepts_both_opencv_output_shapes(monkeypatch):
    segs = np.array([[2, 100, 300, 100], [2, 300, 300, 300]], dtype=np.int32)
    bw = np.zeros((_H, _W), dtype=np.uint8)
    for shape in (segs.reshape(-1, 1, 4), segs):
        monkeypatch.setattr(sv.cv2, "HoughLinesP", lambda *a, _s=shape, **k: _s)
        assert sv._find_line_segments(bw) == [(2, 100, 300, 100), (2, 300, 300, 300)]


def test_single_line_does_not_meet_independent_line_minimum():
    img = _blank_image()
    _draw_line(img, 2, 300, 300, 300)
    result = sv.validate_scan(img)
    assert result["clipped"] is False


def test_full_width_rules_running_edge_to_edge_are_not_clipping():
    img = _blank_image()
    for y in (100, 300, 500):
        _draw_line(img, 0, y, _W - 1, y)
    result = sv.validate_scan(img)
    assert result["clipped"] is False and result["clipped_edges"] == []


def test_full_height_lines_running_edge_to_edge_are_not_clipping():
    img = _blank_image()
    for x in (100, 300, 500):
        _draw_line(img, x, 0, x, _H - 1)
    assert sv.validate_scan(img)["clipped"] is False


def test_lines_cut_at_one_edge_still_count_next_to_full_width_rules():
    img = _blank_image()
    for y in (100, 300, 500):
        _draw_line(img, 0, y, _W - 1, y)
    for y in (180, 380, 580):
        _draw_line(img, 0, y, 300, y)
    _cut_text(img, "left")
    result = sv.validate_scan(img)
    assert result["clipped"] is True and result["clipped_edges"] == ["left"]


def _table_running_to_left_edge():
    img = _blank_image()
    for y in (100, 300, 500):
        _draw_line(img, 2, y, 300, y)
    return img


def test_table_lines_running_to_the_edge_with_no_text_cut_off_are_not_clipping():
    # A layout drawn flush to the page edge (Emerson): lines reach the edge, text does not.
    assert sv.validate_scan(_table_running_to_left_edge())["clipped"] is False


def test_text_cut_at_a_different_edge_than_the_lines_is_not_clipping():
    # Scan frame lines on one side, a barcode at the top (LENOVO): no single edge has both.
    img = _table_running_to_left_edge()
    _cut_text(img, "top")
    assert sv.validate_scan(img)["clipped"] is False


def test_a_text_layer_can_say_where_text_is_cut_off():
    img = _table_running_to_left_edge()
    assert sv.validate_scan(img, cut_text_edges=["left"])["clipped_edges"] == ["left"]
    assert sv.validate_scan(img, cut_text_edges=["right"])["clipped"] is False


def test_tilt_can_be_left_unchecked():
    img = _blank_image()
    for y in (200, 400, 600):
        _draw_line(img, 50, y, 550, y + 60)
    assert sv.validate_scan(img)["skew_ok"] is False
    assert sv.validate_scan(img, check_tilt=False)["skew_ok"] is True
