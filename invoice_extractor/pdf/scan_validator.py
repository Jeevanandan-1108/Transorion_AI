from __future__ import annotations

import cv2
import numpy as np


def _binarize(gray):
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15
    )


def _find_line_segments(bw, min_len_frac=0.25):
    h, w = bw.shape[:2]
    min_len = int(min(h, w) * min_len_frac)
    lines = cv2.HoughLinesP(
        bw, 1, np.pi / 720, threshold=120,
        minLineLength=min_len, maxLineGap=15
    )
    if lines is None:
        return []
    return [tuple(int(v) for v in l) for l in np.asarray(lines).reshape(-1, 4)]


def _angle_of(x1, y1, x2, y2):
    a = np.degrees(np.arctan2(y2 - y1, x2 - x1))
    while a <= -90:
        a += 180
    while a > 90:
        a -= 180
    return a


def _cluster_by_angle(segments, bin_size=1.0):
    buckets = {}
    for (x1, y1, x2, y2) in segments:
        ang = _angle_of(x1, y1, x2, y2)
        norm_ang = ang - 90 if ang > 45 else (ang + 90 if ang < -45 else ang)
        key = round(norm_ang / bin_size) * bin_size
        length = np.hypot(x2 - x1, y2 - y1)
        buckets.setdefault(key, []).append((x1, y1, x2, y2, length, norm_ang, ang))

    if not buckets:
        return None, []

    best_key = max(buckets, key=lambda k: sum(s[4] for s in buckets[k]))
    return best_key, buckets[best_key]


_MIN_CUT_CHARACTERS = 10
_EDGE_BAND_PX = 4


def _edges_with_cut_text(bw, min_characters=_MIN_CUT_CHARACTERS, band=_EDGE_BAND_PX):
    """Edges that character-sized marks run into once long ruled lines are
    removed: text cut off by the edge of the page."""
    H, W = bw.shape[:2]
    lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (60, 1)))
    lines |= cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 60)))
    text = cv2.subtract(bw, cv2.dilate(lines, np.ones((5, 5), np.uint8)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(text, connectivity=8)
    counts = {"left": 0, "right": 0, "top": 0, "bottom": 0}
    for x, y, w, h, area in stats[1:n]:
        if area < 20 or w > W * 0.5 or h > H * 0.5:
            continue
        counts["left"] += x <= band
        counts["right"] += x + w >= W - band
        counts["top"] += y <= band
        counts["bottom"] += y + h >= H - band
    return {side for side, count in counts.items() if count >= min_characters}


def validate_scan(image, max_skew_deg=2.0, edge_margin_frac=0.005, min_len_frac=0.25,
                  cut_text_edges=None, check_tilt=True):
    """Tilt and clipped-table check for one rendered page.

    An edge counts as clipped only when table lines end at it AND text is cut
    off at that same edge: lines alone may be a design that runs to the page
    edge. `cut_text_edges` gives the edges where the page's text layer shows
    words running past the page; without it, cut text is looked for in the
    image. `check_tilt=False` skips the tilt check (a page read from its text
    layer is not affected by tilt).
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    H, W = gray.shape[:2]
    bw = _binarize(gray)

    segments = _find_line_segments(bw, min_len_frac=min_len_frac)
    angle, cluster = _cluster_by_angle(segments)

    result = {
        "skew_deg": None,
        "skew_ok": True,
        "clipped_edges": [],
        "clipped": False,
        "valid": True,
        "reasons": [],
        "n_lines_used": 0,
    }

    if angle is None or not cluster:
        result["reasons"].append(
            "Could not detect a clear table grid (too few long straight lines); "
            "unable to confirm tilt/clipping."
        )
        result["valid"] = False
        return result

    result["skew_deg"] = round(float(angle), 2)
    result["n_lines_used"] = len(cluster)

    if check_tilt and abs(angle) > max_skew_deg:
        result["skew_ok"] = False
        result["reasons"].append(
            f"Page is tilted {angle:+.2f}°, exceeding the {max_skew_deg}° limit."
        )

    margin_x = max(3, int(W * edge_margin_frac))
    margin_y = max(3, int(H * edge_margin_frac))
    MIN_INDEPENDENT_LINES = 3
    DEDUPE_GAP = 40
    MAX_PARALLEL_DEG = 20

    hits_per_edge = {"left": [], "right": [], "top": [], "bottom": []}
    for (x1, y1, x2, y2, length, norm_ang, raw_ang) in cluster:
        is_horizontal = abs(raw_ang) <= MAX_PARALLEL_DEG
        is_vertical = abs(abs(raw_ang) - 90) <= MAX_PARALLEL_DEG

        line_hits: dict[str, list[int]] = {}
        for (x, y) in ((x1, y1), (x2, y2)):
            if is_horizontal:
                if x <= margin_x:
                    line_hits.setdefault("left", []).append(y)
                elif x >= W - margin_x:
                    line_hits.setdefault("right", []).append(y)
            if is_vertical:
                if y <= margin_y:
                    line_hits.setdefault("top", []).append(x)
                elif y >= H - margin_y:
                    line_hits.setdefault("bottom", []).append(x)

        # A line from one edge to the opposite one is a full-width rule or a
        # form border drawn edge to edge. A table cut off by the edge of the
        # scan reaches only the edge it was cut at.
        if {"left", "right"} <= line_hits.keys() or {"top", "bottom"} <= line_hits.keys():
            continue
        for side, coords in line_hits.items():
            hits_per_edge[side].append(sum(coords) / len(coords))

    edges_hit = set()
    for side, coords in hits_per_edge.items():
        if not coords:
            continue
        coords = sorted(coords)
        groups = [[coords[0]]]
        for c in coords[1:]:
            if c - groups[-1][-1] <= DEDUPE_GAP:
                groups[-1].append(c)
            else:
                groups.append([c])
        if len(groups) >= MIN_INDEPENDENT_LINES:
            edges_hit.add(side)

    if edges_hit:
        edges_hit &= set(cut_text_edges) if cut_text_edges is not None else _edges_with_cut_text(bw)
    if edges_hit:
        result["clipped"] = True
        result["clipped_edges"] = sorted(edges_hit)
        result["reasons"].append(
            f"Table border line(s) and text run off the page edge at: {', '.join(sorted(edges_hit))}. "
            "This means part of the table (a row or column) likely falls outside "
            "the scanned page and is missing."
        )

    result["valid"] = result["skew_ok"] and not result["clipped"]
    return result


def validate_pdf(pdf_path, dpi=200, page=0, **kwargs):
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(pdf_path)
    try:
        pdf_page = pdf[page]
        scale = dpi / 72
        bitmap = pdf_page.render(scale=scale)
        pil_img = bitmap.to_pil()
    finally:
        pdf.close()

    img = cv2.cvtColor(np.array(pil_img.convert("RGB")), cv2.COLOR_RGB2BGR)
    return validate_scan(img, **kwargs)
