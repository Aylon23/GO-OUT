"""Scenario stress tests: the math, the text rules, and the API end to end (synthetic data)."""
import math

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import scenarios, services
from app.engine import stress
from app.main import app
from app.market import provider
from app.market.synthetic import SyntheticProvider
from app.scenario_text import parse_rules

COMPANIES = {"אפל": "AAPL", "apple": "AAPL", "nvidia": "NVDA", "אנבידיה": "NVDA"}


# ------------------------------------------------------------------ math

def test_conditional_two_assets_by_hand():
    s1, s2, rho = 0.02, 0.03, 0.5
    C = np.array([[s1 * s1, rho * s1 * s2], [rho * s1 * s2, s2 * s2]])
    out = stress.conditional(C, [0], np.array([-0.04]))
    # E[r2 | r1 = -4%] = rho * s2/s1 * -4% = -3%; sd = s2 * sqrt(1 - rho^2)
    assert out["mu"][1] == pytest.approx(-0.03, abs=1e-9)
    assert out["sd"][1] == pytest.approx(s2 * math.sqrt(1 - rho**2), rel=1e-6)
    assert out["mu"][0] == -0.04 and out["sd"][0] == 0
    # horizon scales the spread, not the expected move
    out4 = stress.conditional(C, [0], np.array([-0.04]), horizon=4)
    assert out4["mu"][1] == pytest.approx(-0.03, abs=1e-9)
    assert out4["sd"][1] == pytest.approx(2 * out["sd"][1], rel=1e-6)


def test_conditional_recovers_betas_from_data():
    rng = np.random.default_rng(1)
    n = 3000
    m = rng.normal(0, 0.02, n)
    o = rng.normal(0, 0.05, n)
    R = pd.DataFrame({"M": m, "O": o, "A": 1.5 * m + rng.normal(0, 0.01, n), "E": 0.4 * o + 0.8 * m + rng.normal(0, 0.01, n)})
    C, _ = stress.pairwise_moment(R)
    out = stress.conditional(C, [0, 1], np.array([-0.05, 0.10]))
    assert out["mu"][2] == pytest.approx(1.5 * -0.05, abs=0.004)
    assert out["mu"][3] == pytest.approx(0.4 * 0.10 + 0.8 * -0.05, abs=0.004)


def test_pairwise_moment_uses_overlap_only():
    R = pd.DataFrame({"a": [0.01, -0.02, 0.03, 0.01], "b": [np.nan, np.nan, 0.02, 0.02]})
    C, cnt = stress.pairwise_moment(R)
    assert cnt[1, 1] == 2 and cnt[0, 1] == 2
    assert C[0, 1] == pytest.approx((0.03 * 0.02 + 0.01 * 0.02) / 2)
    assert C[0, 0] == pytest.approx(np.mean(np.square([0.01, -0.02, 0.03, 0.01])))


def test_chi2_tail():
    assert stress.chi2_sf(3.841, 1) == pytest.approx(0.05, abs=5e-4)
    assert stress.chi2_sf(5.991, 2) == pytest.approx(0.05, abs=5e-4)
    assert stress.chi2_sf(0, 3) == 1.0


def test_plausibility_counts_history():
    rng = np.random.default_rng(2)
    R = pd.DataFrame({"a": rng.standard_t(3, 520) * 0.02})
    C, _ = stress.pairwise_moment(R)
    small = stress.plausibility(C, [0], np.array([0.001]), 1, R)
    huge = stress.plausibility(C, [0], np.array([5.0]), 1, R)
    assert small["label"] == "common" and small["seen"] > 400
    assert huge["seen"] == 0 and huge["label"] == "not seen in the data"
    # a 4-week shock is judged per week
    four = stress.plausibility(C, [0], np.array([0.04]), 4, R)
    one = stress.plausibility(C, [0], np.array([0.02]), 1, R)
    assert four["d2"] == pytest.approx(one["d2"])


def test_weekly_returns_friday_to_friday():
    idx = pd.bdate_range("2024-01-01", "2024-01-19")
    p = pd.Series(np.arange(len(idx), dtype=float) + 100, index=[d.date() for d in idx])
    R = stress.weekly_returns({"x": p})
    assert R["x"].iloc[0] == pytest.approx(109 / 104 - 1)


# ------------------------------------------------------------------ words

