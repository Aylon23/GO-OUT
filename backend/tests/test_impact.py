"""Impact engine: Monte Carlo over branches, factor moves and residual noise."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import factors, impact, services
from app.main import app
from app.market import provider
from app.market.synthetic import SyntheticProvider
from app.scenarios import books

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
    for t in ("XOM", "NEE"):
        px = c.get(f"/api/price/{t}?date=2024-01-03").json()["close"]
        c.post(f"/api/transactions?portfolio={pid2}", json={"ticker": t, "type": "BUY", "date": "2024-01-03", "quantity": 50, "price": px, "currency": "USD"})
    c.session = session
    return c


def test_seed_reproduces_and_percentiles_are_ordered(fam):
    a = impact.run(fam.session, EXAMPLE, n=3000, seed=11, analogs=False)
    b = impact.run(fam.session, EXAMPLE, n=3000, seed=11, analogs=False)
    assert a["blended"] == b["blended"] and a["histogram"] == b["histogram"]
    c = impact.run(fam.session, EXAMPLE, n=3000, seed=12, analogs=False)
    assert c["blended"][0]["p5"] != a["blended"][0]["p5"]
    for book in a["blended"] + [x for br in a["branches"] for x in br["books"]]:
        assert book["p5"] <= book["median"] <= book["p95"]
        assert 0 <= book["prob_loss"] <= 1
    assert sum(a["histogram"]["counts"]) == 3000


def test_branch_probabilities_are_respected(fam):
    out = impact.run(fam.session, EXAMPLE, n=10000, seed=3, analogs=False)
    for br, spec_br in zip(out["branches"], EXAMPLE["branches"]):
        assert br["draws"] / 10000 == pytest.approx(spec_br["probability"], abs=0.02)


def test_zero_uncertainty_equals_beta_calculation(fam):
    spec = copy.deepcopy(EXAMPLE)
    spec["branches"] = [{"name": "Fixed", "probability": 1.0, "cause": "other", "narrative": "",
                         "factor_moves": {"market": {"low": -10, "mid": -10, "high": -10}, "oil": {"low": 20, "mid": 20, "high": 20},
                                          "shekel": {"low": -5, "mid": -5, "high": -5}}}]
    out = impact.run(fam.session, spec, n=500, analogs=False, noise=False)
    ports, family = books(fam.session, None)
    exp = factors.exposures(fam.session, [h.symbol for h in family.holdings])
    f = {"market": -0.10, "oil": 0.20, "shekel": -0.05}
    by_hand = sum(h.value / family.value * sum(exp[h.symbol]["ils"]["betas"].get(k, 0) * v for k, v in f.items()) for h in family.holdings)
    cash = 1 - sum(h.value for h in family.holdings) / family.value
    by_hand += cash * f["shekel"]  # dollar cash counted in shekels
    fam_out = out["blended"][0]
    assert fam_out["median"] == pytest.approx(by_hand, abs=1e-9)
    assert fam_out["p5"] == pytest.approx(by_hand, abs=1e-9) and fam_out["p95"] == pytest.approx(by_hand, abs=1e-9)
    usd_ils = out["usd_ils"]
    assert fam_out["median_ils"] == pytest.approx(by_hand * family.value * usd_ils, rel=1e-9)


def test_editing_a_link_moves_everything_downstream(fam):
    edited = impact.apply_edits(EXAMPLE, [{"branch": "Shipping disruption", "factor": "oil", "mid": 12.5}])
    br = next(b for b in edited["branches"] if b["name"] == "Shipping disruption")
    orig = next(b for b in EXAMPLE["branches"] if b["name"] == "Shipping disruption")
    assert br["factor_moves"]["oil"]["mid"] == 12.5
    assert br["factor_moves"]["oil"]["low"] == orig["factor_moves"]["oil"]["low"] - 12.5  # range moves with it
    for k in ("market", "rates"):  # oil -> market, oil -> rates
        assert br["factor_moves"][k]["mid"] == pytest.approx(orig["factor_moves"][k]["mid"] * 0.5)
    assert br["factor_moves"]["dollar"] == orig["factor_moves"]["dollar"]  # trigger -> dollar: not downstream of oil
    a = impact.run(fam.session, EXAMPLE, n=4000, analogs=False)
    b = impact.run(fam.session, EXAMPLE, n=4000, analogs=False, edits=[{"branch": "Shipping disruption", "factor": "oil", "mid": 12.5}])
    sa = next(x for x in a["branches"] if x["name"] == "Shipping disruption")["books"][0]["mid"]
    sb = next(x for x in b["branches"] if x["name"] == "Shipping disruption")["books"][0]["mid"]
    assert sb != pytest.approx(sa) and abs(sb) < abs(sa)


def test_local_rule_becomes_one_branch_spec(fam):
    spec = impact.spec_from_shocks(fam.session, [{"symbol": "USO", "kind": "factor", "move": -0.20}], 1.0, "oil -20%")
    br = spec["branches"][0]
    assert br["probability"] == 1 and br["factor_moves"]["oil"]["mid"] == pytest.approx(-20)
    assert br["factor_moves"]["oil"]["low"] < -20 < br["factor_moves"]["oil"]["high"]
    assert "market" in br["factor_moves"] and "XLE" in br["sector_overrides"]
    assert br["sector_overrides"]["XLE"]["mid"] < 0  # energy stocks fall with oil
    out = impact.run(fam.session, spec, n=2000, analogs=False)
    keren_id = next(b["id"] for b in out["blended"] if b["name"] == "Keren")
    xom = next(h for h in out["holdings"][str(keren_id)] if h["symbol"] == "XOM")
    assert xom["move"] < 0 and any(w["factor"] == "oil" for w in xom["why"])
    # company news holds the market still and moves the stock itself
    apple = impact.spec_from_shocks(fam.session, [{"symbol": "AAPL", "kind": "stock", "move": -0.08}])
    assert apple["branches"][0]["factor_moves"]["market"]["mid"] == pytest.approx(0, abs=1e-9)
    res = impact.run(fam.session, apple, n=1000, analogs=False, noise=False)
    aapl = next(h for h in res["holdings"]["family"] if h["symbol"] == "AAPL")
    shekel_mid = apple["branches"][0]["factor_moves"]["shekel"]["mid"] / 100
    assert aapl["own_news"] and aapl["move"] == pytest.approx(-0.08 + shekel_mid, abs=1e-9)  # its own move, plus USD/ILS in shekels


def test_api_and_analogs(fam):
    c = fam
    c.post("/api/specs", json={"text": json.dumps(EXAMPLE)})
    out = c.post(f"/api/impact/spec/{EXAMPLE['id']}", json={}).json()
    assert out["answer"]["p5_ils"] <= out["answer"]["median_ils"]
    assert {b["regime"] for b in out["branches"]} == {"selloff", "normal"}  # geopolitical/supply vs policy
    forced = c.post(f"/api/impact/spec/{EXAMPLE['id']}", json={"regime": "normal"}).json()
    assert {b["regime"] for b in forced["branches"]} == {"normal"}
    assert any(a.get("family") for a in out["analogs"])
    assert any(f["symbol"] == "^TA125.TA" for a in out["analogs"] if a.get("factors") for f in a["factors"])
    r = c.post("/api/impact/shocks", json={"shocks": [{"symbol": "USO", "kind": "factor", "move": -0.2}], "horizon_weeks": 1}).json()
    assert r["branches"][0]["regime"] == "normal" and len(r["blended"]) == 3
