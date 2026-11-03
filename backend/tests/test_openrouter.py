"""OpenRouter foundation: free-only, privacy on every payload, quota counter, pacing, retries, repair, resume."""
import datetime as dt
import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import ai_jobs, context_pack, openrouter, privacy, services
from app.main import app
from app.market import provider
from app.market.synthetic import SyntheticProvider
from app.models import AiCall, AiJob, AiJobStep

SCHEMA = {"type": "object", "required": ["view", "confidence"], "additionalProperties": False,
          "properties": {"view": {"type": "string"}, "confidence": {"type": "number", "minimum": 0, "maximum": 1}}}
OK = json.dumps({"view": "flat", "confidence": 0.5})
FREE = ["google/gemma-4-31b-it:free", "nvidia/nemotron-3.5-super-vl:free"]


class Crash(BaseException):
    """Stands in for the app stopping mid-request (not caught like an ordinary error)."""


class FakeOpenRouter:
    """Counts every chat request against the daily free quota, like OpenRouter."""

    def __init__(self, clock, limit=50, used=0):
        self.clock, self.limit, self.used = clock, limit, used
        self.posts: list[dict] = []
        self.times: list[float] = []
        self.answers: list = []
        self.key_reads = 0
        self.inspect = None  # called with every outgoing body

    def request(self, method, url, headers, body, timeout):
        if url.endswith("/key"):
            self.key_reads += 1
            return 200, {}, {"data": {"free_model_daily_requests": {"limit": self.limit, "used": self.used,
                                                                    "remaining": max(0, self.limit - self.used)}}}
        if url.endswith("/models"):
            return 200, {}, {"data": [{"id": m, "name": m} for m in FREE] + [{"id": "openai/gpt-5", "name": "paid"}]}
        if self.inspect:
            self.inspect(body)
        self.used += 1
        self.posts.append(body)
        self.times.append(self.clock.t)
        a = self.answers.pop(0) if self.answers else OK
        if isinstance(a, BaseException):
            raise a
        if isinstance(a, tuple):
            return a
        return 200, {}, {"model": body["models"][0], "choices": [{"message": {"content": a}}],
                         "usage": {"prompt_tokens": 100, "completion_tokens": 20}}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, s)


