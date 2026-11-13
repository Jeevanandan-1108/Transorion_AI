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

from invoice_extractor.pdf.device import docling_device, rapidocr_use_cuda

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
    page: pdfplumber.page.Page | None,
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

    if page is not None:
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


# Reading every page with pdfplumber is slow on PDFs whose text is drawn as
# outlines (over a minute for 10 pages), and both the scan check and
# is_scanned_pdf need it, so one read per file is kept for the next call.
_PAGE_STATS_CACHE: dict[tuple, list[dict]] = {}


def _page_text_stats(pdf_path: str | Path, min_word_len: int = 2) -> list[dict]:
    try:
        st = os.stat(pdf_path)
        key = (str(Path(pdf_path).resolve()), st.st_mtime_ns, st.st_size, min_word_len)
    except OSError:
        key = None
    if key is not None and key in _PAGE_STATS_CACHE:
        return _PAGE_STATS_CACHE[key]

    stats: list[dict] = []
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

            # Words running past the page box: text cut off by the page edge.
            width, height = float(page.width), float(page.height)
            beyond = {"left": 0, "right": 0, "top": 0, "bottom": 0}
            for w in words:
                beyond["left"] += w["x0"] < -0.5
                beyond["right"] += w["x1"] > width + 0.5
                beyond["top"] += w["top"] < -0.5
                beyond["bottom"] += w["bottom"] > height + 0.5

            stats.append({
                "chars": len(page_text.strip()),
                "valid_words": valid_words,
                "single_char_words": single_char_words,
                "words_beyond_edge": beyond,
                # Something is drawn on the page even if it has no text: a
                # scanned image, or text drawn as outlines.
                "has_content": bool(page.images) or len(page.curves) + len(page.rects) + len(page.lines) >= 20,
            })
    if key is not None:
        if len(_PAGE_STATS_CACHE) >= 8:
            _PAGE_STATS_CACHE.pop(next(iter(_PAGE_STATS_CACHE)))
        _PAGE_STATS_CACHE[key] = stats
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
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    pipeline_options = PdfPipelineOptions()
    pipeline_options.accelerator_options = AcceleratorOptions(
        device=AcceleratorDevice.CUDA if docling_device() == "cuda" else AcceleratorDevice.CPU,
    )
    _DOCLING_CONVERTER = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)},
    )
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


def _docling_split_wrapped(words: list[dict], char_width_pts: float) -> list[dict]:
    """Split Docling items that cover several printed lines into one word per line.

    A table cell whose text wraps over 2-3 printed lines comes back as ONE item
    with a tall box. Laid out as a single line, its long text overruns the
    neighbouring cells (price, amount) and overwrites them. Here the item is cut
    at spaces into as many pieces as it has lines, each given its own slice of
    the vertical box, so the canvas lays them out like the printed page.
    """
    if not words:
        return words
    line_h = statistics.median(w["bottom"] - w["top"] for w in words)
    if line_h <= 0:
        return words
    out: list[dict] = []
    for w in words:
        text = w["text"]
        height = w["bottom"] - w["top"]
        width = w["x1"] - w["x0"]
        tokens = text.split()
        if len(tokens) < 2 or height < line_h * 1.6 or width <= 0:
            out.append(w)
            continue
        # Lines implied by the height, capped by what the text length needs.
        n = min(round(height / line_h), round(len(text) * char_width_pts / width), len(tokens))
        if n < 2:
            out.append(w)
            continue
        target = len(text) / n
        chunks: list[list[str]] = [[]]
        used = 0
        for tok in tokens:
            if chunks[-1] and len(chunks) < n and used + len(tok) / 2 > target * len(chunks):
                chunks.append([])
            chunks[-1].append(tok)
            used += len(tok) + 1
        step = height / len(chunks)
        for i, chunk in enumerate(chunks):
            out.append({
                "x0": w["x0"], "x1": w["x1"],
                "top": w["top"] + i * step, "bottom": w["top"] + (i + 1) * step,
                "text": " ".join(chunk),
            })
    return out