@pytest.mark.parametrize("text,expect", [
    ("מחיר הנפט ירד", [("USO", -1, None)]),
    ("oil drops 20% and nasdaq falls 5%", [("USO", -1, -0.20), ("QQQ", -1, -0.05)]),
    ("מחיר האג\"ח בארה\"ב ירד", [("TLT", -1, None)]),
    ("תשואות האגח עולות", [("TLT", -1, None)]),  # yields up = bond prices down
    ("US bond yields rise 50bp", [("TLT", -1, -0.08)]),
    ("הפד מעלה ריבית ב-0.5%", [("TLT", -1, -0.08)]),
    ("ריבית יורדת", [("TLT", 1, None)]),
    ("השקל מתחזק", [("ILS=X", -1, None)]),  # USD/ILS falls
    ("the dollar strengthens against the shekel", [("ILS=X", 1, None)]),
    ("אפל ידווחו לא טוב", [("AAPL", -1, None)]),
    ("Apple beats earnings", [("AAPL", 1, None)]),
    ("מניות השבבים צונחות 15%", [("SOXX", -1, -0.15)]),
    ("gold and oil", []),  # named without a direction: no shock
    ("oil up 10 percent in 5 years", [("USO", 1, 0.10)]),
])
def test_rules_read_shocks(text, expect):
    got = [(s["symbol"], s["direction"], None if s["move"] is None else round(s["move"], 4)) for s in parse_rules(text, COMPANIES)["shocks"]]
    assert got == expect


@pytest.mark.parametrize("text,theme", [
    ("מלחמה עם איראן", "iran_war"), ("war with Iran", "iran_war"), ("קריסה בשוק", "crash"),
    ("מיתון בארה\"ב", "recession"), ("מכסים חדשים על סין", "tariffs"), ("SVB style bank crisis", "bank_crisis"),
    ("הפד מעלה ריבית", "rate_hike"), ("הסלמה עם חיזבאללה בישראל", "israel_war"),
])
def test_rules_read_themes(text, theme):
    assert theme in parse_rules(text, COMPANIES)["themes"]


def test_iran_war_is_not_also_generic_war():
    assert parse_rules("מלחמה עם איראן", COMPANIES)["themes"] == ["iran_war"]


# ------------------------------------------------------------------ API (synthetic market)

@pytest.fixture
def api(session):
    provider.set_provider(SyntheticProvider())
    services.bump_revision()
    scenarios._universe_cache.clear()
    scenarios._check_cache.clear()
    c = TestClient(app)
    c.put("/api/settings", json={"base_currency": "USD"})
    assert c.post("/api/demo?portfolio=1").status_code == 200
    pid2 = c.post("/api/portfolios", json={"name": "Keren"}).json()["id"]
    c.post(f"/api/transactions?portfolio={pid2}", json={"ticker": "NVDA", "type": "DEPOSIT", "date": "2024-01-02", "quantity": 0, "price": 20000, "currency": "USD"})
    px = c.get("/api/price/NVDA?date=2024-01-03").json()["close"]
    r = c.post(f"/api/transactions?portfolio={pid2}", json={"ticker": "NVDA", "type": "BUY", "date": "2024-01-03", "quantity": 10, "price": px, "currency": "USD"})
    assert r.status_code == 200, r.text
    c.pid2 = pid2
    return c


