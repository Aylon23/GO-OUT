"""Scenario debate: input reading, rounds, judge always valid, disagreement widens, impact ranking, scoring."""
import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import ai_jobs, debate, debate_input, factors, impact, specs
from app.main import app
from app.models import AiCall, Debate, DebateSpec
from app.scenarios import books
from tests.test_openrouter import FREE, _assert_clean, _family, _private_values, ai  # noqa: F401 (fixture)


def branch(name, p, moves, cause="geopolitical", analog=True):
    b = {"name": name, "probability": p, "cause": cause, "narrative": f"{name} narrative",
         "factor_moves": {f: {"low": m - 2, "mid": m, "high": m + 2} for f, m in moves.items()},
         "transmission": [{"from": "trigger", "to": "oil", "direction": "+", "explanation": "supply risk"}]}
    if analog:
        b["analogs"] = [{"name": "Israel–Iran war", "start": "2025-06-12", "end": "2025-06-24"}]
    return b


def spec_like(oil, market, title="View"):
    return {"title": title, "summary": f"oil {oil}, market {market}", "confidence": "medium",
            "branches": [branch("Contained", 0.7, {"oil": oil, "market": market}), branch("Escalation", 0.3, {"oil": oil * 2, "market": market * 2})]}


class Answers:
    """Plays the analysts and the judge by reading which answer the request asks for."""

    def __init__(self, r1, judge=None):
        self.r1 = list(r1)  # one spec per analyst, in request order
        self.judge = judge
        self.i1 = self.i2 = 0

    def __call__(self, body):
        sysmsg = body["messages"][0]["content"]
        if "(scenario_spec)" in sysmsg:
            s = self.r1[self.i1 % len(self.r1)]
            self.i1 += 1
            return json.dumps(s)
        if "(critique_and_revised_spec)" in sysmsg:
            s = self.r1[self.i2 % len(self.r1)]
            self.i2 += 1
            return json.dumps({"critique": "Ranges too tight.", "devils_case": "Markets shrug it off.", "spec": s})
        return json.dumps(self.judge or {"spec": spec_like(5, -2, "Merged"), "agreements": ["Oil up first"],
                                         "disputes": [{"topic": "Size of the oil move", "views": "A: +3%, B: +8%", "why": "Different analogs"}]})


def wire(ai, answers):
    """Every chat request gets its answer from `answers`; every request is also privacy-checked."""
    if not hasattr(ai, "_orig_request"):
        ai._orig_request = ai.request
    orig = ai._orig_request
    ai.answers.clear()
    names_amounts = {}

    def request(method, url, headers, body, timeout):
        if body is not None and url.endswith("/chat/completions"):
            if names_amounts:
                _assert_clean(body, *names_amounts["v"])
            ai.answers.append(answers(body))
        return orig(method, url, headers, body, timeout)

    ai.request = request
    return names_amounts


# ---------- input ----------

def test_input_reads_horizon_cause_and_converts_dollars(session, ai):
    _family(session)
    r = debate_input.read(session, "oil falls $10 per barrel")
    oil = r["dollar_moves"][0]
    from app.market.cache import load_history
    price = float(load_history(session, "CL=F")["close"].iloc[-1])
    assert oil["symbol"] == "CL=F" and oil["pct"] == pytest.approx(round(-1000 / price, 2))
    assert set(r["needs"]) == {"horizon", "cause"}  # asked with chips before anything is sent
    r = debate_input.read(session, "תהיה מלחמה עם איראן")
    assert r["cause"] == "geopolitical" and "horizon" in r["needs"]
    r = debate_input.read(session, "war with Iran over the next 3 months")
    assert r["horizon"] == "3m" and r["needs"] == {}
    c = TestClient(app)
    out = c.post("/api/debates?wait=true", json={"text": "oil falls $10 per barrel"}).json()
    assert out["needs"] and "id" not in out and ai.posts == []


# ---------- end to end ----------

