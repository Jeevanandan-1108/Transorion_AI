from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

ShipmentType = Literal["import", "export", "unknown"]


class AmbiguousShipmentTypeError(RuntimeError):
    pass


_EXPORT_STRONG = (
    "shipping bill", "let export order", "export invoice", "drawback",
    "export tax invoice", "expwp", "expwop",
)
_EXPORT_WEAK = ("rodtep", "meis", "egm")

_IMPORT_STRONG = (
    "bill of entry", "boe no", "into bond", "warehouse bond",
    "import invoice", "customs duty payable", "import tax invoice",
)
_IMPORT_WEAK = ("igm", "bcd", "cvd", "swc")

_STRONG_POINTS = 3
_WEAK_POINTS = 1
_STRUCTURAL_POINTS = _STRONG_POINTS
_DECISION_MARGIN = 3

_COLUMN_BOUNDARY_MARKER = "│"

_ORIGIN_LABEL_RE = re.compile(
    r"country\s+of\s+origin(?:\s+of\s+\w+)?|exporter\s+country|"
    r"country\s+of\s+loading(?:\s+of\s+\w+)?|country\s+of\s+export|"
    r"port\s+of\s+loading|port\s+of\s+export",
    re.IGNORECASE,
)
_DEST_LABEL_RE = re.compile(
    r"country\s+of\s+(?:final\s+)?dest(?:ination)?\.?|port\s+of\s+discharge|"
    r"final\s+destination|port\s+of\s+import",
    re.IGNORECASE,
)

_COUNTRY_NAMES = (
    "india", "usa", "united states", "uk", "united kingdom", "germany",
    "singapore", "china", "maldives", "malaysia", "japan", "france",
    "italy", "spain", "netherlands", "belgium", "canada", "australia",
    "thailand", "vietnam", "indonesia", "korea", "taiwan", "mexico",
    "brazil", "switzerland", "uae", "saudi arabia", "hong kong",
    "south africa", "nigeria", "kenya", "egypt", "turkey", "russia",
    "poland", "sweden", "norway", "denmark", "finland", "austria",
    "portugal", "ireland", "greece", "israel", "qatar", "kuwait",
    "oman", "bangladesh", "sri lanka", "nepal", "pakistan", "myanmar",
    "philippines", "new zealand", "argentina", "chile", "colombia",
    "peru", "ukraine", "czech republic", "romania", "hungary",
)
_COUNTRY_NAME_RE = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in _COUNTRY_NAMES) + r")\b",
    re.IGNORECASE,
)

_COUNTRY_NAME_PREFIX_RE = re.compile(
    r"(republic\s+of|kingdom\s+of|state\s+of|sultanate\s+of|principality\s+of|"
    r"united\s+states\s+of|people'?s\s+republic\s+of)\s*$",
    re.IGNORECASE,
)

_BUYER_LABELS = ("customer", "buyer", "consignee", "bill to", "ship to",
                 "sold to", "invoice address", "invoice to", "invoices address",
                 "delivery address", "importer", "receiver")
_SUPPLIER_LABELS = ("supplier", "seller", "exporter", "shipper", "sold by",
                    "vendor", "manufacturer", "ship from")
_LABEL_PROXIMITY_WINDOW = 900
_LABEL_PROXIMITY_POINTS = _STRONG_POINTS

_SINGLE_LABEL_SEARCH_WINDOW = 150


_REFERENCE_FIELD_RE = re.compile(
    r"^\s*(?:'?s)?\s*(order|ref\w*|po\b|invoice|date)\b", re.IGNORECASE,
)


def _is_reference_field_label(text_lower: str, match_end: int) -> bool:
    return bool(_REFERENCE_FIELD_RE.match(text_lower[match_end:match_end + 20]))


_WORD_RE = re.compile(r"[a-z]{2,}")


def _starts_its_own_cell(text_lower: str, match_start: int) -> bool:
    line_start = text_lower.rfind("\n", 0, match_start) + 1
    col_start = text_lower.rfind(_COLUMN_BOUNDARY_MARKER, line_start, match_start)
    cell_start = col_start + 1 if col_start != -1 else line_start
    return not _WORD_RE.search(text_lower[cell_start:match_start])


