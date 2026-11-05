"""P0-14: the per-person reports live in Settings, not on All portfolios."""
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "frontend/src/pages"


def test_reports_moved_to_settings():
    assert "PeriodicReports" not in (SRC / "AllPortfolios.tsx").read_text()
    assert "<PeriodicReports />" in (SRC / "Settings.tsx").read_text()
