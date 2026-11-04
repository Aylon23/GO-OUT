"""AI stock council: tally math, crowded and flip-flop rules, translation overrides, privacy, split runs, scoring."""
import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import ai_jobs, council, services
from app.main import app
from app.models import AiCall, CouncilRating, CouncilVerdict
from app.scenarios import books
from tests.test_openrouter import _assert_clean, _family, _private_values, ai  # noqa: F401 (fixture)


# ---------- rules ----------

@pytest.mark.parametrize("votes,strength,majority", [
    (["buy"] * 4, "unanimous", "buy"),
    (["buy", "buy", "buy", "hold"], "strong", "buy"),
    (["buy", "buy", "buy", "hold", "sell"], "split", "buy"),  # 3 of 5: a majority, but split
    (["buy", "buy", "hold", "sell"], "split", ""),  # 2 of 4: no majority
    (["buy", "buy", "sell", "sell"], "split", ""),
    (["insufficient"] * 4, "insufficient", ""),
    (["sell", "sell", "sell", "insufficient"], "unanimous", "sell"),  # insufficient isn't a vote
    (["hold", "insufficient", "insufficient"], "split", "hold"),  # one vote can't be unanimous
])
def test_tally_and_consensus(votes, strength, majority):
    t = council.tally(votes)
    assert sum(t.values()) == len(votes) and t["buy"] == votes.count("buy")
    assert council.consensus(t) == (strength, majority)


def test_agreement_quality():
    finals = [{"rating": "buy", "tags": ["momentum"]}, {"rating": "buy", "tags": ["momentum"]}, {"rating": "buy", "tags": ["momentum", "valuation"]},
              {"rating": "sell", "tags": ["valuation"]}]
    assert council.agreement_quality("buy", finals) == "single reason"  # momentum is 3 of 4 mentions
    finals[1]["tags"] = ["growth"]
    finals[2]["tags"] = ["valuation", "analyst revisions"]
    assert council.agreement_quality("buy", finals) == "diverse"


def test_crowded_flag():
    pack = {"price": 98, "from_high": -0.02, "range_52w": {"low": 60, "high": 100}, "analysts": {"consensus": "buy"}}
    assert council.crowded("unanimous", "buy", pack) and council.crowded("strong", "buy", pack)
    assert not council.crowded("split", "buy", pack)
    assert not council.crowded("unanimous", "buy", {**pack, "from_high": -0.08})
    assert not council.crowded("unanimous", "buy", {**pack, "analysts": {"consensus": "hold"}})
    low = {"price": 62, "range_52w": {"low": 60, "high": 100}, "analysts": {"consensus": "sell"}}
    assert council.crowded("strong", "sell", low) and not council.crowded("strong", "sell", {**low, "price": 70})


def test_flip_flop_rule():
    prev = {"rating": "buy", "tags": ["valuation"]}
    assert council.is_flipflop(prev, {"rating": "hold", "tags": ["valuation"]}, material=False)
    assert not council.is_flipflop(prev, {"rating": "hold", "tags": ["valuation", "macro"]}, material=False)  # new reason tag
    assert not council.is_flipflop(prev, {"rating": "hold", "tags": ["valuation"]}, material=True)  # price ±10%, earnings, revision
    assert not council.is_flipflop(prev, {"rating": "buy", "tags": []}, material=False)  # no change
    assert not council.is_flipflop(None, {"rating": "sell", "tags": []}, material=False)


def test_translation_overrides_council_ratings(session, ai):
    _family(session)
    ports, _fam = books(session, None)
    p1 = next(b for b in ports if b.pid == 1)
    w = {h.symbol: h.value / p1.value for h in p1.holdings}
    big = max(w, key=w.get)
    assert w[big] >= council.DEFAULT_CAP
    pack = {"factor_exposures": {"betas": {"market": 1.5}}}
    a = next(x for x in council.translate(session, big, "unanimous", "buy", pack, "4/4") if x["portfolio_id"] == 1)
    assert a["action"] == "Hold (at limit)" and "Fragile in a sell-off" in a["why"]
    gain = services.position_gain(services.Context(session, 1), big)["gain_base"]
    s = next(x for x in council.translate(session, big, "strong", "sell", {}, "3/4") if x["portfolio_id"] == 1)
    if gain >= council.BIG_GAIN * a["value"]:
        assert s["action"] == f"Review sell: est. tax ${gain * council.DEFAULT_TAX:,.0f}" and "estimate" in s["why"]
    else:
        assert s["action"] == "Consider selling"
    assert council.translate(session, big, "split", "buy", pack, "2/4") == []  # a split council suggests nothing
    small = min(w, key=w.get)
    if w[small] < council.DEFAULT_CAP:
        assert next(x for x in council.translate(session, small, "unanimous", "buy", {}, "4/4") if x["portfolio_id"] == 1)["action"] == "Consider adding"


# ---------- runs ----------