def _label_positions(text_lower: str, labels: tuple[str, ...]) -> list[tuple[int, int]]:
    positions = []
    for label in labels:
        pattern = r"[\s\-]+".join(re.escape(w) for w in label.split())
        for m in re.finditer(pattern, text_lower):
            if not _is_reference_field_label(text_lower, m.end()) and _starts_its_own_cell(text_lower, m.start()):
                positions.append((m.start(), m.end()))
    return positions


def _label_proximity_hits(
    text_lower: str,
    labels: tuple[str, ...],
    other_side_starts: list[int] = (),
) -> int:
    hits = 0
    for label in labels:
        pattern = r"[\s\-]+".join(re.escape(w) for w in label.split())
        for m in re.finditer(pattern, text_lower):
            if _is_reference_field_label(text_lower, m.end()):
                continue
            if not _starts_its_own_cell(text_lower, m.start()):
                continue
            window_end = m.end() + _LABEL_PROXIMITY_WINDOW
            next_other = min((p for p in other_side_starts if p > m.end()), default=None)
            if next_other is not None:
                window_end = min(window_end, next_other)
            origin_or_dest = _ORIGIN_LABEL_RE.search(text_lower, m.end())
            dm = _DEST_LABEL_RE.search(text_lower, m.end())
            if dm is not None and (origin_or_dest is None or dm.start() < origin_or_dest.start()):
                origin_or_dest = dm
            if origin_or_dest is not None:
                window_end = min(window_end, origin_or_dest.start())
            window = _same_column_window(text_lower, m.end(), window_end)
            if _has_own_india(window):
                hits += 1
                break
    return hits


def _same_column_window(text: str, start: int, end: int) -> str:
    line_start = text.rfind("\n", 0, start) + 1
    col_idx = text.count(_COLUMN_BOUNDARY_MARKER, line_start, start)

    parts: list[str] = []
    pos = start
    first = True
    while pos < end:
        nl = text.find("\n", pos, end)
        line_end = nl if nl != -1 else end
        line = text[pos:line_end]
        if first:
            parts.append(line)
        elif _COLUMN_BOUNDARY_MARKER in line:
            segments = line.split(_COLUMN_BOUNDARY_MARKER)
            parts.append(segments[col_idx] if col_idx < len(segments) else "")
        else:
            parts.append(line)
        first = False
        if nl == -1:
            break
        pos = line_end + 1
    return "\n".join(parts)


_DESTINATION_PREFIX_RE = re.compile(r"\bto\s*$", re.IGNORECASE)


def _has_own_india(window: str) -> bool:
    for m in re.finditer(r"\bindia\b", window):
        prefix = window[max(0, m.start() - 6):m.start()]
        if not _DESTINATION_PREFIX_RE.search(prefix):
            return True
    return False


def _fallback_pairing_is_reliable(gap: str) -> bool:
    residue = gap.strip().replace(_COLUMN_BOUNDARY_MARKER, "").strip()
    residue = _COUNTRY_NAME_PREFIX_RE.sub("", residue).strip()
    return residue == ""


def _origin_destination_signal(text: str) -> tuple[bool, bool]:
    origin_match = _ORIGIN_LABEL_RE.search(text)
    dest_match = _DEST_LABEL_RE.search(text)
    if not origin_match and not dest_match:
        return False, False

    def _nearest_country(after: int, bound: int | None) -> str | None:
        limit = len(text) if bound is None else min(len(text), bound)
        m = _COUNTRY_NAME_RE.search(text, after, limit) if limit > after else None
        return m.group(1).lower() if m else None

    origin_country = None
    dest_country = None

    if origin_match and dest_match:
        origin_bound = dest_match.start() if dest_match.start() > origin_match.end() else None
        dest_bound = origin_match.start() if origin_match.start() > dest_match.end() else None
        origin_country = _nearest_country(origin_match.end(), origin_bound)
        dest_country = _nearest_country(dest_match.end(), dest_bound)

        if origin_country is None or dest_country is None:
            start = min(origin_match.start(), dest_match.start())
            country_matches = list(_COUNTRY_NAME_RE.finditer(text[start:]))
            if len(country_matches) >= 2:
                gap = text[start + country_matches[0].end():start + country_matches[1].start()]
                if _fallback_pairing_is_reliable(gap):
                    found = [m.group(1).lower() for m in country_matches[:2]]
                    if origin_match.start() <= dest_match.start():
                        origin_country, dest_country = found[0], found[1]
                    else:
                        dest_country, origin_country = found[0], found[1]
    elif origin_match:
        origin_country = _nearest_country(origin_match.end(), origin_match.end() + _SINGLE_LABEL_SEARCH_WINDOW)
    else:
        dest_country = _nearest_country(dest_match.end(), dest_match.end() + _SINGLE_LABEL_SEARCH_WINDOW)

    return origin_country == "india", dest_country == "india"


