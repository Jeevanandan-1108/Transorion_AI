from __future__ import annotations
from pathlib import Path

import logging
import math
import os
import re
import shutil
import statistics

import pdfplumber
import pypdfium2 as pdfium

logger = logging.getLogger("invoice_extractor")

try:
    from invoice_extractor.pdf import scan_validator
except Exception as _scan_validator_import_error:
    scan_validator = None
    logger.warning(
        "scan_validator could not be imported (%s) -- tilt correction and "
        "clipped-table detection are DISABLED", _scan_validator_import_error,
    )


class ClippedTableError(RuntimeError):
    pass


_MAX_CORRECTABLE_SKEW_DEG = 30.0
_MIN_SKEW_TO_CORRECT_DEG = 0.3


_COLUMN_BOUNDARY_MARKER = "│"

_DUPLICATE_GLYPH_TOLERANCE_PT = 1.0


def _dedupe_overlapping_chars(
    chars: list[dict],
    x_tolerance: float = _DUPLICATE_GLYPH_TOLERANCE_PT,
    top_tolerance_factor: float = 0.35,
) -> list[dict]:
    chars_sorted = sorted(chars, key=lambda c: (c["top"], c["x0"]))
    kept: list[dict] = []
    for c in chars_sorted:
        is_dup = False
        c_is_space = c["text"].isspace()
        for k in kept[-12:]:
            if c_is_space != k["text"].isspace():
                continue
            height = min(k["bottom"] - k["top"], c["bottom"] - c["top"]) or 1.0
            top_tolerance = max(x_tolerance, height * top_tolerance_factor)
            if (
                abs(k["top"] - c["top"]) <= top_tolerance
                and abs(k["x0"] - c["x0"]) <= x_tolerance
            ):
                is_dup = True
                break
        if not is_dup:
            kept.append(c)
    return _drop_spaces_inside_glyphs(kept)


def _drop_spaces_inside_glyphs(chars: list[dict], window: int = 12, overlap_fraction: float = 0.5) -> list[dict]:
    drop: set[int] = set()
    for i, c in enumerate(chars):
        if not c["text"].isspace():
            continue
        if "x1" not in c:
            continue
        width = c["x1"] - c["x0"]
        if width <= 0:
            continue
        for j in range(max(0, i - window), min(len(chars), i + window + 1)):
            g = chars[j]
            if j == i or g["text"].isspace() or "x1" not in g:
                continue
            if abs(g["top"] - c["top"]) > max(1.0, 0.35 * (g["bottom"] - g["top"])):
                continue
            inside = min(c["x1"], g["x1"]) - max(c["x0"], g["x0"])
            if inside >= overlap_fraction * width:
                drop.add(i)
                break
    return [c for i, c in enumerate(chars) if i not in drop] if drop else chars


def _detect_column_boundaries(
    lines: list[list[dict]],
    page: pdfplumber.page.Page,
    char_width_pts: float,
    min_gap_multiplier: float = 4.0,
    min_recurrence_fraction: float = 0.15,
    min_recurrence_count: int = 3,
) -> list[float]:
    cluster_tolerance_pts = char_width_pts * 2
    min_gap_pts = char_width_pts * min_gap_multiplier

    gap_midpoints: list[float] = []
    for line in lines:
        for prev_w, next_w in zip(line, line[1:]):
            gap = next_w["x0"] - prev_w["x1"]
            if gap >= min_gap_pts:
                gap_midpoints.append((prev_w["x1"] + next_w["x0"]) / 2.0)
                gap_midpoints.append(next_w["x0"] - char_width_pts)

    gap_midpoints.sort()
    bands: list[list[float]] = []
    for x in gap_midpoints:
        if bands and x - bands[-1][0] <= cluster_tolerance_pts:
            bands[-1].append(x)
        else:
            bands.append([x])

    min_recurrence = max(min_recurrence_count, int(len(lines) * min_recurrence_fraction))
    confirmed = [sum(band) / len(band) for band in bands if len(band) >= min_recurrence]

    min_rule_height = page.height * 0.05
    for line_obj in page.lines or []:
        if abs(line_obj["x0"] - line_obj["x1"]) <= 1.0 and (line_obj["bottom"] - line_obj["top"]) >= min_rule_height:
            confirmed.append(line_obj["x0"])
    for rect in page.rects or []:
        if rect["width"] <= 1.5 and rect["height"] >= min_rule_height:
            confirmed.append(rect["x0"])

    return sorted(set(round(x, 1) for x in confirmed))


