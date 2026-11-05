"""P0-6: a blank metric always says why."""
from pathlib import Path

from tests.test_portfolios import client, days  # noqa: F401 (fixture)


def summary(c):
    return c.get("/api/portfolio?portfolio=1&range=MAX").json()["summary"]


def test_xirr_blank_without_dated_flows_points_to_the_importer(client):
    client.post("/api/holdings/import?portfolio=1&commit=true", files={"file": ("h.csv", "ticker,quantity,avg_price\nAAPL,10,100\n")})
    s = summary(client)
    assert s["xirr"] is None and s["xirr_why"] == "XIRR needs dated cash flows. Import transaction history."


def test_xirr_blank_for_a_short_range_says_so(client):
    client.post("/api/transactions?portfolio=1", json={"ticker": "AAPL", "type": "BUY", "date": days(400)[-10].isoformat(), "quantity": 1, "price": 100, "currency": "USD"})
    s = summary(client)
    assert s["xirr"] is None and "at least a month" in s["xirr_why"]


def test_xirr_present_has_no_reason(client):
    client.post("/api/transactions?portfolio=1", json={"ticker": "AAPL", "type": "BUY", "date": days(400)[0].isoformat(), "quantity": 1, "price": 50, "currency": "USD"})
    s = summary(client)
    assert s["xirr"] is not None and s["xirr_why"] is None


def test_metric_screens_have_no_bare_dash():
    root = Path(__file__).resolve().parents[2] / "frontend/src"
    for f in ["pages/Dashboard.tsx", "pages/Compare.tsx", "pages/Council.tsx", "pages/Scenarios.tsx", "components/StressTests.tsx",
              "components/StatsTable.tsx", "components/Book.tsx", "components/AiDebate.tsx", "components/Exposures.tsx"]:
        src = (root / f).read_text()
        assert ': "–"}' not in src and '?? "–"}' not in src, f
