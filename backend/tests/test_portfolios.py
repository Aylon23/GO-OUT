import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import services
from app.main import app
from tests.conftest import frame

TODAY = dt.date.today()


def days(n):
    start = TODAY - dt.timedelta(days=n - 1)
    return [start + dt.timedelta(days=i) for i in range(n)]


@pytest.fixture
def client(session, fake):
    services.bump_revision()
    ds = days(400)
    fake.frames["ILS=X"] = frame([(d.isoformat(), 4.0) for d in ds])
    # SPY doubles over the window, QQQ flat, AAPL triples, TEVA.TA (agorot) flat at 5000
    n = len(ds)
    fake.frames["SPY"] = frame([(d.isoformat(), 100 * (1 + i / (n - 1))) for i, d in enumerate(ds)])
    fake.frames["QQQ"] = frame([(d.isoformat(), 100) for d in ds])
    fake.frames["AAPL"] = frame([(d.isoformat(), 50 * (1 + 2 * i / (n - 1))) for i, d in enumerate(ds)])
    fake.frames["TEVA.TA"] = frame([(d.isoformat(), 5000) for d in ds])
    return TestClient(app)


def test_portfolios_are_separate(client):
    p2 = client.post("/api/portfolios", json={"name": "Dad", "note": "Low risk"}).json()
    client.post("/api/transactions?portfolio=1", json={"ticker": "AAPL", "type": "BUY", "date": days(400)[10].isoformat(), "quantity": 1, "price": 51, "currency": "USD"})
    client.post(f"/api/transactions?portfolio={p2['id']}", json={"ticker": "AAPL", "type": "BUY", "date": days(400)[10].isoformat(), "quantity": 5, "price": 51, "currency": "USD"})
    assert len(client.get("/api/transactions?portfolio=1").json()) == 1
    assert client.get(f"/api/transactions?portfolio={p2['id']}").json()[0]["quantity"] == 5
    v1 = client.get("/api/portfolio?portfolio=1&range=MAX").json()["summary"]["value"]
    v2 = client.get(f"/api/portfolio?portfolio={p2['id']}&range=MAX").json()["summary"]["value"]
    assert abs(v2 - 5 * v1) < 1e-6
    names = [p["name"] for p in client.get("/api/portfolios").json()]
    assert names == ["My portfolio", "Dad"]
    client.delete(f"/api/portfolios/{p2['id']}")
    assert len(client.get("/api/portfolios").json()) == 1
    assert client.delete("/api/portfolios/1").status_code == 400  # keep at least one


def test_holdings_import_with_average_cost_and_agorot(client):
    csv = "symbol,quantity,avg price,currency\nAAPL,10,100,USD\nTEVA.TA,100,4000,\nNOPE,1,1,USD\n"
    prev = client.post("/api/holdings/import?portfolio=1", files={"file": ("h.csv", csv)}).json()
    assert prev["valid"] == 2 and prev["invalid"] == 1
    teva = next(r for r in prev["rows"] if r["ticker"] == "TEVA.TA")
    assert teva["avg_price"] == 40.0 and teva["currency"] == "ILS" and teva["notes"]  # agorot -> shekels
    client.post("/api/holdings/import?portfolio=1&commit=true", files={"file": ("h.csv", csv)})
    h = client.get("/api/holdings?portfolio=1&range=1M").json()
    aapl = next(r for r in h["rows"] if r["ticker"] == "AAPL")
    # AAPL ends at 150 USD, average cost 100 -> +50%; cost converted at 4.0 ILS/USD
    assert abs(aapl["unrealized_pct"] - 0.5) < 1e-9
    assert abs(aapl["avg_cost"] - 100) < 1e-9
    # performance starts from market value, so no fake jump on the import day
    p = client.get("/api/portfolio?portfolio=1&range=MAX").json()
    assert abs(p["summary"]["twr"]) < 1e-9


def test_lookback_holds_current_shares(client):
    csv = "ticker,quantity,avg_price\nAAPL,10,100\n"
    client.post("/api/holdings/import?portfolio=1&commit=true", files={"file": ("h.csv", csv)})
    lb = client.get("/api/lookback?portfolio=1&range=MAX").json()
    # AAPL 50 -> 150 over the window: +200%; SPY 100 -> 200: +100%; QQQ flat
    assert abs(lb["summary"]["return"] - 2.0) < 1e-9
    bm = {b["symbol"]: b["twr"] for b in lb["summary"]["benchmarks"]}
    assert abs(bm["SPY"] - 1.0) < 1e-9 and abs(bm["QQQ"]) < 1e-9
    assert abs(lb["cost"]["gain_pct"] - 0.5) < 1e-9
    assert abs(lb["summary"]["end_value"] - 10 * 150 * 4.0) < 1e-6  # today's value in shekels


def test_overview_lists_every_portfolio(client):
    p2 = client.post("/api/portfolios", json={"name": "Mom"}).json()
    client.post("/api/holdings/import?portfolio=1&commit=true", files={"file": ("h.csv", "ticker,quantity,avg_price\nAAPL,10,100\n")})
    o = client.get("/api/overview?range=1Y").json()
    rows = {r["name"]: r for r in o["rows"]}
    assert rows["Mom"]["empty"] and not rows["My portfolio"]["empty"]
    assert abs(rows["My portfolio"]["cost_gain_pct"] - 0.5) < 1e-9
    assert "p1" in o["chart"]["perf"] and "SPY" in o["chart"]["perf"]
    assert abs(o["total_value"] - 6000) < 1e-6


def test_routes_exist(client):
    assert client.post("/api/omnibox", json={"text": "bought 1 AAPL"}).headers["content-type"].startswith("application/json")
    assert client.get("/api/metrics?symbols=SPY").headers["content-type"].startswith("application/json")


def test_sectioned_sheet_creates_portfolios(client):
    # same shape as a family spreadsheet: a title row per portfolio, a header with an empty first
    # column, holdings, a totals row, then a list of realized gains without quantities
    csv = (
        "Dad,,,\n"
        ",cost rate,current rate,amount,cost value\n"
        "AAPL,100,150,10,1000\n"
        "SPY,150,200,4,600\n"
        'סה"כ,,,,1600\n'
        "tsla,-1565\n"
        "qqq,1375\n"
        "Keren,,,\n"
        ",cost rate,current rate,amount\n"
        "AAPL,120,150,5\n"
    )
    prev = client.post("/api/holdings/import?portfolio=1", files={"file": ("f.csv", csv)}).json()
    assert prev["valid"] == 3 and prev["invalid"] == 0
    assert {g["name"]: g["valid"] for g in prev["groups"]} == {"Dad": 2, "Keren": 1}
    client.post("/api/holdings/import?portfolio=1&commit=true", files={"file": ("f.csv", csv)})
    ps = {p["name"]: p for p in client.get("/api/portfolios").json()}
    assert ps["Dad"]["transactions"] == 2 and ps["Keren"]["transactions"] == 1
    assert ps["My portfolio"]["transactions"] == 0
    # importing an updated file with replace=true does not duplicate
    client.post("/api/holdings/import?portfolio=1&commit=true&replace=true", files={"file": ("f.csv", csv)})
    ps = {p["name"]: p for p in client.get("/api/portfolios").json()}
    assert ps["Dad"]["transactions"] == 2