def _gap_crosses_boundary(gap_start_pts: float, gap_end_pts: float, boundaries: list[float]) -> bool:
    return any(gap_start_pts <= b <= gap_end_pts for b in boundaries)


def _deskew_words(words: list[dict], skew_deg: float, page_width: float, page_height: float) -> list[dict]:
    theta = math.radians(-skew_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    cx, cy = page_width / 2.0, page_height / 2.0

    corrected: list[dict] = []
    for w in words:
        width, height = w["x1"] - w["x0"], w["bottom"] - w["top"]
        center_x, center_y = (w["x0"] + w["x1"]) / 2.0, (w["top"] + w["bottom"]) / 2.0
        dx, dy = center_x - cx, center_y - cy
        new_cx = cx + dx * cos_t - dy * sin_t
        new_cy = cy + dx * sin_t + dy * cos_t

        nw = dict(w)
        nw["x0"], nw["x1"] = new_cx - width / 2.0, new_cx + width / 2.0
        nw["top"], nw["bottom"] = new_cy - height / 2.0, new_cy + height / 2.0
        corrected.append(nw)
    return corrected


def extract_page_layout_canvas(
    page: pdfplumber.page.Page,
    char_width_pts: float | None = None,
    v_tolerance_factor: float = 0.35,
    skew_deg: float | None = None,
) -> str:
    chars = _dedupe_overlapping_chars(page.chars)
    words = pdfplumber.utils.extract_words(chars, x_tolerance=1.5, y_tolerance=1.5)
    if not words:
        return ""

    if (
        skew_deg is not None
        and _MIN_SKEW_TO_CORRECT_DEG <= abs(skew_deg) <= _MAX_CORRECTABLE_SKEW_DEG
    ):
        words = _deskew_words(words, skew_deg, page.width, page.height)

    if char_width_pts is None:
        if page.chars:
            valid_widths = [c['width'] for c in page.chars if c.get('text', '').strip()]
            char_width_pts = round(statistics.median(valid_widths), 2) if valid_widths else 4.0
        else:
            char_width_pts = 4.0

    font_heights = [w['bottom'] - w['top'] for w in words]
    med_height = statistics.median(font_heights) if font_heights else 8.0
    v_tolerance = max(2.0, med_height * v_tolerance_factor)

    sorted_words = sorted(words, key=lambda w: (w['top'], w['x0']))

    lines: list[list[dict]] = []
    curr_line: list[dict] = []
    curr_center: float | None = None

    for w in sorted_words:
        w_center = (w['top'] + w['bottom']) / 2.0
        if curr_center is None:
            curr_center = w_center
            curr_line.append(w)
        elif abs(w_center - curr_center) <= v_tolerance:
            curr_line.append(w)
            curr_center = sum((x['top'] + x['bottom']) / 2.0 for x in curr_line) / len(curr_line)
        else:
            lines.append(sorted(curr_line, key=lambda x: x['x0']))
            curr_line = [w]
            curr_center = w_center

    if curr_line:
        lines.append(sorted(curr_line, key=lambda x: x['x0']))

    column_boundaries = _detect_column_boundaries(lines, page, char_width_pts)

    total_cols = int(math.ceil(page.width / char_width_pts)) + 20
    output_rows: list[str] = []

    for line in lines:
        row = [' '] * total_cols
        curr_col = 0
        for idx, w in enumerate(line):
            target_col = int(round(w['x0'] / char_width_pts))
            col = max(target_col, curr_col + 1 if curr_col > 0 else target_col)

            if idx > 0 and column_boundaries:
                prev_w = line[idx - 1]
                this_gap = w["x0"] - prev_w["x1"]
                if this_gap >= char_width_pts * 1.5 and _gap_crosses_boundary(prev_w["x1"], w["x0"], column_boundaries):
                    delim_col = (curr_col + col) // 2
                    if curr_col <= delim_col < len(row):
                        row[delim_col] = _COLUMN_BOUNDARY_MARKER

            w_text = w['text']
            for i, ch in enumerate(w_text):
                pos = col + i
                if pos < len(row):
                    row[pos] = ch
            curr_col = col + len(w_text)

        output_rows.append(''.join(row).rstrip())

    return '\n'.join(output_rows)


def _detect_page_skew(pdf_path: str | Path, page_index: int) -> float | None:
    if scan_validator is None:
        return None
    try:
        return scan_validator.validate_pdf(str(pdf_path), page=page_index)["skew_deg"]
    except Exception as e:
        logger.warning("%s page %d: skew detection failed (%s) -- tilt NOT corrected", pdf_path, page_index, e)
        return None


def extract_full_pdf(pdf_path: str | Path, char_width_pts: float | None = None) -> str:
    pdf_path = Path(pdf_path)
    extracted_pages: list[str] = []

    with pdfplumber.open(str(pdf_path)) as pdf:
        num_pages = len(pdf.pages)
        for idx, page in enumerate(pdf.pages, start=1):
            skew_deg = _detect_page_skew(pdf_path, idx - 1)
            page_text = extract_page_layout_canvas(page, char_width_pts=char_width_pts, skew_deg=skew_deg)
            if page_text and page_text.strip():
                header = f"\n{'=' * 35} PAGE {idx} OF {num_pages} ({pdf_path.name}) {'=' * 35}\n"
                extracted_pages.append(header + page_text)

    return "\n".join(extracted_pages).strip()


_WORD_PATTERN = re.compile(r"[a-zA-Z0-9]")


def _page_text_stats(pdf_path: str | Path, min_word_len: int = 2) -> list[dict[str, int]]:
    stats: list[dict[str, int]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            chars = _dedupe_overlapping_chars(page.chars)
            words = pdfplumber.utils.extract_words(chars)
            page_text = pdfplumber.utils.extract_text(chars) or ""

            valid_words = 0
            single_char_words = 0
            for w in words:
                t = w.get("text", "").strip()
                if _WORD_PATTERN.search(t):
                    if len(t) >= min_word_len:
                        valid_words += 1
                    else:
                        single_char_words += 1

            stats.append({
                "chars": len(page_text.strip()),
                "valid_words": valid_words,
                "single_char_words": single_char_words,
            })
    return stats


def is_scanned_pdf(
    pdf_path: str | Path,
    min_words_per_page: int = 25,
    min_word_len: int = 2,
    min_avg_chars_per_page: int = 150,
) -> bool:
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return True

    try:
        page_stats = _page_text_stats(pdf_path, min_word_len=min_word_len)
    except Exception as e:
        logger.debug(
            "%s: is_scanned_pdf check failed (%s), treating as scanned to trigger OCR",
            pdf_path, e,
        )
        return True

    total_pages = len(page_stats)
    if total_pages == 0:
        return True

    total_chars = sum(p["chars"] for p in page_stats)
    total_valid_words = sum(p["valid_words"] for p in page_stats)
    single_char_word_count = sum(p["single_char_words"] for p in page_stats)
    pages_with_dense_text = sum(1 for p in page_stats if p["valid_words"] >= min_words_per_page)

    avg_chars_per_page = total_chars / total_pages
    if avg_chars_per_page < min_avg_chars_per_page:
        logger.debug(
            "%s: avg %.0f chars/page < %d -> scanned",
            pdf_path, avg_chars_per_page, min_avg_chars_per_page,
        )
        return True

    avg_words_per_page = total_valid_words / total_pages
    if avg_words_per_page < min_words_per_page:
        logger.debug(
            "%s: avg %.1f words/page < %d -> scanned",
            pdf_path, avg_words_per_page, min_words_per_page,
        )
        return True

    if single_char_word_count > (total_valid_words * 0.8):
        logger.debug(
            "%s: %d single-char word fragments vs %d real words -> scanned/corrupted",
            pdf_path, single_char_word_count, total_valid_words,
        )
        return True

    if total_pages > 1 and pages_with_dense_text < (total_pages / 2):
        logger.debug(
            "%s: only %d/%d pages have dense text -> scanned",
            pdf_path, pages_with_dense_text, total_pages,
        )
        return True

    return False


_DOCLING_CONVERTER = None


def _get_docling_converter():
    global _DOCLING_CONVERTER
    if _DOCLING_CONVERTER is not None:
        return _DOCLING_CONVERTER
    from docling.document_converter import DocumentConverter
    _DOCLING_CONVERTER = DocumentConverter()
    return _DOCLING_CONVERTER


def _docling_group_lines(words: list[dict], v_tolerance: float) -> list[list[dict]]:
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines: list[list[dict]] = []
    curr_line: list[dict] = []
    curr_center: float | None = None
    for w in sorted_words:
        w_center = (w["top"] + w["bottom"]) / 2.0
        if curr_center is None:
            curr_center = w_center
            curr_line.append(w)
        elif abs(w_center - curr_center) <= v_tolerance:
            curr_line.append(w)
            curr_center = sum((x["top"] + x["bottom"]) / 2.0 for x in curr_line) / len(curr_line)
        else:
            lines.append(sorted(curr_line, key=lambda x: x["x0"]))
            curr_line = [w]
            curr_center = w_center
    if curr_line:
        lines.append(sorted(curr_line, key=lambda x: x["x0"]))
    return lines


def _docling_render_canvas(words: list[dict], char_width_pts: float, v_tolerance: float) -> str:
    if not words:
        return ""
    lines = _docling_group_lines(words, v_tolerance)
    total_cols = int(math.ceil(max(w["x1"] for w in words) / char_width_pts)) + 20
    output_rows: list[str] = []
    for line in lines:
        row = [" "] * total_cols
        curr_col = 0
        for w in line:
            target_col = int(round(w["x0"] / char_width_pts))
            col = max(target_col, curr_col + 1 if curr_col > 0 else target_col)
            text = w["text"]
            for i, ch in enumerate(text):
                pos = col + i
                if pos < len(row):
                    row[pos] = ch
            curr_col = col + len(text)
        output_rows.append("".join(row).rstrip())
    return "\n".join(output_rows)


def _extract_via_docling(pdf_path: str | Path) -> tuple[str, float]:
    import time
    from docling_core.types.doc import TableItem

    start = time.perf_counter()
    pdf_path = Path(pdf_path)

    with pdfplumber.open(str(pdf_path)) as pdf:
        page_sizes = [(p.width, p.height) for p in pdf.pages]

    res = _get_docling_converter().convert(str(pdf_path))
    doc = res.document

    def to_box(bbox, page_no: int) -> tuple[float, float, float, float]:
        dp_size = doc.pages[page_no].size
        pw, ph = page_sizes[page_no - 1] if page_no - 1 < len(page_sizes) else (dp_size.width, dp_size.height)
        b = bbox.to_top_left_origin(page_height=dp_size.height)
        sx, sy = pw / dp_size.width, ph / dp_size.height
        return (b.l * sx, b.t * sy, b.r * sx, b.b * sy)

    def as_word(box: tuple[float, float, float, float], text: str) -> dict:
        return {"x0": box[0], "x1": box[2], "top": box[1], "bottom": box[3], "text": text}

    extracted_pages: list[str] = []
    num_pages = len(res.pages)
    for res_page in sorted(res.pages, key=lambda pg: pg.page_no):
        page_no = res_page.page_no

        cells = list(getattr(res_page, "cells", None) or [])
        if not cells:
            parsed = getattr(res_page, "parsed_page", None)
            cells = list(getattr(parsed, "textline_cells", None) or [])

        words: list[dict] = []
        for c in cells:
            txt = (getattr(c, "text", "") or "").strip()
            if not txt:
                continue
            try:
                box = to_box(c.rect.to_bounding_box(), page_no)
            except Exception:
                continue
            words.append(as_word(box, txt))

        if not words:
            for item, _level in doc.iterate_items():
                if not getattr(item, "prov", None):
                    continue
                if isinstance(item, TableItem):
                    if item.prov[0].page_no != page_no:
                        continue
                    for c in item.data.table_cells:
                        txt = (c.text or "").strip()
                        if txt:
                            words.append(as_word(to_box(c.bbox, page_no), txt))
                    continue
                text = getattr(item, "text", None)
                if text and text.strip():
                    for prov in item.prov:
                        if prov.page_no == page_no:
                            words.append(as_word(to_box(prov.bbox, page_no), text.strip()))

        if not words:
            continue

        heights = [w["bottom"] - w["top"] for w in words] or [8.0]
        v_tolerance = max(2.0, statistics.median(heights) * 0.35)
        widths = [(w["x1"] - w["x0"]) / len(w["text"]) for w in words if len(w["text"]) > 1]
        char_width = max(round(statistics.median(widths), 2), 1.0) if widths else 4.0

        page_text = _docling_render_canvas(words, char_width, v_tolerance)
        if page_text.strip():
            header = f"\n{'=' * 35} PAGE {page_no} OF {num_pages} ({pdf_path.name}) {'=' * 35}\n"
            extracted_pages.append(header + page_text)

    full_text = "\n".join(extracted_pages).strip()
    elapsed = round(time.perf_counter() - start, 2)
    return full_text, elapsed


_OCR_ENGINE = None
_OCR_ENGINE_TYPE = None


def _get_ocr_engine():
    global _OCR_ENGINE, _OCR_ENGINE_TYPE
    if _OCR_ENGINE is not None:
        return _OCR_ENGINE, _OCR_ENGINE_TYPE

    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception as e:
        raise ImportError(
            "Scanned image PDF detected, but RapidOCR is not ready in Python.\n"
            "Please run: pip install rapidocr_onnxruntime"
        ) from e

    _OCR_ENGINE = RapidOCR()
    _OCR_ENGINE_TYPE = "rapidocr"
    logger.info("OCR engine: using RapidOCR")
    return _OCR_ENGINE, _OCR_ENGINE_TYPE


def _run_ocr_engine_on_image(image_path: str, engine, engine_type: str, page_idx: int = 0) -> list[str]:
    lines_extracted: list[str] = []
    if engine_type == "rapidocr":
        with open(image_path, "rb") as f:
            img_bytes = f.read()
        result, _ = engine(img_bytes)
        if result:
            for line in result:
                if line and len(line) > 1:
                    lines_extracted.append(str(line[1]))
    return lines_extracted


def _ocr(pdf_path: str | Path, dpi: int = 200) -> tuple[str, str, str, float]:
    import tempfile
    import time

    start_ocr = time.perf_counter()
    engine, engine_type = _get_ocr_engine()

    ocr_pages: list[str] = []
    ocr_tables: list[str] = []

    scale = dpi / 72
    doc = pdfium.PdfDocument(str(pdf_path))
    for page_idx in range(len(doc)):
        page = doc[page_idx]
        bitmap = page.render(scale=scale)
        pil_image = bitmap.to_pil()

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_img:
            pil_image.save(tmp_img, format="PNG")
            tmp_img_path = tmp_img.name

        try:
            lines_extracted = _run_ocr_engine_on_image(tmp_img_path, engine, engine_type, page_idx)
        finally:
            if os.path.exists(tmp_img_path):
                try:
                    os.unlink(tmp_img_path)
                except Exception:
                    pass

        page.close()

        if lines_extracted:
            ocr_pages.append(f"--- Page {page_idx + 1} (OCR) ---\n" + "\n".join(lines_extracted))

    doc.close()

    ocr_time_seconds = round(time.perf_counter() - start_ocr, 2)
    full_ocr_text = "\n\n".join(ocr_pages).strip()
    full_ocr_tables = "\n\n".join(ocr_tables).strip()
    return full_ocr_text, full_ocr_tables, engine_type, ocr_time_seconds


_GARBLED_MIN_WORDS = 20
_GARBLED_MIN_VALID_WORD_RATIO = 0.5


def _word_quality_ratio(text: str) -> tuple[int, float]:
    words = [w for w in re.split(r"\s+", text) if _WORD_PATTERN.search(w)]
    if not words:
        return 0, 0.0
    valid = sum(1 for w in words if len(w) >= 2)
    return len(words), valid / len(words)


def _looks_garbled(text: str) -> bool:
    count, ratio = _word_quality_ratio(text)
    return count >= _GARBLED_MIN_WORDS and ratio < _GARBLED_MIN_VALID_WORD_RATIO


def _ocr_page_via_docker(image, dpi: int, rotation: int) -> str:
    import tempfile

    candidate_image = image if rotation == 0 else image.rotate(-rotation, expand=True)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_pdf:
        tmp_pdf_path = tmp_pdf.name
    try:
        candidate_image.convert("RGB").save(tmp_pdf_path, "PDF")
        docker_pdf, _ = _ocr_with_docker(tmp_pdf_path, dpi=dpi, force_ocr=True, rotate_pages=False)
        if docker_pdf is None:
            return ""
        try:
            return extract_full_pdf(docker_pdf)
        finally:
            if os.path.exists(docker_pdf):
                try:
                    os.unlink(docker_pdf)
                except Exception:
                    pass
    finally:
        if os.path.exists(tmp_pdf_path):
            try:
                os.unlink(tmp_pdf_path)
            except Exception:
                pass


def _ocr_best_rotation(pdf_path: str | Path, dpi: int = 200) -> tuple[str, float, float]:
    import time

    start_ocr = time.perf_counter()
    scale = dpi / 72

    doc = pdfium.PdfDocument(str(pdf_path))
    num_pages = len(doc)
    if num_pages == 0:
        doc.close()
        return "", 0.0, round(time.perf_counter() - start_ocr, 2)

    first_page_image = doc[0].render(scale=scale).to_pil()

    best_rotation = 0
    best_text = ""
    best_ratio = -1.0
    for rotation in (0, 90, 180, 270):
        candidate_text = _ocr_page_via_docker(first_page_image, dpi, rotation)
        _, ratio = _word_quality_ratio(candidate_text)
        if ratio > best_ratio:
            best_ratio = ratio
            best_rotation = rotation
            best_text = candidate_text

    ocr_pages = [best_text] if best_text else []
    for page_idx in range(1, num_pages):
        page_image = doc[page_idx].render(scale=scale).to_pil()
        page_text = _ocr_page_via_docker(page_image, dpi, best_rotation)
        if page_text:
            ocr_pages.append(page_text)

    doc.close()
    elapsed = round(time.perf_counter() - start_ocr, 2)
    logger.info(
        "%s: rotation-recovery OCR finished in %.2fs (chosen rotation: %d degrees)",
        pdf_path, elapsed, best_rotation,
    )
    full_text = "\n\n".join(ocr_pages).strip()
    _, overall_ratio = _word_quality_ratio(full_text)
    return full_text, overall_ratio, elapsed


def _maybe_recover_garbled_ocr(
    pdf_path: str | Path, text: str, method: str, ocr_time: float,
) -> tuple[str, str, float]:
    if not _looks_garbled(text):
        return text, method, ocr_time
    logger.info("%s: OCR output looks garbled, retrying with per-page rotation search", pdf_path)
    recovered_text, recovered_ratio, recovery_time = _ocr_best_rotation(pdf_path)
    _, current_ratio = _word_quality_ratio(text)
    if recovered_text and recovered_ratio > current_ratio:
        return recovered_text, f"{method}+rotation_recovered", round(ocr_time + recovery_time, 2)
    return text, method, round(ocr_time + recovery_time, 2)


_ROTATE_PAGES_THRESHOLD = 5


def _ocr_with_docker(
    input_path: str | Path, dpi: int = 300, force_ocr: bool = False, rotate_pages: bool = True,
    rotate_pages_threshold: float = _ROTATE_PAGES_THRESHOLD,
) -> tuple[Path | None, float]:
    import subprocess
    import tempfile
    import time

    in_file = Path(input_path)
    if not in_file.exists():
        return None, 0.0

    if shutil.which("docker") is None:
        logger.info("Docker OCR fallback skipped: 'docker' not found on PATH")
        return None, 0.0

    start_docker = time.perf_counter()
    tmp_out = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp_out.close()

    cmd = [
        "docker", "run", "--rm", "-i",
        "jbarlow83/ocrmypdf-alpine",
        "--image-dpi", str(dpi),
        *(["--rotate-pages", "--rotate-pages-threshold", str(rotate_pages_threshold)] if rotate_pages else []),
        "--deskew",
        "--force-ocr" if force_ocr else "--skip-text",
        "-", "-"
    ]

    try:
        with open(in_file, "rb") as infile, open(tmp_out.name, "wb") as outfile:
            proc = subprocess.run(
                cmd,
                stdin=infile,
                stdout=outfile,
                stderr=subprocess.PIPE,
                timeout=180,
            )
        elapsed = round(time.perf_counter() - start_docker, 2)
        if proc.returncode == 0 and os.path.getsize(tmp_out.name) > 0:
            logger.info("Docker OCRmyPDF succeeded in %.2fs", elapsed)
            return Path(tmp_out.name), elapsed
        else:
            logger.info(
                "Docker OCRmyPDF produced no usable output (returncode=%s): %s",
                proc.returncode, proc.stderr.decode(errors="ignore")[:300],
            )
            if os.path.exists(tmp_out.name):
                try:
                    os.unlink(tmp_out.name)
                except Exception:
                    pass
            return None, 0.0
    except subprocess.TimeoutExpired:
        logger.info("Docker OCRmyPDF timed out after 180s, falling back to Python OCR")
        if os.path.exists(tmp_out.name):
            try:
                os.unlink(tmp_out.name)
            except Exception:
                pass
        return None, 0.0
    except Exception as e:
        logger.info("Docker OCR fallback skipped due to error: %s", e)
        if os.path.exists(tmp_out.name):
            try:
                os.unlink(tmp_out.name)
            except Exception:
                pass
        return None, 0.0


def visualize_document_layout(pdf_path: str | Path, page_num: int = 0, dpi: int = 120):
    import matplotlib.pyplot as plt

    pdf_path = Path(pdf_path)
    with pdfplumber.open(str(pdf_path)) as pdf:
        if page_num >= len(pdf.pages):
            raise IndexError(f"Page {page_num} out of range (PDF has {len(pdf.pages)} pages).")
        page = pdf.pages[page_num]
        page_img = page.to_image(resolution=dpi)
        words = page.extract_words()
        annotated = page_img.draw_rects(words, stroke="red", stroke_width=1, fill=None)

        fig, ax = plt.subplots(figsize=(10, 10 * (page.height / page.width)))
        ax.imshow(annotated.annotated)
        ax.axis('off')
        ax.set_title(f"Visual Debug: {pdf_path.name} - Page {page_num + 1} ({len(words)} words)", fontsize=12, fontweight='bold')
        plt.tight_layout()
        plt.show()


def check_scan_quality(pdf_path: str | Path) -> dict | None:
    if scan_validator is None:
        return None
    try:
        scan_quality = scan_validator.validate_pdf(str(pdf_path), page=0)
        if not scan_quality["valid"]:
            logger.warning("%s: scan quality check failed: %s", pdf_path, scan_quality["reasons"])
        return scan_quality
    except Exception as e:
        logger.warning("%s: scan quality check could not run (%s) -- clipped-table check SKIPPED", pdf_path, e)
        return None


SCAN_QUALITY_REJECTED = "scan_quality_rejected"


def extract_text(
    pdf_path: str | Path,
    min_chars_for_text_layer: int = 40,
    force_ocr: bool | None = None,
) -> tuple[str, str, str, float, dict | None]:
    scan_quality = check_scan_quality(pdf_path)

    # A CONFIRMED tilt (skew past the threshold) or a CONFIRMED clipped
    # table edge means the page is unusable -- stop here, before the
    # digital text-layer check, Docling, or OCR ever run, so none of those
    # (Docling's models especially) get loaded on a page we already know
    # is bad. "Could not detect a clear table grid" is NOT confirmed-bad --
    # it's inconclusive (no grid lines to measure against), so an invoice
    # with no ruled table borders still goes through normal extraction
    # instead of being wrongly rejected here.
    if scan_quality and (scan_quality.get("skew_ok") is False or scan_quality.get("clipped")):
        logger.warning(
            "%s: confirmed scan-quality failure (%s) -- skipping digital text "
            "layer, Docling, and OCR entirely", pdf_path, scan_quality["reasons"],
        )
        return "", "", SCAN_QUALITY_REJECTED, 0.0, scan_quality

    skip_digital_path = force_ocr is True

    if not skip_digital_path:
        already_known_scanned = force_ocr is None and is_scanned_pdf(pdf_path)
        if not already_known_scanned:
            text = extract_full_pdf(pdf_path)
            if len(text) >= min_chars_for_text_layer:
                return text, "", "2d_layout_canvas", 0.0, scan_quality
            logger.info(
                "Digital text layer in %s was too sparse (<%d chars), falling back to OCR",
                pdf_path, min_chars_for_text_layer,
            )
        else:
            logger.info("%s classified as scanned (sparse/garbage text layer), routing straight to OCR", pdf_path)
    else:
        logger.info("force_ocr=True requested for %s, skipping the digital text-layer check", pdf_path)

    docling_time = 0.0
    try:
        d_text, docling_time = _extract_via_docling(pdf_path)
        if len(d_text) >= min_chars_for_text_layer:
            d_text, method, docling_time = _maybe_recover_garbled_ocr(
                pdf_path, d_text, "docling", docling_time,
            )
            return d_text, "", method, docling_time, scan_quality
        logger.warning(
            "%s: Docling produced <%d chars -- falling back to RapidOCR",
            pdf_path, min_chars_for_text_layer,
        )
    except Exception as e:
        logger.warning("%s: Docling failed (%s) -- falling back to RapidOCR", pdf_path, e)

    ocr_text, ocr_tables, engine_type, ocr_time = _ocr(pdf_path)
    ocr_time = round(docling_time + ocr_time, 2)
    ocr_text, method, ocr_time = _maybe_recover_garbled_ocr(
        pdf_path, ocr_text, f"ocr_{engine_type}", ocr_time,
    )
    return ocr_text, ocr_tables, method, ocr_time, scan_quality


if __name__ == "__main__":
    import sys
    t, tabs, m, ot, sq = extract_text(sys.argv[1])
    print(f"--- extracted via {m} ({len(t)} chars, ocr_time: {ot}s) ---")
    if sq:
        print(f"--- scan quality: valid={sq['valid']} skew={sq['skew_deg']} clipped={sq['clipped_edges']} ---")
    print(t[:10000])
