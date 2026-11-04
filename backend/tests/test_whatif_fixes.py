"""What-if review fixes: one simulation shared by every view, "by cause" regimes, a book at exactly 100%."""
import pytest
from sqlmodel import select

from app import book, impact
from app.models import ResearchedSpec, Scenario
from tests.test_openrouter import _family, ai  # noqa: F401 (fixture)

WAR = {"shocks": [{"symbol": "USO", "kind": "factor", "move": 0.03}, {"symbol": "SPY", "kind": "factor", "move": -0.02},
                  {"symbol": "GLD", "kind": "factor", "move": 0.01}],
       "horizon_weeks": 2, "events": ["iran_jun25", "iran_oct24"], "used_events": ["iran_jun25", "iran_oct24"],
       "text": "war with Iran", "regime": None, "edits": []}


def test_headline_memo_and_people_share_one_simulation(session, ai):
    c = _family(session)
    page = c.post("/api/impact/shocks", json=WAR).json()
    people = c.post("/api/actions", json=WAR).json()
    memo = c.post("/api/memo", json={**WAR, "lang": "en"}).json()
    fam = page["blended"][0]
    headline = " ".join(memo["sections"][0]["lines"])
    assert f"${abs(fam['median_amt']):,.0f}" in headline and f"${abs(fam['p5_amt']):,.0f}" in headline
    by_name = {b["name"]: b for b in page["blended"][1:]}
    assert people["people"] and memo["people"]
    for p in people["people"]:
        assert p["median_amt"] == pytest.approx(by_name[p["name"]]["median_amt"], rel=1e-12)
    for p in memo["people"]:
        assert p["median_amt"] == pytest.approx(by_name[p["name"]]["median_amt"], rel=1e-12)


def test_war_by_cause_uses_selloff_correlations(session, ai):
    c = _family(session)
    by_cause = c.post("/api/impact/shocks", json=WAR).json()
    normal = c.post("/api/impact/shocks", json={**WAR, "regime": "normal"}).json()
    selloff = c.post("/api/impact/shocks", json={**WAR, "regime": "selloff"}).json()
    assert by_cause["branches"][0]["cause"] == "geopolitical" and by_cause["branches"][0]["regime"] == "selloff"
    assert by_cause["blended"][0]["median_amt"] != pytest.approx(normal["blended"][0]["median_amt"], rel=1e-9)
    assert by_cause["blended"][0]["median_amt"] == pytest.approx(selloff["blended"][0]["median_amt"], rel=1e-12)
    assert by_cause["spec"]["branches"][0]["name"] == "war with Iran"  # the memo names the branch, not "Scenario"


def test_local_cause():
    assert impact.local_cause("", ["iran_jun25", "oct7_23"]) == "geopolitical"
    assert impact.local_cause("", ["covid20"]) == "demand"
    assert impact.local_cause("", ["cpi_jun22"]) == "policy"
    assert impact.local_cause("the Fed cuts rates") == "policy"
    assert impact.local_cause("something odd") == "other"


def test_book_saves_at_exactly_100_percent(session, ai):
    _family(session)
    for i, name in enumerate(["a", "b", "c", "d"]):
        session.add(Scenario(name=name, text=name, shocks='[{"symbol": "SPY", "kind": "factor", "move": -0.05}]'))
    session.commit()
    ids = [f"saved-{s.id}" for s in session.exec(select(Scenario)).all()]
    probs = [0.05, 0.55, 0.30, 0.10]  # 5% + 55% + 30% + 10%
    total = 0.0
    for p in probs:  # left to right, like the browser (Python's sum() compensates and hides the error)
        total += p
    assert total != 1.0 and abs(total - 1) < 1e-9  # 1.0000000000000002
    v = book.save(session, [{"scenario_id": s, "probability": p} for s, p in zip(ids, probs)])
    assert v.id
    with pytest.raises(book.BookError):
        book.save(session, [{"scenario_id": s, "probability": p} for s, p in zip(ids, [0.06, 0.55, 0.30, 0.10])])
