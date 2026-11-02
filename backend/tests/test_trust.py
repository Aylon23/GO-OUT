"""Section A: numbers must never lie."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import services
from app.main import app
from app.market.names import auto_aliases, clean_name
from tests.conftest import frame

TODAY = dt.date.today()
DAYS = [TODAY - dt.timedelta(days=59 - i) for i in range(60)]
lin = lambda a, b: [(d.isoformat(), a + (b - a) * i / 59) for i, d in enumerate(DAYS)]


@pytest.fixture
def client(session, fake):
    services.bump_revision()
    fake.frames["ILS=X"] = frame(lin(3.5, 4.0))  # the shekel weakens: FX must not leak into "gain vs cost"
    fake.frames["SPY"] = frame(lin(100, 110))
    fake.frames["QQQ"] = frame(lin(100, 110))
    fake.frames["AAA"] = frame(lin(100, 150))  # +50%
    fake.frames["BBB"] = frame(lin(100, 80))  # -20%
    fake.frames["XLK"] = frame(lin(100, 120))
    fake.funds = {"XLK": ({"technology": 0.9, "communication_services": 0.1}, "Technology"), "IBIT": ({}, "Digital Assets")}
    return TestClient(app)


def buy(c, t, day, q, px, pid=1):
    r = c.post(f"/api/transactions?portfolio={pid}", json={"ticker": t, "type": "BUY", "date": DAYS[day].isoformat(), "quantity": q, "price": px, "currency": "USD"})
    assert r.status_code == 200, r.text


def test_a1_each_holding_has_its_own_return_and_best_worst_use_it(client):
    buy(client, "AAA", 0, 10, 100)
    buy(client, "BBB", 0, 10, 100)
    h = client.get("/api/holdings?range=MAX").json()
    r = {x["ticker"]: x["range_return"] for x in h["rows"]}
    assert abs(r["AAA"] - 0.5) < 1e-9 and abs(r["BBB"] + 0.2) < 1e-9
    assert h["best"] == ["AAA"] and h["worst"] == ["BBB"]
    # bought mid-range: measured from its own buy day, not the range start
    buy(client, "AAA", 30, 1, 100 + 50 * 30 / 59)
    h = client.get("/api/holdings?range=MAX").json()
    assert abs({x["ticker"]: x["range_return"] for x in h["rows"]}["AAA"] - 0.5) < 1e-9


def test_a2_missing_price_is_not_a_loss(client, fake, session):
    from sqlmodel import delete
    from app.market.cache import clear_memory_cache
    from app.models import PriceCache, PriceCoverage

    buy(client, "AAA", 0, 10, 100)
    fake.frames["SEDG"] = frame(lin(20, 20))
    buy(client, "SEDG", 0, 5, 20)
    # later the fetch fails and nothing is cached (e.g. a fresh database copy): Yahoo has nothing
    del fake.frames["SEDG"]
    session.exec(delete(PriceCache).where(PriceCache.ticker == "SEDG"))
    session.exec(delete(PriceCoverage).where(PriceCoverage.ticker == "SEDG"))
    session.commit()
    clear_memory_cache()
    services.bump_revision()
    h = client.get("/api/holdings?range=MAX").json()
    sedg = next(x for x in h["rows"] if x["ticker"] == "SEDG")
    assert sedg["price_status"] == "missing" and sedg["price"] is None and sedg["range_return"] is None
    assert abs(sedg["value"] - 100) < 1e-6  # last known value kept: 5 x $20
    assert "SEDG" not in h["worst"] and any("SEDG" in w for w in h["warnings"])
    p = client.get("/api/portfolio?range=MAX").json()
    assert p["summary"]["value"] > 10 * 150  # the total still includes SEDG


def test_a6_a7_one_gain_number_everywhere_in_the_security_currency(client):
    client.put("/api/settings", json={"base_currency": "ILS"})
    services.bump_revision()
    buy(client, "AAA", 0, 10, 100)
    buy(client, "BBB", 10, 4, 100 - 20 * 10 / 59)
    client.post("/api/portfolios", json={"name": "Dad"})
    h = client.get("/api/holdings?portfolio=1&range=MAX").json()
    aaa = next(x for x in h["rows"] if x["ticker"] == "AAA")
    assert abs(aaa["unrealized_native"] - 500) < 1e-6  # (150 - 100) x 10, in dollars
    assert abs(aaa["unrealized"] - 500 * 4.0) < 1e-6  # converted at today's rate only
    assert abs(aaa["unrealized_pct"] - 0.5) < 1e-9
    rows_sum = sum(x["unrealized"] for x in h["rows"] if not x["closed"])
    summary = client.get("/api/portfolio?portfolio=1&range=1M").json()["summary"]["cost_gain"]
    overview = next(r for r in client.get("/api/overview?range=1Y").json()["rows"] if r["id"] == 1)["cost_gain"]
    assert abs(rows_sum - summary) < 1e-6 and abs(rows_sum - overview) < 1e-6


def test_a8_funds_are_looked_through_never_etf(client):
    client.post("/api/symbols/select", json={"symbol": "XLK", "name": "TECHNOLOGY SELECT SECTOR SPDR FUND", "type": "ETF", "currency": "USD"})
    buy(client, "XLK", 0, 10, 100)
    buy(client, "AAA", 0, 10, 100)
    h = client.get("/api/holdings?range=MAX").json()
    sectors = {s["sector"]: s["weight"] for s in h["sectors"]}
    assert "ETF" not in sectors
    w_xlk = next(x["weight"] for x in h["rows"] if x["ticker"] == "XLK")
    assert abs(sectors["Technology"] - 0.9 * w_xlk) < 1e-9
    assert abs(sectors["Communication Services"] - 0.1 * w_xlk) < 1e-9
    assert "Unclassified" in sectors  # AAA has no sector info in the test feed


def test_a9_clean_names_and_a4_nicknames():
    assert clean_name("AMAZON.COM, INC.", "AMZN") == "Amazon"
    assert clean_name("Amazon.Com, Inc.", "X") == "Amazon"
    assert clean_name("Invesco QQQ Trust 1 (XNAS:QQQ)", "QQQ") == "Invesco QQQ (Nasdaq 100)"
    assert clean_name("MICROSOFT CORPORATION", "MSFT") == "Microsoft"
    assert clean_name("TAKE-TWO INTERACTIVE SOFTWARE, INC.", "X") == "Take-Two Interactive Software"
    assert auto_aliases("Keren") == ["קרן"] and auto_aliases("Dad") == ["אבא"] and auto_aliases("Keren & Ido") == ["קרן ועידו"]
    assert auto_aliases("קרן") == ["Keren"]
    assert clean_name("", "SEDG") == "SEDG" and clean_name("SEDG", "SEDG") == "SEDG"


def test_a4_new_portfolios_get_nicknames(client):
    p = client.post("/api/portfolios", json={"name": "Keren"}).json()
    assert p["aliases"] == "קרן"


def test_today_move_like_a_broker_app_and_it_adds_up(client):
    buy(client, "AAA", 0, 10, 100)
    buy(client, "BBB", 0, 4, 100)
    h = client.get("/api/holdings?range=MAX").json()
    rows = {r["ticker"]: r for r in h["rows"]}
    step_a, step_b = 50 / 59, -20 / 59  # daily price moves of the linear test series
    assert abs(rows["AAA"]["today_native"] - 10 * step_a) < 1e-9 and abs(rows["BBB"]["today_native"] - 4 * step_b) < 1e-9
    assert abs(rows["AAA"]["today_pct"] - (150 / (150 - step_a) - 1)) < 1e-9
    total = rows["AAA"]["today"] + rows["BBB"]["today"]
    assert abs(h["today"]["change"] - total) < 1e-9
    s = client.get("/api/portfolio?range=1M").json()["summary"]
    assert abs(s["today_change"] - total) < 1e-9  # Overview and Holdings show the same "today"
    prev_value = s["value"] - total
    assert abs(s["today_pct"] - total / prev_value) < 1e-9