def _docling_render_canvas(words: list[dict], char_width_pts: float, v_tolerance: float) -> str:
    if not words:
        return ""
    words = _docling_split_wrapped(words, char_width_pts)
    lines = _docling_group_lines(words, v_tolerance)
    # OCR'd/scanned pages have no vector table rules to confirm column
    # boundaries against, but recurring word gaps alone are still enough to
    # find them -- without this, "│" markers never appear in Docling/OCR
    # text, and every downstream guardrail that reads a "│"-delimited item
    # table (part-number vs. PO-number disambiguation, chief among them)
    # silently does nothing for scanned invoices.
    column_boundaries = _detect_column_boundaries(lines, None, char_width_pts)
    total_cols = int(math.ceil(max(w["x1"] for w in words) / char_width_pts)) + 20
    output_rows: list[str] = []
    for line in lines:
        row = [" "] * total_cols
        curr_col = 0
        for idx, w in enumerate(line):
            target_col = int(round(w["x0"] / char_width_pts))
            col = max(target_col, curr_col + 1 if curr_col > 0 else target_col)

            if idx > 0 and column_boundaries:
                prev_w = line[idx - 1]
                this_gap = w["x0"] - prev_w["x1"]
                if this_gap >= char_width_pts * 1.5 and _gap_crosses_boundary(prev_w["x1"], w["x0"], column_boundaries):
                    delim_col = (curr_col + col) // 2
                    if curr_col <= delim_col < len(row):
                        row[delim_col] = _COLUMN_BOUNDARY_MARKER

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

    use_cuda = rapidocr_use_cuda()
    _OCR_ENGINE = RapidOCR(det_use_cuda=use_cuda, cls_use_cuda=use_cuda, rec_use_cuda=use_cuda)
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


_TEXT_LAYER_MIN_WORDS = 25
_CUT_TEXT_MIN_WORDS = 5


def _page_layouts(pdf_path: str | Path) -> list[tuple[bool, list[str]]]:
    """For each page: whether it has a usable text layer, and the edges where
    at least _CUT_TEXT_MIN_WORDS of its words run past the page box (text cut
    off by the page edge)."""
    return [
        (
            page["valid_words"] >= _TEXT_LAYER_MIN_WORDS,
            sorted(side for side, n in page["words_beyond_edge"].items() if n >= _CUT_TEXT_MIN_WORDS),
        )
        for page in _page_text_stats(pdf_path)
    ]


def check_scan_quality(pdf_path: str | Path) -> dict | None:
    """Tilt and clipped-table check, page by page.

    A page with a usable text layer is read from that layer, so tilt does not
    matter and it is only checked when its words run past the page edge. Any
    other page (a scan, or text drawn as outlines) goes to OCR and gets the
    full image check. `bad_pages` lists the pages (numbered from 1) confirmed
    tilted or clipped; the top-level fields keep their old meaning, combined
    over the checked pages, so callers that read `clipped` / `skew_ok` still work.
    """
    if scan_validator is None:
        return None
    try:
        layouts = _page_layouts(pdf_path)
        goes_to_ocr = is_scanned_pdf(pdf_path)
        checked = []
        for index, (text_layer, cut_edges) in enumerate(layouts):
            text_layer = text_layer and not goes_to_ocr
            if text_layer and not cut_edges:
                continue
            options = {"cut_text_edges": cut_edges, "check_tilt": False} if text_layer else {}
            checked.append({"page": index + 1, **scan_validator.validate_pdf(str(pdf_path), page=index, **options)})
        page_count = len(layouts)
    except Exception as e:
        logger.warning("%s: scan quality check could not run (%s) -- clipped-table check SKIPPED", pdf_path, e)
        return None

    bad = [r for r in checked if r["skew_ok"] is False or r["clipped"]]
    unsure = [r for r in checked if not r["valid"] and r not in bad]
    skews = [r["skew_deg"] for r in checked if r["skew_deg"] is not None]
    reasons = [f"page {r['page']}: {reason}" if page_count > 1 else reason for r in bad for reason in r["reasons"]]
    if unsure:
        where = f"page(s) {', '.join(str(r['page']) for r in unsure)}: " if page_count > 1 else ""
        reasons.append(where + unsure[0]["reasons"][0])
    scan_quality = {
        "skew_deg": max(skews, key=abs) if skews else None,
        "skew_ok": all(r["skew_ok"] is not False for r in checked),
        "clipped_edges": sorted({e for r in checked for e in r["clipped_edges"]}),
        "clipped": any(r["clipped"] for r in checked),
        "valid": all(r["valid"] for r in checked),
        "reasons": reasons,
        "n_lines_used": sum(r["n_lines_used"] for r in checked),
        "page_count": page_count,
        "checked_pages": [r["page"] for r in checked],
        "bad_pages": [r["page"] for r in bad],
    }
    if not scan_quality["valid"]:
        logger.warning("%s: scan quality check failed: %s", pdf_path, scan_quality["reasons"])
    return scan_quality


