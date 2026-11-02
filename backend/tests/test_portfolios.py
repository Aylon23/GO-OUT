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
    c = TestClient(app)
    c.put("/api/settings", json={"base_currency": "ILS"})  # these cases are written in shekels
    return c


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
    mine = rows["My portfolio"]
    assert abs(mine["cost_gain_pct"] - 0.5) < 1e-9
    # real results only: imported today, so the period starts today and nothing has moved yet
    assert mine["short_history"] and abs(mine["return"]) < 1e-9
    assert "p1" in o["chart"]["perf"] and "SPY" in o["chart"]["perf"]
    assert abs(o["total_value"] - 6000) < 1e-6


def test_overview_real_return_vs_spy_same_days(client):
    n = 399
    aapl = lambda i: 50 * (1 + 2 * i / n)
    spy = lambda i: 100 * (1 + i / n)
    start = days(400)[100].isoformat()
    client.post("/api/transactions?portfolio=1", json={"ticker": "AAPL", "type": "BUY", "date": start, "quantity": 1, "price": aapl(100), "currency": "USD"})
    o = client.get("/api/overview?range=MAX").json()
    row = o["rows"][0]
    # the day bought counts at the close, so the return runs from that close to today
    assert abs(row["return"] - (aapl(n) / aapl(100) - 1)) < 1e-9
    assert abs(row["bench"]["SPY"] - (spy(n) / spy(100) - 1)) < 1e-9  # same days: from the close of the buy day


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


def test_deleted_default_portfolio_stays_deleted(client):
    from app import db
    client.post("/api/portfolios", json={"name": "Dad"})
    assert client.delete("/api/portfolios/1").status_code == 200
    db.migrate(db.get_engine())  # what happens on every app start
    assert [p["name"] for p in client.get("/api/portfolios").json()] == ["Dad"]


def test_overview_since_tracking_spark_activity_and_insights(client):
    ds = days(400)
    p2 = client.post("/api/portfolios", json={"name": "Keren"}).json()["id"]
    p3 = client.post("/api/portfolios", json={"name": "Shir"}).json()["id"]
    # My portfolio: all AAPL (one stock, 100%); Keren: AAPL + TEVA.TA; Shir: started 5 days ago
    client.post("/api/transactions?portfolio=1", json={"ticker": "AAPL", "type": "BUY", "date": ds[10].isoformat(), "quantity": 10, "price": 40, "currency": "USD"})
    client.post(f"/api/transactions?portfolio={p2}", json={"ticker": "AAPL", "type": "BUY", "date": ds[100].isoformat(), "quantity": 2, "price": 200, "currency": "USD"})
    client.post(f"/api/transactions?portfolio={p2}", json={"ticker": "TEVA.TA", "type": "BUY", "date": ds[120].isoformat(), "quantity": 100, "price": 50, "currency": "ILS"})
    client.post(f"/api/transactions?portfolio={p3}", json={"ticker": "QQQ", "type": "BUY", "date": ds[-5].isoformat(), "quantity": 1, "price": 100, "currency": "USD"})
    o = client.get("/api/overview?range=1M").json()
    rows = {r["name"]: r for r in o["rows"]}
    me = rows["My portfolio"]
    # since tracking: AAPL from day 10 vs SPY over exactly the same days, independent of the 1M range
    a10, a_end = 50 * (1 + 2 * 10 / 399), 150
    s10, s_end = 100 * (1 + 10 / 399), 200
    assert me["since_return"] == pytest.approx(a_end / a10 - 1, rel=1e-6)
    assert me["since_spy"] == pytest.approx(s_end / s10 - 1, rel=1e-6)
    assert me["since_vs_spy"] == pytest.approx(me["since_return"] - me["since_spy"])
    assert me["trading_days"] == 389 and me["spark"][0] == pytest.approx(100) and len(me["spark"]) <= 45
    assert me["last_activity"] == ds[10].isoformat()
    assert me["cost_gain_usd"] == pytest.approx(10 * (150 - 40), rel=1e-6)  # base is ILS; shown in dollars
    assert rows["Shir"]["trading_days"] < o["min_trading_days"]
    kinds = {i["kind"]: i for i in o["insights"]}
    assert kinds["winner"]["symbol"] == "AAPL" and "My portfolio and Keren" in kinds["winner"]["text"]
    assert kinds["shared"]["symbol"] == "AAPL" and "2 of 3" in kinds["shared"]["text"]
    assert any(i["kind"] == "stock" and "My portfolio has 100% in AAPL" in i["text"] for i in o["insights"])
    assert 3 <= len(o["insights"]) <= 5


def test_overview_benchmarks_cover_the_oldest_portfolio(client):
    ds = days(400)
    client.post("/api/transactions?portfolio=1", json={"ticker": "AAPL", "type": "BUY", "date": ds[10].isoformat(), "quantity": 1, "price": 51, "currency": "USD"})
    p2 = client.post("/api/portfolios", json={"name": "New"}).json()["id"]  # listed last, started last week
    client.post(f"/api/transactions?portfolio={p2}", json={"ticker": "QQQ", "type": "BUY", "date": ds[-6].isoformat(), "quantity": 1, "price": 100, "currency": "USD"})
    o = client.get("/api/overview?range=MAX").json()
    spy = [v for v in o["chart"]["perf"]["SPY"] if v is not None]
    assert len(spy) > 350 and spy[0] == pytest.approx(100)
    assert spy[-1] == pytest.approx(200 / (100 * (1 + 10 / 399)) * 100, rel=1e-6)  # SPY from the oldest start, rebased
