import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import services
from app.main import app
from tests.conftest import frame

TODAY = dt.date.today()
DAYS = [TODAY - dt.timedelta(days=99 - i) for i in range(100)]
D = lambda i: DAYS[i].isoformat()


@pytest.fixture
def client(session, fake):
    services.bump_revision()
    fake.frames["ILS=X"] = frame([(d.isoformat(), 3.7) for d in DAYS])
    # SPY +10% over the window, AVGO +30%, TSLA -20%, all linear
    lin = lambda a, b: [(d.isoformat(), a + (b - a) * i / 99) for i, d in enumerate(DAYS)]
    fake.frames["SPY"] = frame(lin(100, 110))
    fake.frames["QQQ"] = frame(lin(100, 110))
    fake.frames["AVGO"] = frame(lin(100, 130))
    fake.frames["TSLA"] = frame(lin(100, 80))
    c = TestClient(app)
    for name in ("Keren", "Shir", "Dad"):
        c.post("/api/portfolios", json={"name": name})
    return c


def ids(c):
    return {p["name"]: p["id"] for p in c.get("/api/portfolios").json()}


def test_one_command_several_portfolios_logs_one_call(client):
    p = ids(client)
    for name, alias in (("Keren", "קרן"), ("Shir", "שיר"), ("Dad", "אבא")):
        client.put(f"/api/portfolios/{p[name]}", json={"name": name, "aliases": alias})
    d = client.post("/api/omnibox", json={"text": f"קרן, שיר ואבא קנו 5 AVGO {DAYS[0].strftime('%d/%m/%Y')}"}).json()
    assert [t["portfolio_name"] for t in d["targets"]] == ["Keren", "Shir", "Dad"] and d["missing"] == []
    out = client.post("/api/omnibox/commit", json={"draft": d, "log_call": True}).json()
    assert len(out["saved"]) == 3 and out["rec_id"]
    calls = client.get("/api/calls").json()
    row = calls["rows"][0]
    assert row["ticker"] == "AVGO" and sorted(row["portfolios"]) == ["Dad", "Keren", "Shir"]
    # AVGO +30% vs SPY +10% over the same days -> edge +20 points, a call that worked
    assert abs(row["return"] - 0.30) < 1e-9 and abs(row["spy"] - 0.10) < 1e-9 and abs(row["edge"] - 0.20) < 1e-9 and row["worked"]


def test_everyone_sold_sells_whole_positions_and_closes_the_call(client):
    p = ids(client)
    for name, q in (("Keren", 4), ("Shir", 2)):
        d = client.post("/api/omnibox", json={"text": f"{name} bought {q} TSLA {DAYS[0].strftime('%d/%m/%Y')}"}).json()
        client.post("/api/omnibox/commit", json={"draft": d, "log_call": name == "Keren"})
    d = client.post("/api/omnibox", json={"text": f"כולם מכרו TSLA {DAYS[50].strftime('%d/%m/%Y')}"}).json()
    by = {t["portfolio_name"]: t for t in d["targets"]}
    assert by["Keren"]["quantity"] == 4 and by["Shir"]["quantity"] == 2 and by["Dad"]["skip"]
    out = client.post("/api/omnibox/commit", json={"draft": d, "log_call": True}).json()
    assert len(out["saved"]) == 2 and len(out["failed"]) == 2  # Dad and the empty default portfolio skipped
    assert client.get(f"/api/holdings?portfolio={p['Keren']}").json()["rows"][0]["closed"]
    rows = {r["action"]: r for r in client.get("/api/calls").json()["rows"]}
    buy, sell = rows["BUY"], rows["SELL"]
    assert buy["closed_date"] == D(50)
    # BUY measured day 0 -> 50: TSLA 100 -> ~89.9, SPY 100 -> ~105.05
    t50, s50 = 100 - 20 * 50 / 99, 100 + 10 * 50 / 99
    assert abs(buy["return"] - (t50 / 100 - 1)) < 1e-9 and abs(buy["spy"] - (s50 / 100 - 1)) < 1e-9 and not buy["worked"]
    # SELL measured day 50 -> today: TSLA kept falling while SPY rose, so selling worked
    assert abs(sell["edge"] - ((110 / s50 - 1) - (80 / t50 - 1))) < 1e-9 and sell["worked"]
    card = client.get("/api/calls").json()["scorecard"]
    assert card["all"]["scored"] == 2 and card["all"]["worked"] == 1 and card["avg_days_held"] == 50


def test_realized_from_sales_and_spreadsheet_lines(client):
    p = ids(client)["Keren"]
    client.post(f"/api/transactions?portfolio={p}", json={"ticker": "AVGO", "type": "BUY", "date": D(0), "quantity": 10, "price": 100, "currency": "USD"})
    client.post(f"/api/transactions?portfolio={p}", json={"ticker": "AVGO", "type": "SELL", "date": D(99), "quantity": 4, "price": 130, "currency": "USD"})
    csv = "Keren,,,\n,cost rate,current rate,amount\nAVGO,100,130,6\ntsla,-1565\nmsft 18/07,563.8\n"
    prev = client.post(f"/api/holdings/import?portfolio={p}", files={"file": ("k.csv", csv)}).json()
    assert len(prev["realized"]) == 2 and prev["realized"][1]["ticker"] == "MSFT" and prev["realized"][1]["note"] == "18/07"
    client.post(f"/api/holdings/import?portfolio={p}&commit=true", files={"file": ("k.csv", csv)})  # adds on top (no replace)
    s = client.get(f"/api/portfolio?portfolio={p}&range=MAX").json()["summary"]
    # sold 4 at 130 bought at 100 -> +120, plus -1565 + 563.8 from the sheet
    assert abs(s["realized"] - (120 - 1565 + 563.8)) < 1e-6


def test_reports_in_hebrew(client):
    p = ids(client)["Dad"]
    client.post(f"/api/transactions?portfolio={p}", json={"ticker": "AVGO", "type": "BUY", "date": D(0), "quantity": 1, "price": 100, "currency": "USD"})
    r = client.get(f"/api/report?portfolio={p}&period=week").json()
    assert "Dad" in r["text"] and "S&P 500" in r["text"] and "לפני המדד" in r["text"]
    a = client.get("/api/report?all=true&period=month").json()
    assert "1 מתוך 1 לפני המדד" in a["text"]