def test_debate_end_to_end(session, ai):
    c = _family(session)
    pv = wire(ai, Answers([spec_like(4, -1), spec_like(8, -3), spec_like(-2, 1), spec_like(6, -2)]))
    pv["v"] = _private_values(session)
    r = c.post("/api/debates?wait=true", json={"text": "War with Iran", "horizon": "1m", "cause": "geopolitical"}).json()
    did = r["id"]
    ai_jobs.drain(session)
    out = c.get(f"/api/debates/{did}").json()
    assert out["phase"] == "done", out["note"]
    assert out["requests"] == 9  # 4 analysts, 4 revisions, 1 judge
    res = out["result"]
    assert res["answer"].startswith("Typical: the family")
    assert res["hurt"] and all(h["change_amt"] < 0 for h in res["hurt"]) and len(res["hurt"]) <= 5
    assert res["most_exposed"]["name"] in ("My portfolio", "Keren")
    assert {h["stage"] for h in res["headline"]} == {"round 1", "round 2"} and len(res["headline"]) == 8
    assert any(x.get("added_by") == "app" for x in res["disputes"])  # the -2% oil model is stated, not averaged away
    assert res["widened"]  # models disagreed on oil: ranges widened
    assert specs.validate(session, c.get(f"/api/specs/{out['spec_id']}").json()["spec"])["errors"] == []
    assert len(session.exec(select(DebateSpec).where(DebateSpec.debate_id == did)).all()) == 5  # 4 round-1 + judge
    rounds = {t["round"] for t in out["transcript"]}
    assert rounds == {"round1", "round2", "judge"} and all(t["models"] for t in out["transcript"])
    # buttons
    assert c.post(f"/api/debates/{did}/queue").json()["added"] > 0
    assert c.post(f"/api/debates/{did}/book").json()["probability"] > 0
    # the devil's advocate rotates
    r2 = c.post("/api/debates?wait=true", json={"text": "Oil falls", "horizon": "1m", "cause": "supply"}).json()
    assert session.get(Debate, r2["id"]).devil != session.get(Debate, did).devil


def test_judge_output_always_passes_the_schema(session, ai):
    c = _family(session)
    garbage = [
        {"spec": {"branches": [{"name": "x", "probability": 3, "factor_moves": {"oil": {"low": 9, "mid": 1, "high": -4}, "sp500": 5}}]},
         "agreements": [], "disputes": []},
        {"spec": {"branches": [{"name": "a", "probability": 0, "factor_moves": {"market": -400}}, {"name": "a", "probability": 0,
                                "factor_moves": {"gold": "3"}, "analogs": [{"name": "bad", "start": "2025-13-40", "end": "x"}]}]},
         "agreements": [], "disputes": []},
        {"spec": {"title": "No branches"}, "agreements": [], "disputes": []},
    ]
    for g in garbage:
        wire(ai, Answers([spec_like(4, -1), spec_like(6, -2)], judge=g))
        r = c.post("/api/debates?wait=true", json={"text": "Oil shock", "horizon": "1m", "cause": "supply"}).json()
        ai_jobs.drain(session)
        out = c.get(f"/api/debates/{r['id']}").json()
        assert out["phase"] == "done", out["note"]
        spec = c.get(f"/api/specs/{out['spec_id']}").json()["spec"]
        assert specs.validate(session, spec)["errors"] == []
        assert abs(sum(b["probability"] for b in spec["branches"]) - 1) < 1e-6


# ---------- merge rules ----------

def test_disagreement_widens_ranges_more_than_agreement():
    judge = debate.sanitize(spec_like(5, -2), "q", "1m", "geopolitical")
    agree = {k: debate.sanitize(spec_like(5, -2), "q", "1m", "geopolitical") for k in "ABCD"}
    disagree = {"A": debate.sanitize(spec_like(-10, 3), "q", "1m", "geopolitical"), "B": debate.sanitize(spec_like(20, -6), "q", "1m", "geopolitical"),
                "C": debate.sanitize(spec_like(5, -2), "q", "1m", "geopolitical"), "D": debate.sanitize(spec_like(8, -3), "q", "1m", "geopolitical")}
    a, wa = debate.enforce_spread(judge, agree)
    d, wd = debate.enforce_spread(judge, disagree)

    def width(s, f):
        return sum(b["factor_moves"][f]["high"] - b["factor_moves"][f]["low"] for b in s["branches"])

    assert wa == [] and width(a, "oil") == width(judge, "oil")  # agreement: the judge's tight ranges stay
    assert width(d, "oil") > width(a, "oil") and width(d, "market") > width(a, "market")
    assert all(b["factor_moves"]["oil"]["low"] <= b["factor_moves"]["oil"]["mid"] <= b["factor_moves"]["oil"]["high"] for b in d["branches"])
    # the -10% oil view is stated even though the judge's disputes didn't mention it
    added = debate.minority_disputes(d, disagree, [])
    assert any("oil" in x["topic"].lower() and "Model A" in x["views"] for x in added)
    assert debate.minority_disputes(d, disagree, [{"topic": "oil direction", "views": "A says down"}]) == [
        x for x in added if "oil" not in x["topic"].lower()]


