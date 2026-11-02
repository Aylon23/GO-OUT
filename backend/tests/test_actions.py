"""Per-person actions, the message draft, new-money ranking and the watchlist."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import actions, factors, services
from app.main import app
from app.market import provider
from app.market.synthetic import SyntheticProvider

EXAMPLE = json.loads((Path(__file__).resolve().parents[2] / "docs/examples/iran-escalation.scenario.json").read_text(encoding="utf-8"))


@pytest.fixture
def fam(session):
    provider.set_provider(SyntheticProvider())
    services.bump_revision()
    factors._factor_cache.clear()
    c = TestClient(app)
    c.put("/api/settings", json={"base_currency": "USD"})
    assert c.post("/api/demo?portfolio=1").status_code == 200
    pid2 = c.post("/api/portfolios", json={"name": "Keren"}).json()["id"]
    for t, q in (("NVDA", 400), ("XOM", 50), ("NEE", 50)):
        px = c.get(f"/api/price/{t}?date=2024-01-03").json()["close"]
        c.post(f"/api/transactions?portfolio={pid2}", json={"ticker": t, "type": "BUY", "date": "2024-01-03", "quantity": q, "price": px, "currency": "USD"})
    c.session, c.pid2 = session, pid2
    return c


def test_options_long_only_and_capped(fam):
    out = actions.actions(fam.session, EXAMPLE, n=2000)
    assert [p["name"] for p in out["people"]] and set(out["rank_expected"]) == {p["id"] for p in out["people"]}
    fam_value = sum(p["value_amt"] for p in out["people"])
    for p in out["people"]:
        kinds = [o["kind"] for o in p["options"]]
        assert kinds[-1] == "nothing" and len(kinds) <= 4
        assert set(kinds) <= {"trim", "rebalance", "add", "cash", "nothing"}
        for o in p["options"]:
            assert o["amount"] <= 0.5 * p["value_amt"] + 1e-6 and o["amount"] <= fam_value + 1e-6
            assert o["share_of_portfolio"] == pytest.approx(o["amount"] / p["value_amt"])
            assert o["cost_amt"] >= 0
            if o["kind"] in ("trim", "rebalance", "cash"):  # selling into cash never raises the largest weight
                assert o["top_weight_after"] <= o["top_weight_before"] + 1e-9
            if o["kind"] != "nothing":
                assert o["benefit_amt"] > 0
    # ranking by bad case is by the 5th percentile
    bad = {p["id"]: p["bad_amt"] for p in out["people"]}
    assert out["rank_bad"] == sorted(bad, key=bad.get)


def test_message_has_only_numbers_from_the_result(fam):
    out = actions.actions(fam.session, EXAMPLE, n=2000)
    for p in out["people"]:
        msg = p["message"]
        allowed = {"1", "20"}  # "1 מתוך 20"
        allowed |= {str(i) for i in range(1, 6)}  # option numbers
        for v in (p["median_amt"], p["bad_amt"]):
            allowed.add(actions.amt_he(v, p["currency"]).split(" ")[0])
        for v in (p["median"],):
            allowed.add(actions.pct_he(v).rstrip("%"))
        for o in p["options"]:
            allowed |= {actions.amt_he(o["amount"], p["currency"]).split(" ")[0], actions.amt_he(o["benefit_amt"], p["currency"]).split(" ")[0],
                        actions.amt_he(o["cost_amt"], p["currency"]).split(" ")[0], actions.pct_he(o["share_of_portfolio"]).rstrip("%")}
            if o["kind"] == "rebalance":
                allowed.add(f"{o['target'] * 100:.0f}")
        for sym in [h["symbol"] for h in p["drivers_holdings"]] + [o.get("symbol", "") for o in p["options"]]:
            allowed |= set(actions.message_numbers(sym or ""))
        stray = [x for x in actions.message_numbers(msg) if x not in allowed]
        assert stray == [], (stray, msg)
        assert "היי" in msg and "לא ייעוץ" in msg


def test_new_money_ranking_is_stable_and_robust_first(fam):
    c = fam
    assert c.post("/api/watchlist/AVGO").json()["ok"]
    assert c.get("/api/watchlist").json() == ["AVGO"]
    only_normal = actions.new_money(fam.session, 20000, "ILS", n=1000)
    assert only_normal["note"] and [s["id"] for s in only_normal["scenarios"]] == ["normal"]
    assert "AVGO" in [r["symbol"] for r in only_normal["rows"]] and "SPY" in [r["symbol"] for r in only_normal["rows"]]
    c.post("/api/specs", json={"text": json.dumps(EXAMPLE)})
    a = actions.new_money(fam.session, 20000, "ILS", n=1500, seed=5)
    b = actions.new_money(fam.session, 20000, "ILS", n=1500, seed=5)
    assert [r["symbol"] for r in a["rows"]] == [r["symbol"] for r in b["rows"]] and a["rows"][0]["score"] == b["rows"][0]["score"]
    assert a["note"] is None and len(a["scenarios"]) == 2
    flags = [r["robust"] for r in a["rows"]]
    assert flags == sorted(flags, reverse=True)  # robust candidates first
    top = a["rows"][0]
    # 20,000 ₪ is shown in the display currency (USD) at today's rate; the percentage is unchanged
    assert a["amount_amt"] == pytest.approx(20000 / services.fx_series(fam.session).iloc[-1])
    assert top["expected_amt"] == pytest.approx(a["amount_amt"] * top["expected"])
    assert set(top["by_scenario"]) == {"normal", EXAMPLE["id"]}
    person = actions.new_money(fam.session, 20000, "ILS", person=fam.pid2, n=500)
    assert all("weight_after" in r for r in person["rows"])
    c.delete("/api/watchlist/AVGO")
    assert c.get("/api/watchlist").json() == []


def test_actions_api_for_typed_scenario(fam):
    r = fam.post("/api/actions", json={"shocks": [{"symbol": "USO", "kind": "factor", "move": 0.25}], "horizon_weeks": 1, "text": "oil +25%"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["people"] and all(p["message"] for p in d["people"])
