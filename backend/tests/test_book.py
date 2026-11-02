"""The scenario book: probabilities, combined result, fragility, signposts, versions."""
import copy
import datetime as dt
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import book, factors, services
from app.main import app
from app.market import provider
from app.market.synthetic import SyntheticProvider
from tests.conftest import frame

EXAMPLE = json.loads((Path(__file__).resolve().parents[2] / "docs/examples/iran-escalation.scenario.json").read_text(encoding="utf-8"))


def spec(i: int, oil: float) -> dict:
    s = copy.deepcopy(EXAMPLE)
    s["id"] = f"spec-{i:04d}-0000"
    s["title"] = f"Scenario {i}"
    s["branches"] = [{"name": "Main", "probability": 1.0, "cause": "supply", "narrative": "",
                      "factor_moves": {"oil": {"low": oil - 5, "mid": oil, "high": oil + 5}, "market": {"low": -6, "mid": -3, "high": 0}},
                      "signposts": [{"indicator": "Oil", "symbol": "USO", "condition": ">", "value": 1.0, "meaning": "oil up"}]}]
    return s


@pytest.fixture
def fam(session):
    provider.set_provider(SyntheticProvider())
    services.bump_revision()
    factors._factor_cache.clear()
    c = TestClient(app)
    c.put("/api/settings", json={"base_currency": "USD"})
    assert c.post("/api/demo?portfolio=1").status_code == 200
    pid2 = c.post("/api/portfolios", json={"name": "Keren"}).json()["id"]
    for t in ("XOM", "NVDA"):
        px = c.get(f"/api/price/{t}?date=2024-01-03").json()["close"]
        c.post(f"/api/transactions?portfolio={pid2}", json={"ticker": t, "type": "BUY", "date": "2024-01-03", "quantity": 100, "price": px, "currency": "USD"})
    for i, oil in ((1, 20), (2, -15), (3, 5)):
        assert c.post("/api/specs", json={"text": json.dumps(spec(i, oil))}).status_code == 200
    c.session = session
    return c


def test_probabilities_with_remainder_sum_to_one(fam):
    entries = [{"scenario_id": "spec-0001-0000", "probability": 0.3}, {"scenario_id": "spec-0002-0000", "probability": 0.2}]
    sp, owner = book.combined_spec(fam.session, entries)
    assert sum(b["probability"] for b in sp["branches"]) == pytest.approx(1.0)
    assert sp["branches"][-1]["name"] == "Nothing special" and sp["branches"][-1]["probability"] == pytest.approx(0.5)
    r = fam.put("/api/book", json={"entries": entries}).json()
    assert r["result"]["remainder"] == pytest.approx(0.5)
    assert fam.put("/api/book", json={"entries": [{"scenario_id": "spec-0001-0000", "probability": 0.7},
                                                  {"scenario_id": "spec-0002-0000", "probability": 0.4}]}).status_code == 400
    full = book.combined_spec(fam.session, [{"scenario_id": "spec-0001-0000", "probability": 1.0}])[0]
    assert all(b["name"] != "Nothing special" for b in full["branches"])


def test_removing_a_scenario_restores_the_result(fam):
    a = [{"scenario_id": "spec-0001-0000", "probability": 0.4}]
    r1 = book.run(fam.session, a, n=2000)
    r2 = book.run(fam.session, a + [{"scenario_id": "spec-0002-0000", "probability": 0.3}], n=2000)
    r3 = book.run(fam.session, a, n=2000)
    assert r1["blended"] == r3["blended"] and r1["blended"] != r2["blended"]


def test_positioning_and_fragility(fam):
    r = book.run(fam.session, [{"scenario_id": "spec-0001-0000", "probability": 0.3}, {"scenario_id": "spec-0002-0000", "probability": 0.3}], n=3000)
    for p in r["positioning"]:
        assert p["fragility"] >= 0
        shares = sum(x["share"] for x in p["by_scenario"])
        assert shares == pytest.approx(1.0)
    keren = next(p for p in r["positioning"] if p["name"] == "Keren")
    by = {x["title"]: x["median"] for x in keren["by_scenario"]}
    assert by["Scenario 1"] > by["Scenario 2"]  # Exxon: oil up helps more than oil down
    assert r["most_fragile"]["name"] in {p["name"] for p in r["positioning"] if p["id"] is not None}


def test_signpost_status_from_cached_prices(session, fake):
    days = [dt.date(2026, 10, 1) + dt.timedelta(days=i) for i in range(20)]
    fake.frames["USO"] = frame([(d.isoformat(), 70.0 + i) for i, d in enumerate(days)])  # last close 89
    fake.frames["ILS=X"] = frame([(d.isoformat(), 3.6) for d in days])
    s = spec(9, 10)
    s["branches"][0]["signposts"] = [
        {"indicator": "Oil above 80", "symbol": "USO", "condition": ">", "value": 80, "meaning": "x"},
        {"indicator": "Oil above 92", "symbol": "USO", "condition": ">", "value": 92, "meaning": "x"},
        {"indicator": "Oil below 60", "symbol": "USO", "condition": "<", "value": 60, "meaning": "x"},
        {"indicator": "Unknown", "symbol": "NOPE", "condition": ">", "value": 1, "meaning": "x"},
        {"indicator": "Text only", "condition": ">", "value": 1, "meaning": "x"},
    ]
    c = TestClient(app)
    assert c.post("/api/specs", json={"text": json.dumps(s)}).status_code == 200
    rows = {r["indicator"]: r for r in book.signposts(session, [{"scenario_id": s["id"], "probability": 0.5}])}
    assert rows["Oil above 80"]["status"] == "triggered" and rows["Oil above 80"]["current"] == 89
    assert rows["Oil above 92"]["status"] == "near" and rows["Oil above 92"]["distance"] == pytest.approx(3 / 89)
    assert rows["Oil below 60"]["status"] == "far"
    assert rows["Unknown"]["status"] == "no data" and rows["Text only"]["status"] == "not tracked"


def test_versions_and_compare(fam):
    v1 = fam.put("/api/book", json={"entries": [{"scenario_id": "spec-0001-0000", "probability": 0.3}], "note": "first"}).json()["version"]
    v2 = fam.put("/api/book", json={"entries": [{"scenario_id": "spec-0001-0000", "probability": 0.5},
                                                {"scenario_id": "spec-0003-0000", "probability": 0.2}]}).json()["version"]
    vs = fam.get("/api/book/versions").json()
    assert [v["id"] for v in vs] == [v2, v1] and vs[1]["note"] == "first"
    cmp = fam.get(f"/api/book/compare?a={v1}&b={v2}").json()
    ch = {c["title"]: (c["from"], c["to"]) for c in cmp["changes"]}
    assert ch == {"Scenario 1": (0.3, 0.5), "Scenario 3": (None, 0.2)}
    assert cmp["a"]["result"]["answer"] != cmp["b"]["result"]["answer"]
    nm = fam.post("/api/new-money", json={"amount": 20000, "use_book": True}).json()
    w = {s["id"]: s["weight"] for s in nm["scenarios"]}
    assert w["normal"] == pytest.approx(0.3) and w["spec-0001-0000"] == pytest.approx(0.5)