class Council:
    """Plays the analysts: analyst i rates every stock `plan[i]`; round 2 keeps it; the judge summarizes."""

    def __init__(self, plan, tags=None, r2_change_without_reason=False):
        self.plan, self.tags = plan, tags or {}
        self.n1 = self.n2 = 0
        self.r2_flip = r2_change_without_reason

    @staticmethod
    def tickers(body):
        msg = next(m["content"] for m in body["messages"] if m["content"].startswith("DATA PACKS:"))
        return [p["ticker"] for p in json.loads(msg[len("DATA PACKS:\n"):])]

    def __call__(self, body):
        sysmsg = body["messages"][0]["content"]
        ts = self.tickers(body)
        if "(council_ratings)" in sysmsg:
            r = self.plan[self.n1 % len(self.plan)]
            self.n1 += 1
            return json.dumps({"ratings": [{"ticker": t, "rating": r, "conviction": 3, "thesis": f"{t} thesis", "main_risk": "risk",
                                            "change_my_mind": "x", "tags": self.tags.get(r, ["valuation"])} for t in ts]})
        if "(council_revisions)" in sysmsg:
            r = self.plan[self.n2 % len(self.plan)]
            self.n2 += 1
            if self.r2_flip:
                return json.dumps({"ratings": [{"ticker": t, "rating": "sell" if r != "sell" else "buy", "new_reason": ""} for t in ts]})
            return json.dumps({"ratings": [{"ticker": t, "rating": r, "conviction": 4, "changed": False, "tags": self.tags.get(r, ["valuation"]),
                                            "devils_argument": "The other side."} for t in ts]})
        return json.dumps({"stocks": [{"ticker": t, "merged_thesis": f"{t}: merged", "strongest_dissent": "dissent", "contradictions": []} for t in ts]})


def wire(ai, answers, check=None):
    if not hasattr(ai, "_orig_request"):
        ai._orig_request = ai.request
    orig = ai._orig_request
    ai.answers.clear()

    def request(method, url, headers, body, timeout):
        if body is not None and url.endswith("/chat/completions"):
            if check:
                check(body)
            ai.answers.append(answers(body))
        return orig(method, url, headers, body, timeout)

    ai.request = request


def test_council_run_end_to_end(session, ai):
    c = _family(session)
    c.post("/api/watchlist/NVDA")
    names, amounts = _private_values(session)
    wire(ai, Council(["buy", "buy", "buy", "hold"]), check=lambda b: _assert_clean(b, names, amounts))
    syms = council.universe(session)
    assert syms[-1] == "NVDA" and set(syms) == {"AAPL", "MSFT", "XOM", "NVDA"}
    run = council.start(session, background=False)
    ai_jobs.drain(session)
    st = council.run_status(session, run)
    assert st["status"] == "done" and st["rated"] == 4 and st["requests"] == 9  # one batch: 4 + 4 + 1
    vs = {v.ticker: v for v in session.exec(select(CouncilVerdict)).all()}
    assert set(vs) == set(syms)
    v = vs["AAPL"]
    assert json.loads(v.tally) == {"buy": 3, "hold": 1, "sell": 0, "insufficient": 0}
    assert v.consensus == "strong" and v.majority == "buy" and v.thesis == "AAPL: merged" and v.devils_argument
    assert v.anchor_price and v.anchor_spy  # anchored at rating time
    rows = c.get("/api/council").json()["rows"]
    assert {r["ticker"] for r in rows} == set(syms) and all(r["tally"]["buy"] == 3 for r in rows)
    detail = c.get("/api/council/stock/AAPL").json()
    assert len(detail["models"]) == 4 and detail["label"].startswith("AI opinion") and detail["pack"]["ticker"] == "AAPL"
    queue = c.get("/api/actions?source=council").json()
    assert queue and all(q["suggested"] for q in queue)  # suggestions only
    # a second run where one analyst changes Buy -> Hold with the same reason tag and nothing material: flip-flop
    wire(ai, Council(["hold", "buy", "buy", "hold"]), check=lambda b: _assert_clean(b, names, amounts))
    run2 = council.start(session, ["AAPL"], kind="stock", background=False)
    ai_jobs.drain(session)
    v2 = session.exec(select(CouncilVerdict).where(CouncilVerdict.run_id == run2.id)).one()
    plan1, plan2 = ["buy", "buy", "buy", "hold"], ["hold", "buy", "buy", "hold"]
    l1, l2 = json.loads(run.labels), json.loads(run2.labels)
    prev = {l1[lbl]["models"][0]: plan1[i] for i, lbl in enumerate(sorted(l1))}
    from app import stock_pack
    material = council.material_change(session, "AAPL", dt.date.today(), v.anchor_price, stock_pack.build(session, "AAPL"), dt.date.today())
    expected = [lbl for i, lbl in enumerate(sorted(l2)) if not material and prev[l2[lbl]["models"][0]] != plan2[i]]
    assert sorted(json.loads(v2.flipflops)) == sorted(expected)
    assert expected  # the fixture does change a model's rating
    assert v2.change == ""  # flip-flops aren't counted as a change