def _phrase_present(text_lower: str, phrase: str) -> bool:
    if " " in phrase:
        pattern = r"\s+".join(re.escape(w) for w in phrase.split())
        return re.search(pattern, text_lower) is not None
    return re.search(rf"\b{re.escape(phrase)}\b", text_lower) is not None


def _count(text_lower: str, phrases: tuple[str, ...], weak: bool) -> int:
    points = _WEAK_POINTS if weak else _STRONG_POINTS
    return sum(points for phrase in phrases if _phrase_present(text_lower, phrase))


@dataclass
class DetectionResult:
    shipment_type: ShipmentType
    export_score: int
    import_score: int
    signal: Literal["keyword", "structural", "both", "none"] = "none"
    matched: list[str] = field(default_factory=list)


def detect_shipment_type(text: str) -> DetectionResult:
    text_lower = text.lower()
    matched: list[str] = []

    export_score = _count(text_lower, _EXPORT_STRONG, weak=False)
    export_score += _count(text_lower, _EXPORT_WEAK, weak=True)
    import_score = _count(text_lower, _IMPORT_STRONG, weak=False)
    import_score += _count(text_lower, _IMPORT_WEAK, weak=True)

    keyword_hit = export_score > 0 or import_score > 0
    for phrase in (*_EXPORT_STRONG, *_EXPORT_WEAK):
        if _phrase_present(text_lower, phrase):
            matched.append(f"export:{phrase}")
    for phrase in (*_IMPORT_STRONG, *_IMPORT_WEAK):
        if _phrase_present(text_lower, phrase):
            matched.append(f"import:{phrase}")

    structural_hit = False
    origin_is_india, dest_is_india = _origin_destination_signal(text)
    if origin_is_india and not dest_is_india:
        export_score += _STRUCTURAL_POINTS
        structural_hit = True
        matched.append("structural:origin=IN,destination=foreign")
    elif dest_is_india and not origin_is_india:
        import_score += _STRUCTURAL_POINTS
        structural_hit = True
        matched.append("structural:destination=IN,origin=foreign")

    buyer_starts = [start for start, _ in _label_positions(text_lower, _BUYER_LABELS)]
    supplier_starts = [start for start, _ in _label_positions(text_lower, _SUPPLIER_LABELS)]
    buyer_india = _label_proximity_hits(text_lower, _BUYER_LABELS, other_side_starts=supplier_starts)
    supplier_india = _label_proximity_hits(text_lower, _SUPPLIER_LABELS, other_side_starts=buyer_starts)
    if buyer_india and not supplier_india:
        import_score += _LABEL_PROXIMITY_POINTS
        structural_hit = True
        matched.append("structural:buyer/consignee address is in India")
    elif supplier_india and not buyer_india:
        export_score += _LABEL_PROXIMITY_POINTS
        structural_hit = True
        matched.append("structural:supplier/exporter address is in India")

    if keyword_hit and structural_hit:
        signal: Literal["keyword", "structural", "both", "none"] = "both"
    elif keyword_hit:
        signal = "keyword"
    elif structural_hit:
        signal = "structural"
    else:
        signal = "none"

    diff = export_score - import_score
    if diff >= _DECISION_MARGIN:
        shipment_type: ShipmentType = "export"
    elif -diff >= _DECISION_MARGIN:
        shipment_type = "import"
    else:
        shipment_type = "unknown"

    return DetectionResult(
        shipment_type=shipment_type,
        export_score=export_score,
        import_score=import_score,
        signal=signal,
        matched=matched,
    )


if __name__ == "__main__":
    import sys
    sample = Path(sys.argv[1]).read_text(encoding="utf-8", errors="ignore") if len(sys.argv) > 1 else ""
    print(detect_shipment_type(sample))
