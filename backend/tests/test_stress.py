"""Stress tests, the reverse stress test, the analyst memo and the scenario prediction journal."""
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import factors, impact, memo, scenario_journal, services, stresstests
from app.main import app
from app.market import provider
from app.market.synthetic import SyntheticProvider
from app.models import ScenarioPrediction
from tests.conftest import frame

EXAMPLE = json.loads((Path(__file__).resolve().parents[2] / "docs/examples/iran-escalation.scenario.json").read_text(encoding="utf-8"))


def bdays(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def test_stress_replay_matches_raw_prices(session, fake):
    days = bdays(dt.date(2020, 1, 2), 90)
    spy = {d: 100.0 + i for i, d in enumerate(days)}
    fx = {d: 3.40 + 0.002 * i for i, d in enumerate(days)}
    fake.frames["SPY"] = frame([(d.isoformat(), spy[d]) for d in days])
    fake.frames["ILS=X"] = frame([(d.isoformat(), fx[d]) for d in days])
    c = TestClient(app)
    c.put("/api/settings", json={"base_currency": "ILS"})
    c.post("/api/transactions", json={"ticker": "SPY", "type": "BUY", "date": days[5].isoformat(), "quantity": 10, "price": spy[days[5]], "currency": "USD"})
    out = c.get("/api/stress").json()
    covid = next(t for t in out["tests"] if t["id"] == "covid20")
    s, e = dt.date(2020, 2, 19), dt.date(2020, 3, 23)
    expected = (spy[e] * fx[e]) / (spy[s] * fx[s]) - 1  # in shekels, from the raw prices
    assert covid["used_start"] == s.isoformat() and covid["used_end"] == e.isoformat()
    assert covid["family"]["impact"] == pytest.approx(expected, rel=1e-9)
    assert covid["sp500_usd"] == pytest.approx(spy[e] / spy[s] - 1)
    assert covid["family"]["estimated_share"] == 0
    gfc = next(t for t in out["tests"] if t["id"] == "gfc08")
    assert gfc["data"] is False and gfc["family"]["impact"] is None  # no prices, no factor data: nothing invented


@pytest.fixture
def syn(session):
    provider.set_provider(SyntheticProvider())
    services.bump_revision()
    factors._factor_cache.clear()
    c = TestClient(app)
    c.put("/api/settings", json={"base_currency": "USD"})
    assert c.post("/api/demo?portfolio=1").status_code == 200
    c.session = session
    return c


def test_reverse_stress_forward_hits_target(syn):
    r = stresstests.reverse(syn.session)
    for res in r["results"]:
        assert res["forward"] == pytest.approx(res["loss"], rel=1e-6)
        moves = res["moves"]
        spec = {"spec_version": 1, "kind": "scenario", "id": "rev", "title": "rev", "horizon": "1m",
                "branches": [{"name": "x", "probability": 1.0, "cause": "other", "narrative": "",
                              "factor_moves": {k: {"low": v * 100, "mid": v * 100, "high": v * 100} for k, v in moves.items()}}]}
        out = impact.run(syn.session, spec, n=200, analogs=False, noise=False)
        assert out["blended"][0]["median"] == pytest.approx(res["loss"], rel=0.01)  # within 1%
        assert res["sentence"].startswith("A month that costs the family")


def test_memo_numbers_come_from_the_engine(syn):
    for lang in ("he", "en"):
        m = memo.build(syn.session, EXAMPLE, lang=lang, n=1500)
        assert [s["n"] for s in m["sections"]] == list(range(1, 10))
        allowed = set(m["numbers"]) | {str(i) for i in range(1, 10)} | {"1", "20"}
        stray = [x for x in memo.text_numbers(m) if x not in allowed]
        assert stray == [], stray
    from app import render
    pdf = render.memo_pdf(m)
    assert pdf[:4] == b"%PDF"
    png = render.memo_card(m)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_journal_scores_without_future_data_and_widens_bands(session, fake):
    days = bdays(dt.date(2026, 1, 5), 60)
    s, e = days[5], days[5] + dt.timedelta(days=7)
    px = {}
    for d in days:
        px[d] = 100.0 if d <= s else 110.0 if d <= e else 300.0  # a huge move after the horizon must not count
    fake.frames["SPY"] = frame([(d.isoformat(), px[d]) for d in days])
    fake.frames["ILS=X"] = frame([(d.isoformat(), 3.5) for d in days])
    pred = {"factors": {"market": -0.05}, "family": {"median": -0.02, "p5": -0.06, "p95": 0.02},
            "snapshot": [{"symbol": "SPY", "value": 1000.0}], "cash": 0.0, "branches": []}
    row = ScenarioPrediction(spec_id="x", title="t", start=s, end=e, horizon="1w", predicted=json.dumps(pred))
    session.add(row)
    session.commit()
    assert scenario_journal.score(session, row, today=e - dt.timedelta(days=1)) is False  # horizon not over
    assert scenario_journal.score(session, row, today=days[-1]) is True
    act = json.loads(row.actual)
    assert act["family"] == pytest.approx(0.10) and act["factors"]["market"] == pytest.approx(0.10)
    assert row.inside is False and row.abs_error == pytest.approx(0.12)
    # five scored misses -> bands widen, and the app says why
    for i in range(4):
        session.add(ScenarioPrediction(spec_id=f"y{i}", title="t", start=s, end=e, horizon="1w", predicted=json.dumps(pred)))
    session.commit()
    scenario_journal.score_due(session, today=days[-1])
    st = services.get_settings(session)
    assert st.band_scale == pytest.approx(1.25) and "0% of 5" in st.band_note
    p = scenario_journal.panel(session)
    assert p["summary"]["n"] == 5 and p["summary"]["coverage"] == 0.0


def test_risk_summary_shapes(syn):
    r = stresstests.risk_summary(syn.session)
    fam = r["books"][0]
    assert fam["var_1w"] > 0 and fam["cvar_1w"] >= fam["var_1w"]
    assert fam["var_1m"] is None or fam["cvar_1m"] >= fam["var_1m"]
    assert sum(x["risk_share"] for x in fam["risk"]) == pytest.approx(1.0)
    assert sum(x["value_share"] for x in fam["risk"]) == pytest.approx(1.0, abs=1e-6)
    assert 1 <= fam["effective_bets"] <= fam["holdings"] + 1
