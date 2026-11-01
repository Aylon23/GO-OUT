import datetime as dt

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import services
from app.main import app
from tests.conftest import frame


def days(n, start=dt.date(2024, 1, 1)):
    return [start + dt.timedelta(days=i) for i in range(n)]


@pytest.fixture
def client(session, fake):
    services.bump_revision()
    ds = days(10)
    fake.frames["ILS=X"] = frame([(d.isoformat(), 3.5) for d in ds])
    fake.frames["SPY"] = frame([(d.isoformat(), 100 + i, 100 + i) for i, d in enumerate(ds)])
    fake.frames["QQQ"] = frame([(d.isoformat(), 100 + 2 * i, 100 + 2 * i) for i, d in enumerate(ds)])
    fake.frames["AAPL"] = frame([(d.isoformat(), 50 + i) for i, d in enumerate(ds)])
    fake.frames["TEVA.TA"] = frame([(d.isoformat(), 4000) for d in ds])
    return TestClient(app)


def test_validation_messages(client):
    r = client.post("/api/transactions", json={"ticker": "AAPL", "type": "BUY", "date": "2023-12-01", "quantity": 1, "price": 50, "currency": "USD"})
    assert r.status_code == 422 and "started trading on 1 Jan 2024" in r.json()["detail"]["fields"]["date"]
    client.post("/api/transactions", json={"ticker": "AAPL", "type": "BUY", "date": "2024-01-02", "quantity": 10, "price": 51, "currency": "USD"})
    r = client.post("/api/transactions", json={"ticker": "AAPL", "type": "SELL", "date": "2024-01-03", "quantity": 11, "price": 52, "currency": "USD"})
    assert r.status_code == 422 and "You held 10 AAPL" in r.json()["detail"]["fields"]["quantity"]
    r = client.post("/api/transactions", json={"ticker": "AAPL", "type": "SELL", "date": "2024-01-01", "quantity": 1, "price": 50, "currency": "USD"})
    assert r.status_code == 422  # sale before the buy


def test_price_on_date_agorot(client):
    r = client.get("/api/price/TEVA.TA", params={"date": "2024-01-05"}).json()
    assert r["close"] == 40.0 and r["currency"] == "ILS"


def test_portfolio_and_shadow(client):
    client.post("/api/transactions", json={"type": "DEPOSIT", "date": "2024-01-01", "price": 3500, "currency": "ILS"})
    client.post("/api/transactions", json={"ticker": "AAPL", "type": "BUY", "date": "2024-01-01", "quantity": 20, "price": 50, "currency": "USD"})
    p = client.get("/api/portfolio", params={"range": "MAX"}).json()
    assert not p["empty"]
    # AAPL 50 -> 59, cash 0 -> 20 * 59 * 3.5 = 4130
    assert abs(p["summary"]["value"] - 4130) < 1e-6
    assert abs(p["summary"]["twr"] - (59 / 50 - 1)) < 1e-9
    spy = next(r for r in p["shadow"]["rows"] if r["symbol"] == "SPY")
    assert abs(spy["value"] - 3500 * 109 / 100) < 1e-6  # same deposit into SPY
    assert p["series"]["perf"]["portfolio"][0] == 100.0


def test_csv_roundtrip(client):
    csv = "date,type,ticker,quantity,price,currency,fees,note\n2024-01-02,BUY,AAPL,5,51,USD,1,\n2024-01-03,SELL,AAPL,9,52,USD,0,\nbad,BUY,AAPL,1,1,USD,0,\n"
    r = client.post("/api/transactions/import", files={"file": ("t.csv", csv)}).json()
    assert r["valid"] == 1 and r["invalid"] == 2
    errs = {row["line"]: row["errors"] for row in r["rows"]}
    assert "quantity" in errs[3] and "date" in errs[4]
    r = client.post("/api/transactions/import", params={"commit": True}, files={"file": ("t.csv", csv)}).json()
    assert r["committed"]
    out = client.get("/api/transactions/export").text
    assert "2024-01-02,BUY,AAPL,5,51,USD,1," in out
