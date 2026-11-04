"""P0-2: one display currency per screen. On Holdings every amount column is in the display currency;
only Price and Avg cost stay in the stock's own currency (headed "(local)" in the UI)."""
from pathlib import Path

from tests.test_portfolios import client  # noqa: F401 (fixture)

RATE = 4.0  # ILS=X in the fixture


def test_holdings_amounts_follow_the_display_currency(client):
    client.post("/api/holdings/import?portfolio=1&commit=true", files={"file": ("h.csv", "ticker,quantity,avg_price\nAAPL,10,100\n")})
    d = client.get("/api/holdings?portfolio=1&range=MAX").json()
    assert d["base"] == "ILS"
    row = next(r for r in d["rows"] if r["ticker"] == "AAPL")
    assert row["currency"] == "USD" and row["opening"]
    # Unrealized follows the display currency (it used to show the dollar amount for imported positions)
    assert abs(row["unrealized"] - row["unrealized_native"] * RATE) < 1e-6
    assert abs(row["value"] - row["quantity"] * row["price"] * RATE) < 1e-6
    # Price and Avg cost stay local
    assert row["price"] < row["value"] / row["quantity"]


def test_holdings_screen_uses_base_for_every_amount_but_price_and_cost():
    src = (Path(__file__).resolve().parents[2] / "frontend/src/pages/Holdings.tsx").read_text()
    assert 'label: "Avg cost (local)"' in src and 'label: "Price (local)"' in src
    local = [ln for ln in src.splitlines() if "money(" in ln and "r.currency" in ln and "row.currency" not in ln and "t.currency" not in ln]
    # the only money() calls in the stock's own currency are price and average cost
    assert local and all(("r.price" in ln or "r.avg_cost" in ln) for ln in local), local
    assert "unrealized_native" not in src