def _run(c, parsed, **kw):
    body = {"shocks": [{"symbol": s["symbol"], "kind": s["kind"], "move": s["move"]} for s in parsed["shocks"]],
            "horizon_weeks": parsed["horizon_weeks"], "events": parsed["events"], "used_events": parsed.get("used_events", []), **kw}
    r = c.post("/api/scenario/run", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_iran_war_end_to_end(api):
    p = api.post("/api/scenario/parse", json={"text": "מלחמה עם איראן"}).json()
    assert p["parser"] == "rules" and p["themes"][0]["id"] == "iran_war"
    assert p["shocks"] and all(s["basis"] in ("events", "typical") for s in p["shocks"])
    assert "iran_jun25" in p["events"]
    out = _run(api, p)
    fam = out["family"]
    # the family figure is the value-weighted sum of its holdings, and of its portfolios
    assert fam["change"] == pytest.approx(sum(h["change"] for h in fam["holdings"]), rel=1e-6)
    assert fam["change"] == pytest.approx(sum(b["change"] for b in out["portfolios"]), rel=1e-6)
    assert fam["low"] <= fam["impact"] <= fam["high"]
    assert {b["id"] for b in out["portfolios"]} == {1, api.pid2}
    assert any(m["symbol"] == "USO" and m["shocked"] for m in out["market"])
    ev = next(e for e in out["events"] if e["id"] == "iran_jun25")
    assert ev["family"]["change"] == pytest.approx(sum(b["change"] for b in ev["portfolios"]), rel=1e-6)


def test_typed_size_is_kept_and_propagates(api):
    p = api.post("/api/scenario/parse", json={"text": "Nasdaq falls 10%"}).json()
    assert p["shocks"][0]["symbol"] == "QQQ" and p["shocks"][0]["move"] == pytest.approx(-0.10)
    assert p["used_events"] == []  # no theme: past events shown are only for comparison
    out = _run(api, p)
    spy = next(m for m in out["market"] if m["symbol"] == "SPY")
    assert spy["move"] < -0.03
    nvda = next(h for h in out["family"]["holdings"] if h["symbol"] == "NVDA")
    assert nvda["move"] < -0.05 and nvda["drivers"][0]["symbol"] == "QQQ"
    assert all(not e["used"] for e in out["events"]) and out["history"] is None


def test_regime_is_chosen_never_switched_by_size(api):
    big = {"shocks": [{"symbol": "QQQ", "kind": "factor", "move": -0.25}], "horizon_weeks": 1, "events": []}
    normal = _run(api, big)
    assert normal["regime"] == "normal"  # a huge move still uses normal correlations by default
    f = normal["family"]
    assert f["impact"] == pytest.approx(f["impact_normal"])
    sell = _run(api, big, regime="selloff")
    assert sell["regime"] == "selloff" and sell["family"]["impact"] == pytest.approx(f["impact_selloff"])
    assert sell["family"]["impact_normal"] == pytest.approx(f["impact_normal"])
    assert api.post("/api/scenario/run", json={**big, "regime": "panic"}).status_code == 400


def test_protection_long_only_capped_and_sized_for_worst_event(api):
    p = api.post("/api/scenario/parse", json={"text": "מלחמה עם איראן"}).json()
    out = _run(api, p)
    allowed = {"trim", "rebalance", "cash", "add"}
    for key, prot in out["protection"].items():
        value = out["family"]["value"] if key == "family" else next(b["value"] for b in out["portfolios"] if str(b["id"]) == key)
        if prot["status"] == "not_needed":
            # never "not needed" while a used past event cost this portfolio more than 2%
            impacts = [e["family"]["impact"] if key == "family" else next(x["impact"] for x in e["portfolios"] if str(x["id"]) == key)
                       for e in out["events"] if e["used"]]
            assert all(i >= -0.02 for i in impacts)
            continue
        for i in prot["ideas"]:
            assert i["kind"] in allowed  # long-only by default
            assert i["amount"] <= 0.5 * value + 1e-6 and i["amount"] <= out["family"]["value"] + 1e-6
            assert i["share_of_portfolio"] == pytest.approx(i["amount"] / value)
        if prot["basis"]["kind"] == "event":
            used = [e for e in out["events"] if e["used"]]
            worst = min(used, key=lambda e: e["family"]["change"] if key == "family" else next(x["change"] for x in e["portfolios"] if str(x["id"]) == key))
            assert prot["basis"]["id"] == worst["id"]
        assert prot["weak"] == (not any(i["covers"] >= 0.2 for i in prot["ideas"]))


def test_shorts_only_when_allowed(api):
    big = {"shocks": [{"symbol": "QQQ", "kind": "factor", "move": -0.15}], "horizon_weeks": 1, "events": []}
    off = _run(api, big)
    assert all(i["kind"] != "short" for p in off["protection"].values() for i in p["ideas"])
    api.put("/api/settings", json={"allow_short_hedges": True})
    on = _run(api, big)
    shorts = [i for i in on["protection"]["family"]["ideas"] if i["kind"] == "short"]
    assert shorts and shorts[0]["offset"] > 0 and shorts[0]["amount"] <= 0.5 * on["family"]["value"] + 1e-6


def test_used_events_and_split_directions(api):
    p = api.post("/api/scenario/parse", json={"text": "מלחמה עם איראן"}).json()
    used = p["used_events"]
    assert used and set(used) <= set(p["events"])
    for sh in p["shocks"]:
        if sh["basis"] == "events":
            n = len(sh["event_moves"])
            assert sh["basis_text"].startswith(f"Median of {n} past event")
            assert {e["id"] for e in sh["event_moves"]} <= set(used)
            signs = {m["move"] > 0.002 for m in sh["event_moves"] if abs(m["move"]) > 0.002}
            assert sh["split"] == (len(signs) == 2)
    out = _run(api, p)
    assert {e["id"] for e in out["events"] if e["used"]} == set(used)
    h = out["history"]
    assert h["n"] == len(used) and h["worst"]["impact"] <= h["best"]["impact"]
    assert int(h["from"]) <= int(h["to"])


def test_oil_drop_note_and_reverse_beyond_data(api):
    p = api.post("/api/scenario/parse", json={"text": "מחיר הנפט יורד 20%"}).json()
    assert any("supply-driven" in n for n in p["notes"])
    out = _run(api, {"shocks": [{"symbol": "SPY", "kind": "factor", "move": -0.002}], "horizon_weeks": 1, "events": []})
    r = out["reverse"]
    assert r is None or r["beyond"] or r["years"] is not None
    huge = _run(api, {"shocks": [{"symbol": "GLD", "kind": "factor", "move": -0.001}], "horizon_weeks": 1, "events": []})
    if huge["reverse"]:
        assert huge["reverse"]["beyond"] and "shocks" not in huge["reverse"]


def test_company_news_holds_market_still(api):
    p = api.post("/api/scenario/parse", json={"text": "אפל ידווחו לא טוב"}).json()
    assert [s["symbol"] for s in p["shocks"]] == ["AAPL"] and p["shocks"][0]["move"] < 0
    out = _run(api, p)
    assert out["pinned"] == "SPY"
    spy = next(m for m in out["market"] if m["symbol"] == "SPY")
    assert spy["move"] == 0 and spy["pinned"]
    aapl = next(h for h in out["family"]["holdings"] if h["symbol"] == "AAPL")
    assert aapl["shocked"] and aapl["move"] == pytest.approx(p["shocks"][0]["move"])


def test_one_portfolio_only(api):
    out = _run(api, {"shocks": [{"symbol": "SOXX", "kind": "factor", "move": -0.1}], "horizon_weeks": 1, "events": []}, portfolio_ids=[api.pid2])
    assert [b["id"] for b in out["portfolios"]] == [api.pid2]
    assert [h["symbol"] for h in out["family"]["holdings"]] == ["NVDA"]


def test_unknown_text_and_bad_moves(api):
    p = api.post("/api/scenario/parse", json={"text": "blah blah"}).json()
    assert not p["understood"] and p["shocks"] == []
    r = api.post("/api/scenario/run", json={"shocks": [{"symbol": "USO", "move": -3}]})
    assert r.status_code == 400
    assert api.post("/api/scenario/run", json={"shocks": []}).json()["empty"]


def test_model_check_and_saved_scenarios(api):
    chk = api.get("/api/scenario/check").json()
    assert chk["summary"]["events"] >= 5 and 0 <= chk["summary"]["hit_rate"] <= 1
    assert all(e["holdings"] >= 2 for e in chk["events"])
    r = api.post("/api/scenarios", json={"name": "Oil shock", "text": "oil +30%", "shocks": [{"symbol": "USO", "kind": "factor", "move": 0.3}],
                                         "events": ["ukraine22"], "used_events": ["ukraine22"]})
    sid = r.json()["id"]
    assert r.json()["used_events"] == ["ukraine22"]
    assert api.get("/api/scenarios").json()[0]["shocks"][0]["move"] == 0.3
    assert api.delete(f"/api/scenarios/{sid}").json()["ok"] and api.get("/api/scenarios").json() == []


def test_ai_key_never_returned(api):
    assert api.put("/api/settings", json={"anthropic_key": "nope"}).status_code == 400
    s = api.put("/api/settings", json={"anthropic_key": "sk-ant-api03-abcdefghijkl"}).json()
    assert s["anthropic_key_set"] and s["anthropic_key_hint"] == "…ijkl" and "anthropic_key" not in s
    assert api.get("/api/scenario/library").json()["ai"]


def test_ai_failure_falls_back_to_rules(api, monkeypatch):
    api.put("/api/settings", json={"anthropic_key": "sk-ant-api03-abcdefghijkl"})

    def boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr(scenarios, "_ai_parse", boom)
    p = api.post("/api/scenario/parse", json={"text": "oil drops 20%"}).json()
    assert p["parser"] == "rules" and p["shocks"][0]["symbol"] == "USO" and "local rules" in p["notes"][0]
