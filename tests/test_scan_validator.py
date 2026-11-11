from __future__ import annotations

import numpy as np
import cv2

import invoice_extractor.pdf.scan_validator as sv

_W, _H = 600, 800


def _blank_image():
    return np.full((_H, _W, 3), 255, dtype=np.uint8)


def _draw_line(img, x1, y1, x2, y2, thickness=2):
    cv2.line(img, (x1, y1), (x2, y2), (0, 0, 0), thickness)


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
