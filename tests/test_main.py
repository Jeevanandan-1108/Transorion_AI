from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from main import run
from invoice_extractor.pdf.reader import ClippedTableError


class _StopAfterGate(Exception):
    pass


def _run(pdf_path="fake.pdf", **kwargs):
    return asyncio.run(run(pdf_path, "gpt-5-nano", "out.json", **kwargs))


def test_clipped_scan_raises_before_extract_text():
    with patch("main.check_scan_quality", return_value={"clipped": True, "clipped_edges": ["left"], "valid": False, "reasons": ["x"]}), \
         patch("main.extract_text") as mock_extract_text:
        with pytest.raises(ClippedTableError):
            _run()
    mock_extract_text.assert_not_called()


def test_clipped_scan_allowed_with_override():
    with patch("main.check_scan_quality", return_value={"clipped": True, "clipped_edges": ["left"], "valid": False, "reasons": ["x"]}), \
         patch("main.extract_text", side_effect=_StopAfterGate):
        with pytest.raises(_StopAfterGate):
            _run(allow_clipped=True)


def test_unclipped_scan_proceeds_past_gate():
    with patch("main.check_scan_quality", return_value={"clipped": False, "clipped_edges": [], "valid": True, "reasons": []}), \
         patch("main.extract_text", side_effect=_StopAfterGate):
        with pytest.raises(_StopAfterGate):
            _run()


def test_no_scan_quality_result_proceeds_past_gate():
    with patch("main.check_scan_quality", return_value=None), \
         patch("main.extract_text", side_effect=_StopAfterGate):
        with pytest.raises(_StopAfterGate):
            _run()