@pytest.fixture
def ai(session, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test-0000000000abcd")
    clock = Clock()
    fake = FakeOpenRouter(clock)
    openrouter.set_transport(fake)
    openrouter.set_clock(clock.now, clock.sleep)
    openrouter.reset_pacing()
    privacy.clear_cache()
    yield fake
    openrouter.set_transport(None)
    openrouter.set_clock(None, None)
    openrouter.reset_pacing()


def ask(session, models=FREE, content="Which way, given the data?", **kw):
    return openrouter.chat_json(session, "analyst_1", models, [{"role": "user", "content": content}], SCHEMA, **kw)


# ---------- free models only ----------

def test_free_only_refuses_any_non_free_id_before_sending(session, ai):
    for models in (["openai/gpt-5"], ["google/gemma-4-31b-it:free", "anthropic/claude-sonnet-4.5"], ["openrouter/auto"]):
        with pytest.raises(openrouter.FreeOnlyError):
            ask(session, models)
    assert ai.posts == []  # nothing left the app
    refused = session.exec(select(AiCall).where(AiCall.status == "refused")).all()
    assert len(refused) == 3 and all(r.requests == 0 for r in refused)
    # the setting can't be saved with a paid model while the toggle is on
    c = TestClient(app)
    r = c.put("/api/ai/settings", json={"roster": [{"role": "judge", "label": "Judge", "models": ["openai/gpt-5"]}]})
    assert r.status_code == 400 and "not a :free model" in r.json()["detail"]["message"]
    # turned off, a paid id is allowed
    services.get_settings(session).ai_free_only = False
    session.commit()
    answer, call = ask(session, ["openai/gpt-5"])
    assert answer["view"] == "flat" and len(ai.posts) == 1 and ai.posts[0]["models"] == ["openai/gpt-5"]


def test_fallbacks_go_in_the_models_array(session, ai):
    roster = openrouter.roster(session)
    assert len(roster) == 5 and sum(r["role"].startswith("analyst") for r in roster) == 4
    assert all(len(r["models"]) == 3 and all(m.endswith(":free") for m in r["models"]) for r in roster)
    _, call = ask(session, roster[0]["models"])
    assert ai.posts[0]["models"] == roster[0]["models"] and "model" not in ai.posts[0]
    assert call.model_used == roster[0]["models"][0]


# ---------- privacy ----------

def _family(session):
    """Two portfolios with names, nicknames and real amounts, on synthetic prices."""
    provider.set_provider(SyntheticProvider())
    services.bump_revision()
    c = TestClient(app)
    c.put("/api/settings", json={"base_currency": "USD"})
    p2 = c.post("/api/portfolios", json={"name": "Keren", "aliases": "קרן"}).json()["id"]
    day = (dt.date.today() - dt.timedelta(days=400)).isoformat()
    for pid, t, q in ((1, "AAPL", 37), (1, "MSFT", 12), (p2, "AAPL", 9), (p2, "XOM", 55)):
        px = c.get(f"/api/price/{t}?date={day}").json()["close"]
        r = c.post(f"/api/transactions?portfolio={pid}", json={"ticker": t, "type": "BUY", "date": day, "quantity": q, "price": round(px, 2), "currency": "USD"})
        assert r.status_code < 300, r.text
    c.post(f"/api/transactions?portfolio={p2}", json={"ticker": "", "type": "DEPOSIT", "date": day, "quantity": 0, "price": 12345.67, "currency": "USD"})
    return c


def _private_values(session):
    from app.models import Portfolio
    from app.scenarios import books

    names = []
    for p in session.exec(select(Portfolio)).all():
        names += [p.name] + [a.strip() for a in (p.aliases or "").split(",") if a.strip()]
    ports, fam = books(session, None)
    amounts = [b.value for b in [*ports, fam]] + [h.value for b in [*ports, fam] for h in b.holdings] + [12345.67]
    return names, amounts


def _assert_clean(body, names, amounts):
    text = json.dumps(body, ensure_ascii=False)
    for n in names:
        assert not re.search(rf"(?<!\w){re.escape(n.lower())}(?!\w)", text.lower()), f"name {n!r} in payload"
    nums = [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*\.?\d*", text)]
    for a in amounts:
        assert not any(abs(x - round(a, 2)) < 0.005 or (x == int(x) and x == round(a) and a >= 1000) for x in nums), f"amount {a} in payload"


def test_every_payload_is_free_of_names_and_amounts(session, ai):
    _family(session)
    names, amounts = _private_values(session)
    assert "Keren" in names and "קרן" in names and len(amounts) > 5
    ai.inspect = lambda body: _assert_clean(body, names, amounts)  # runs on every request that leaves
    pack = context_pack.build(session, ["AAPL"])
    assert {h["ticker"] for h in pack["family"]["holdings"]} == {"AAPL", "MSFT", "XOM"}
    assert all(set(h) == {"ticker", "weight", "betas", "r2"} for h in pack["family"]["holdings"])
    msg = "Context pack:\n" + json.dumps(pack)
    ai.answers = ["not json at all", OK, OK]  # the repair request is checked too
    job = ai_jobs.create(session, "test", "privacy", [
        {"key": f"s{i}", "role": "analyst_1", "models": FREE, "messages": [{"role": "user", "content": msg}], "schema": SCHEMA}
        for i in range(2)])
    assert ai_jobs.run(session, job.id).status == "done"
    assert len(ai.posts) == 3  # two steps, one repair: all inspected

    # and the guard itself refuses what the test would catch
    for bad in ("Keren thinks oil will rise", "קרן חושבת", f"Family value {round(amounts[0], 2)}", "Deposit of 12,345.67"):
        with pytest.raises(privacy.PrivacyError):
            ask(session, content=bad)
    assert len(ai.posts) == 3  # none of them was sent


def test_context_pack_view_passes_the_privacy_check(session, ai):
    c = _family(session)
    v = c.get("/api/ai/context-pack?tickers=AAPL").json()
    assert v["privacy"]["ok"], v["privacy"]
    assert v["privacy"]["checked_names"] >= 2 and v["privacy"]["checked_amounts"] > 5
    pack = v["pack"]
    assert [m["symbol"] for m in pack["market"]] == ["^GSPC", "^NDX", "^VIX", "BZ=F", "CL=F", "^TNX", "GC=F", "DX-Y.NYB"]
    assert all(m["as_of"] for m in pack["market"])
    iran = {a["id"]: a for a in pack["analogs"] if a["id"].startswith("iran_") and "26" in a["id"]}
    assert set(iran) == {"iran_feb26", "iran_hormuz26", "iran_cease26", "iran_mou26", "iran_jul26"}
    from app.specs import factor_move
    a = iran["iran_feb26"]
    assert a["factor_moves_pct"]["oil"] == pytest.approx(factor_move(session, "oil", dt.date(2026, 2, 27), dt.date(2026, 3, 13)), abs=0.01)
    w = sum(h["weight"] for h in pack["family"]["holdings"]) + pack["family"]["cash_weight"]
    assert w == pytest.approx(1, abs=1e-3)
    s = pack["stocks"][0]
    assert s["ticker"] == "AAPL" and s["price"] and s["returns"]["12m"]["vs_spy"] is not None
    assert s["valuation"]["trailing_pe"]["value"] and s["valuation"]["trailing_pe"]["sector_median"]
    assert "valuation.forward_pe.own_5y_percentile" in s["missing"]  # no free history: marked, not guessed


def test_missing_fields_are_marked(session, ai):
    provider.set_provider(SyntheticProvider())
    from app import stock_pack

    etf = stock_pack.build(session, "SPY")
    assert etf["analysts"] is None and "analysts" in etf["missing"] and "growth.next_year_eps_growth_est" in etf["missing"]


# ---------- quota counter ----------

def test_rate_counter_matches_the_api(session, ai):
    ai.used = 10
    for _ in range(3):
        ask(session)
    ai.answers = [(500, {}, {"error": {"message": "upstream"}}), (502, {}, {"error": {"message": "upstream"}})]
    with pytest.raises(openrouter.AiError):
        ask(session)
    assert ai.used == 15  # 3 calls + a failed call with its one retry
    local = openrouter.quota_status(session, refresh=False)
    assert (local["used"], local["remaining"], local["limit"]) == (15, 35, 50)
    fresh = openrouter.refresh_quota(session, force=True)
    assert (fresh.used, fresh.remaining) == (15, 35)
    assert TestClient(app).get("/api/ai/quota").json()["remaining"] == 35
    calls = session.exec(select(AiCall).order_by(AiCall.id)).all()
    assert [c.requests for c in calls] == [1, 1, 1, 2] and calls[-1].status == "failed"
    assert all(c.payload_hash and c.role == "analyst_1" for c in calls)
    assert calls[0].prompt_tokens == 100 and calls[0].completion_tokens == 20 and calls[0].model_used == FREE[0]


def test_at_most_one_retry_and_no_retry_on_client_errors(session, ai):
    ai.answers = [(500, {}, {}), OK]
    _, call = ask(session)
    assert call.status == "ok" and call.requests == 2
    ai.answers = [(400, {}, {"error": {"message": "bad request"}})]
    with pytest.raises(openrouter.AiError):
        ask(session)
    assert len(ai.posts) == 3


def test_pacing_stays_under_20_a_minute(session, ai):
    for _ in range(45):
        ask(session)
    t = ai.times
    assert len(t) == 45
    assert max(sum(1 for y in t if x <= y < x + 60) for x in t) <= 19
    assert t[-1] - t[0] >= 120  # 45 requests need at least two full minutes


def test_invalid_json_gets_one_repair_then_fails(session, ai):
    ai.answers = ["```json\n{\"view\": \"up\"}\n```", OK]  # missing a required field, then fixed
    answer, call = ask(session)
    assert answer == json.loads(OK) and call.requests == 2
    assert "failed validation" in ai.posts[-1]["messages"][-1]["content"]
    ai.answers = ["nope", "{still not json"]
    with pytest.raises(openrouter.InvalidAnswer):
        ask(session)
    last = session.exec(select(AiCall).order_by(AiCall.id.desc())).first()
    assert last.status == "failed" and last.requests == 2


# ---------- jobs: resume ----------

def _steps(n):
    return [{"key": f"step{i}", "role": "analyst_1", "models": FREE,
             "messages": [{"role": "user", "content": f"Question {i}"}], "schema": SCHEMA} for i in range(n)]


def _sent_questions(posts):
    return [p["messages"][-1]["content"] for p in posts]


def test_resume_after_interruption_never_repeats_finished_calls(session, ai):
    job = ai_jobs.create(session, "test", "four steps", _steps(4))
    ai.answers = [OK, OK, Crash()]
    with pytest.raises(Crash):  # the app stops while step 3 is in flight
        ai_jobs.run(session, job.id)
    session.expire_all()
    assert session.get(AiJob, job.id).status == "running"
    assert [s.status for s in session.exec(select(AiJobStep).where(AiJobStep.job_id == job.id).order_by(AiJobStep.idx))] == ["done", "done", "running", "pending"]
    assert [j.id for j in ai_jobs.due(session)] == [job.id]  # picked up again on restart
    before = len(ai.posts)
    assert ai_jobs.run(session, job.id).status == "done"
    assert _sent_questions(ai.posts[before:]) == ["Question 2", "Question 3"]  # steps 1-2 not sent again
    assert _sent_questions(ai.posts).count("Question 0") == 1


def test_job_waits_for_the_next_day_when_quota_runs_out(session, ai):
    ai.used = 48  # 2 free requests left today
    job = ai_jobs.create(session, "test", "four steps", _steps(4))
    j = ai_jobs.run(session, job.id)
    assert j.status == "waiting_quota" and len(ai.posts) == 2
    resume_at = j.resume_after if j.resume_after.tzinfo else j.resume_after.replace(tzinfo=dt.timezone.utc)
    assert resume_at.hour == 0 and resume_at.date() == dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=1)
    assert ai_jobs.due(session) == []  # not before tomorrow
    # next UTC day: OpenRouter's counter is back to 0
    ai.used = 0
    openrouter.refresh_quota(session, force=True)
    assert [x.id for x in ai_jobs.due(session, now=resume_at + dt.timedelta(minutes=1))] == [job.id]
    assert ai_jobs.run(session, job.id).status == "done"
    assert _sent_questions(ai.posts) == ["Question 0", "Question 1", "Question 2", "Question 3"]


def test_no_key_sends_nothing(session, ai, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.setattr(openrouter, "ROOT", openrouter.ROOT / "nowhere")
    assert openrouter.api_key() == ""
    job = ai_jobs.create(session, "test", "x", _steps(1))
    assert ai_jobs.run(session, job.id).status == "queued" and ai.posts == []
    st = TestClient(app).get("/api/ai/status").json()
    assert st["key_set"] is False and st["quota"]["known"] is False