def test_fallback_keeps_every_view_as_a_branch():
    views = {"A": debate.sanitize(spec_like(-10, 3), "q", "1m", "supply"), "B": debate.sanitize(spec_like(20, -6), "q", "1m", "supply")}
    fb = debate.fallback_merge(views, "q", "1m", "supply")
    assert [b["name"] for b in fb["branches"]] == ["Model A view", "Model B view"]
    assert fb["branches"][0]["factor_moves"]["oil"]["mid"] == pytest.approx(debate.expected_moves(views["A"])["oil"])


# ---------- engine ----------

def test_impact_ranking_matches_a_hand_computed_fixture(session, ai):
    _family(session)
    spec = debate.sanitize({"title": "Fixed", "summary": "s", "confidence": "low", "branches": [
        {"name": "Only", "probability": 1, "cause": "supply", "narrative": "",
         "factor_moves": {"market": {"low": -6, "mid": -6, "high": -6}, "oil": {"low": 25, "mid": 25, "high": 25},
                          "rates": {"low": -3, "mid": -3, "high": -3}}}]}, "q", "1m", "supply")
    out = impact.run(session, spec, n=400, analogs=False, noise=False)
    hurt, helped = debate.top_movers(out)
    _ports, fam = books(session, None)
    exp = factors.exposures(session, [h.symbol for h in fam.holdings])
    moves = {"market": -0.06, "oil": 0.25, "rates": -0.03}
    by_hand = []
    for h in fam.holdings:
        m = sum(exp[h.symbol]["local"]["betas"].get(f, 0) * v for f, v in moves.items())
        by_hand.append((h.symbol, m, m * h.value))
    exp_hurt = [s for s, _m, a in sorted(by_hand, key=lambda x: x[2]) if a < 0][:5]
    exp_help = [s for s, _m, a in sorted(by_hand, key=lambda x: -x[2]) if a > 0][:5]
    assert [h["symbol"] for h in hurt] == exp_hurt and [h["symbol"] for h in helped] == exp_help
    for h in hurt + helped:
        s, m, a = next(x for x in by_hand if x[0] == h["symbol"])
        assert h["move"] == pytest.approx(m, abs=1e-9) and h["change_amt"] == pytest.approx(a, rel=1e-9)
        assert h["driver"] in moves  # the factor that drives it


# ---------- scoring ----------

def test_scoring_is_forward_only(session, ai):
    _family(session)
    s = debate.sanitize(spec_like(5, -2), "q", "1m", "supply")
    today = dt.date.today()
    old = DebateSpec(debate_id=1, kind="round1", label="A", model=FREE[0], spec=json.dumps(s), horizon="1m", start=today - dt.timedelta(days=60))
    new = DebateSpec(debate_id=1, kind="round1", label="B", model=FREE[1], spec=json.dumps(s), horizon="1m", start=today - dt.timedelta(days=10))
    session.add(old)
    session.add(new)
    session.commit()
    assert debate.score_due(session) == 1
    assert old.scored_at == today and new.scored_at is None
    from app.specs import factor_move
    end = old.start + dt.timedelta(days=round(21 * 7 / 5))
    actual_oil = factor_move(session, "oil", old.start, end)
    assert old.direction_n == 2 and old.direction_hits == (actual_oil > 0) + (factor_move(session, "market", old.start, end) < 0)
    board = debate.leaderboard(session)
    assert board[0]["model"] == FREE[0] and board[0]["scenarios"] == 1
