"""P0-3: a new install shows amounts in dollars; the Settings switch is the only way to shekels."""
from fastapi.testclient import TestClient

from app.main import app


def test_new_install_defaults_to_usd(session, fake):
    c = TestClient(app)
    assert c.get("/api/settings").json()["base_currency"] == "USD"
    assert c.get("/api/portfolio?portfolio=1").json()["base"] == "USD"
    c.put("/api/settings", json={"base_currency": "ILS"})
    assert c.get("/api/settings").json()["base_currency"] == "ILS"  # the choice sticks


def test_new_money_amount_defaults_to_usd():
    from app.api.routes import NewMoneyIn
    assert NewMoneyIn(amount=1000).currency == "USD"
