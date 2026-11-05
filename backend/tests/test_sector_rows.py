"""P0-11: no 0% sector rows; under 1% grouped as Other."""
from app.services import sector_rows


def test_tiny_sectors_grouped_and_zero_rows_hidden():
    rows = sector_rows({"Technology": 0.6, "Healthcare": 0.385, "Utilities": 0.006, "Energy": 0.004, "Real Estate": 0.001,
                        "Basic Materials": 0.0001, "Cash": 0.0039})
    names = [r["sector"] for r in rows]
    assert names == ["Technology", "Healthcare", "Other"]
    assert abs(rows[-1]["weight"] - (0.006 + 0.004 + 0.001 + 0.0001 + 0.0039)) < 1e-12
    assert all(round(r["weight"] * 100) >= 1 for r in rows)


def test_other_hidden_when_it_rounds_to_zero():
    assert [r["sector"] for r in sector_rows({"Technology": 0.997, "Energy": 0.003})] == ["Technology"]
