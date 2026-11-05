"""P0-5: range columns carry their period. The header is built from holdings.range (code + start),
so a range change must change both."""
from pathlib import Path

from tests.test_portfolios import client, days  # noqa: F401 (fixture)


def test_holdings_range_follows_the_selection(client):
    client.post("/api/transactions?portfolio=1", json={"ticker": "AAPL", "type": "BUY", "date": days(400)[0].isoformat(), "quantity": 1, "price": 50, "currency": "USD"})
    mx = client.get("/api/holdings?portfolio=1&range=MAX").json()["range"]
    m1 = client.get("/api/holdings?portfolio=1&range=1M").json()["range"]
    assert mx["code"] == "MAX" and m1["code"] == "1M" and m1["start"] > mx["start"]


def test_headers_name_their_period():
    src = (Path(__file__).resolve().parents[2] / "frontend/src/pages/Holdings.tsx").read_text()
    assert 'label: "Gain since bought"' in src and "{c.label}, {period.name}" in src and "period.since" in src