SCAN_QUALITY_REJECTED = "scan_quality_rejected"


def extract_text(
    pdf_path: str | Path,
    min_chars_for_text_layer: int = 40,
    force_ocr: bool | None = None,
) -> tuple[str, str, str, float, dict | None]:
    scan_quality = check_scan_quality(pdf_path)

    # When EVERY page is a confirmed tilted or clipped scan, stop here,
    # before the digital text-layer check, Docling, or OCR ever run, so none
    # of those (Docling's models especially) get loaded on pages already known
    # to be bad. When only some pages are bad, extraction goes on: api.py
    # rejects the PDF only if a bad page turns out to be an invoice page (a
    # cut-off packing list or waybill does not matter). "Could not detect a
    # clear table grid" is NOT confirmed-bad -- it's inconclusive.
    bad_pages = (scan_quality or {}).get("bad_pages") or []
    if bad_pages and len(bad_pages) == scan_quality.get("page_count"):
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
                text, ocr_seconds = _add_ocr_of_textless_pages(pdf_path, text)
                method = "2d_layout_canvas+ocr_pages" if ocr_seconds is not None else "2d_layout_canvas"
                return text, "", method, ocr_seconds or 0.0, scan_quality
            logger.info(
                "Digital text layer in %s was too sparse (<%d chars), falling back to OCR",
                pdf_path, min_chars_for_text_layer,
            )
        else:
            logger.info("%s classified as scanned (sparse/garbage text layer), routing straight to OCR", pdf_path)
    else:
        logger.info("force_ocr=True requested for %s, skipping the digital text-layer check", pdf_path)

    ocr_text, ocr_tables, method, ocr_time = _ocr_pdf(pdf_path, min_chars_for_text_layer)
    return ocr_text, ocr_tables, method, ocr_time, scan_quality


def _ocr_pdf(pdf_path: str | Path, min_chars: int) -> tuple[str, str, str, float]:
    """Docling first; RapidOCR when Docling fails or finds fewer than min_chars."""
    docling_time = 0.0
    try:
        d_text, docling_time = _extract_via_docling(pdf_path)
        if len(d_text) >= min_chars:
            d_text, method, docling_time = _maybe_recover_garbled_ocr(
                pdf_path, d_text, "docling", docling_time,
            )
            return d_text, "", method, docling_time
        logger.warning(
            "%s: Docling produced <%d chars -- falling back to RapidOCR",
            pdf_path, min_chars,
        )
    except Exception as e:
        logger.warning("%s: Docling failed (%s) -- falling back to RapidOCR", pdf_path, e)

    ocr_text, ocr_tables, engine_type, ocr_time = _ocr(pdf_path)
    ocr_time = round(docling_time + ocr_time, 2)
    ocr_text, method, ocr_time = _maybe_recover_garbled_ocr(
        pdf_path, ocr_text, f"ocr_{engine_type}", ocr_time,
    )
    return ocr_text, ocr_tables, method, ocr_time