def test_change_without_a_new_reason_keeps_the_round1_rating(session, ai):
    _family(session)
    wire(ai, Council(["buy", "buy", "hold", "buy"], r2_change_without_reason=True))
    run = council.start(session, ["AAPL"], kind="stock", background=False)
    ai_jobs.drain(session)
    r2 = session.exec(select(CouncilRating).where(CouncilRating.run_id == run.id, CouncilRating.round == 2)).all()
    assert r2 and all(r.rating in ("buy", "hold") for r in r2)  # the unexplained flips to sell were ignored


def test_split_run_resumes_without_duplicate_requests(session, ai):
    _family(session)
    wire(ai, Council(["buy", "hold", "buy", "buy"]))
    ai.used = ai.limit - 12  # 12 requests left today: batch 1 (9) fits, batch 2 doesn't
    run = council.start(session, ["AAPL", "MSFT", "XOM", "NVDA", "SPY", "QQQ", "TSLA"], kind="full", background=False)
    ai_jobs.drain(session)
    st = council.run_status(session, run)
    assert st["status"] == "running" and st["rated"] == 5 and st["waiting"]  # "5 of 7 rated", waits for tomorrow
    sent = len(ai.posts)
    assert sent == 12
    ai.used = 0  # next UTC day
    from app import openrouter
    openrouter.refresh_quota(session, force=True)
    from app.models import AiJob
    for j in session.exec(select(AiJob).where(AiJob.status == "waiting_quota")).all():
        ai_jobs.resume(session, j.id)
    ai_jobs.drain(session)
    st = council.run_status(session, run)
    assert st["status"] == "done" and st["rated"] == 7 and len(ai.posts) == 18  # 2 batches × 9, nothing sent twice
    per_step = {}
    for call in session.exec(select(AiCall).where(AiCall.status == "ok")).all():
        per_step[(call.job_id, call.step_key)] = per_step.get((call.job_id, call.step_key), 0) + 1
    assert set(per_step.values()) == {1}


# ---------- scoring ----------

def test_scoring_is_forward_only_and_matches_raw_prices(session, ai):
    _family(session)
    from app.market.cache import load_history

    today = dt.date.today()
    old, new = today - dt.timedelta(days=400), today - dt.timedelta(days=20)
    for d in (old, new):
        session.add(CouncilRating(run_id=1, ticker="AAPL", round=1, label="A", model="m:free", rating="buy", tags="[]", rated_at=d))
    session.commit()
    board = council.leaderboard(session)
    rows = {(r["who"], r["horizon"]): r for r in board["rows"]}
    assert ("m:free", "12m") in rows and rows[("m:free", "12m")]["n"] == 1  # the recent rating isn't scored yet
    a, s = load_history(session, "AAPL")["adj_close"], load_history(session, "SPY")["adj_close"]
    end = old + dt.timedelta(days=365)
    ex = (a[a.index <= end].iloc[-1] / a[a.index <= old].iloc[-1]) - (s[s.index <= end].iloc[-1] / s[s.index <= old].iloc[-1])
    assert rows[("m:free", "12m")]["avg_excess"] == pytest.approx(ex)
    assert rows[("m:free", "12m")]["hit_rate"] == (1.0 if ex > 0 else 0.0)
    assert council._excess(session, "AAPL", new, 91, today) is None  # forward only


def test_settings_and_universe_filter(session, ai):
    c = _family(session)
    out = c.put("/api/council/settings", json={"min_weight": True, "weekday": 2}).json()
    assert out["settings"] == {"min_weight": True, "weekday": 2}
    assert c.put("/api/council/settings", json={"weekday": 9}).status_code == 400


def test_funds_skip_the_stock_cap_and_sp500_trackers_skip_the_council(session, ai):
    c = _family(session)
    day = (dt.date.today() - dt.timedelta(days=400)).isoformat()
    px = c.get(f"/api/price/QQQ?date={day}").json()["close"]
    c.post("/api/transactions?portfolio=1", json={"ticker": "QQQ", "type": "BUY", "date": day, "quantity": 400, "price": round(px, 2), "currency": "USD"})
    c.post("/api/transactions?portfolio=1", json={"ticker": "SPY", "type": "BUY", "date": day, "quantity": 5, "price": 400, "currency": "USD"})
    services.bump_revision()
    assert "SPY" not in council.universe(session) and "QQQ" in council.universe(session)
    ports, _ = books(session, None)
    p1 = next(b for b in ports if b.pid == 1)
    q = next(h for h in p1.holdings if h.symbol == "QQQ")
    assert q.value / p1.value > council.DEFAULT_CAP
    assert council.translate(session, "QQQ", "unanimous", "hold", {}, "4/4") == []  # no "above limit" for a fund
    buy = next(x for x in council.translate(session, "QQQ", "unanimous", "buy", {}, "4/4") if x["portfolio_id"] == 1)
    assert buy["action"] == "Consider adding"
