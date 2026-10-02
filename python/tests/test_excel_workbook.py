"""Excel reconciliation smoke test: skipped when LibreOffice is not installed."""
import json
import tempfile
from pathlib import Path
import pytest
from waterfall import config as cfg, reconcile_excel as rx

pytestmark = pytest.mark.skipif(not any(Path(p).exists() for p in rx.SOFFICE_CANDIDATES), reason="LibreOffice not installed")


def test_base_case_matches_python():
    meta = json.loads(rx.META.read_text())
    wbv = rx.recalc(rx.XLSX, cfg.BASE, 0, 0, 1, meta, Path(tempfile.mkdtemp()))
    res = rx.compare("Base", cfg.BASE, 0, 0, wbv, meta)
    assert res["passed"], res["issues"]
    assert res["worst_cash_diff"] < rx.TOL_CASH


def test_workbook_creator_and_no_stray_constants():
    import openpyxl
    wb = openpyxl.load_workbook(rx.XLSX)
    assert wb.properties.creator == "Deepak Chaudhary"
    for name in ("Pool", "Waterfall", "CashFlows"):
        ws = wb[name]
        for row in ws.iter_rows(min_row=6):
            for c in row:
                assert isinstance(c.value, str) and c.value.startswith("="), (name, c.coordinate)