_TEXTLESS_PAGE_MAX_WORDS = 5
_ANY_PAGE_MARK_RE = re.compile(
    r"^(?:=+\s*PAGE\s+(\d+)\s+OF\s+\d+\s*\(.*?\)\s*=+|-{3}\s*Page\s+(\d+)\s*\(OCR\)\s*-{3})[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)


def _page_blocks(text: str) -> dict[int, tuple[str, str]]:
    """Page number -> (its whole block, banner included; the text under the banner)."""
    marks = list(_ANY_PAGE_MARK_RE.finditer(text))
    blocks: dict[int, tuple[str, str]] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        blocks[int(m.group(1) or m.group(2))] = (text[m.start():end].rstrip(), text[m.end():end].strip())
    return blocks


def _add_ocr_of_textless_pages(pdf_path: str | Path, text: str) -> tuple[str, float | None]:
    """OCR the pages of a text-layer PDF that have no text of their own -- say
    a scanned invoice behind a digital courier waybill -- and put each in its
    place in page order. Returns the text and the OCR seconds, or None when no
    page needed OCR. If OCR fails, the text layer alone is returned, as before."""
    pdf_path = Path(pdf_path)
    try:
        stats = _page_text_stats(pdf_path)
    except Exception as e:
        logger.warning("%s: could not look for pages without a text layer (%s) -- using the text layer only",
                       pdf_path, e)
        return text, None
    pages = [i for i, page in enumerate(stats)
             if page["valid_words"] < _TEXTLESS_PAGE_MAX_WORDS and page["has_content"]]
    if not pages:
        return text, None

    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.close()
    try:
        src = pdfium.PdfDocument(str(pdf_path))
        subset = pdfium.PdfDocument.new()
        try:
            subset.import_pages(src, pages)
            subset.save(tmp.name)
        finally:
            subset.close()
            src.close()
        ocr_text, _, method, seconds = _ocr_pdf(tmp.name, min_chars=10)
    except Exception as e:
        logger.warning("%s: OCR of text-less page(s) %s failed (%s) -- using the text layer only",
                       pdf_path, [p + 1 for p in pages], e)
        return text, None
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    ocr_pages = {n: body for n, (_, body) in _page_blocks(ocr_text).items()}
    if not ocr_pages and ocr_text.strip():
        ocr_pages = {1: ocr_text.strip()}
    logger.info("%s: page(s) %s have no text layer -- OCR'd with %s in %.1fs",
                pdf_path, [p + 1 for p in pages], method, seconds)

    blocks = {n: block for n, (block, _) in _page_blocks(text).items()}
    head = text[:min((m.start() for m in _ANY_PAGE_MARK_RE.finditer(text)), default=len(text))].strip()
    for position, page_index in enumerate(pages, 1):
        body = ocr_pages.get(position, "").strip()
        if body:
            number = page_index + 1
            blocks.setdefault(number, f"{'=' * 35} PAGE {number} OF {len(stats)} ({pdf_path.name}) {'=' * 35}\n{body}")
    merged = "\n\n".join(blocks[n] for n in sorted(blocks))
    return (f"{head}\n\n{merged}" if head else merged), seconds


if __name__ == "__main__":
    import sys
    t, tabs, m, ot, sq = extract_text(sys.argv[1])
    print(f"--- extracted via {m} ({len(t)} chars, ocr_time: {ot}s) ---")
    if sq:
        print(f"--- scan quality: valid={sq['valid']} skew={sq['skew_deg']} clipped={sq['clipped_edges']} ---")
    print(t[:10000])
