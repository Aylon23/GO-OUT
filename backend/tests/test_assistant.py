"""The assistant: answers from computed facts; names never leave; amounts are filled in here."""
import json

import pytest

from app import assistant
from app.models import ChatMessage
from tests.test_openrouter import _family, ai  # noqa: F401 (fixture)
from tests.test_panel import paid  # noqa: F401 (fixture)


@pytest.fixture
def chat(paid, monkeypatch):  # noqa: F811
    """The panel fixture, plus canned assistant answers."""
    inner = paid.request
    paid.chat_bodies = []
    paid.reply = {"answer": "Portfolio B הכי חשוף: [[B:-2.5]]. Portfolio A פחות.", "caveat": "ההערכה תלויה במחיר הנפט.",
                  "followups": ["ומה אם הנפט יורד?"], "ask_panel": False}

    def request(method, url, headers, body, timeout):
        if body and "(assistant_answer)" in body["messages"][0]["content"]:
            paid.chat_bodies.append(body)
            return 200, {}, {"model": body["models"][0], "choices": [{"message": {"content": json.dumps(paid.reply, ensure_ascii=False)}}],
                             "usage": {"prompt_tokens": 2000, "completion_tokens": 300, "cost": 0.02}}
        return inner(method, url, headers, body, timeout)

    monkeypatch.setattr(paid, "request", request)
    return paid


def test_names_become_labels_and_come_back(session, chat):
    c = _family(session)
    rid = c.post("/api/panel?wait=true", json={"text": "oil jumps 20%"}).json()["id"]
    msgs = c.post(f"/api/assistant/{rid}", json={"text": "מי יותר חשוף, קרן או My portfolio? ומה עם לקרן בטווח הארוך"}).json()
    body = chat.chat_bodies[0]
    sent = json.dumps(body, ensure_ascii=False)
    assert "קרן" not in sent and "Keren" not in sent and "My portfolio" not in sent and "12345" not in sent
    assert "Portfolio A" in sent and "Portfolio B" in sent
    assert body["messages"][-1]["content"].count("Portfolio B") == 2  # "קרן" and "לקרן"
    user, bot = msgs
    assert user["role"] == "user" and "קרן" in user["text"]  # shown as typed
    assert bot["text"].startswith("Keren הכי חשוף: \u2066−2.5%\u00a0(−$") and "My portfolio פחות" in bot["text"]
    assert bot["followups"] == ["ומה אם הנפט יורד?"] and bot["caveat"] and bot["cost"] == 0.02


def test_the_amount_is_the_portfolio_value_times_the_percent(session, chat):
    c = _family(session)
    rid = c.post("/api/panel?wait=true", json={"text": "oil jumps 20%"}).json()["id"]
    keren = next(lab for lab in assistant.labels(session) if lab["name"] == "Keren")
    bot = c.post(f"/api/assistant/{rid}", json={"text": "who is most exposed?"}).json()[-1]
    assert f"(−${keren['value'] * 0.025:,.0f})" in bot["text"]


def test_facts_carry_the_panel_numbers_and_only_percentages(session, chat):
    c = _family(session)
    run = c.post("/api/panel?wait=true", json={"text": "oil jumps 20%"}).json()
    c.post(f"/api/assistant/{run['id']}", json={"text": "why is XOM up?"})
    facts = json.loads(chat.chat_bodies[0]["messages"][1]["content"].split("\n", 1)[1])
    fam = run["result"]["tables"][0]
    assert facts["all_portfolios"]["models_average_pct"] == round(fam["avg"]["pct"], 1)
    xom = next(s for s in facts["stocks"] if s["symbol"] == "XOM")
    assert xom["by_model_pct"] == {"Claude": 6.0, "ChatGPT": 5.0, "Kimi": 7.0} and xom["engine_pct"] is not None
    assert set(facts["portfolios"]) == {"Portfolio A", "Portfolio B"}
    assert "value_amt" not in json.dumps(facts) and "amt" not in json.dumps(facts)


def test_a_follow_up_scenario_runs_the_engine_first(session, chat):
    c = _family(session)
    rid = c.post("/api/panel?wait=true", json={"text": "oil jumps 20%"}).json()["id"]
    bot = c.post(f"/api/assistant/{rid}", json={"text": "and what if oil drops 20% over a month?"}).json()[-1]
    facts = json.loads(chat.chat_bodies[0]["messages"][1]["content"].split("\n", 1)[1])
    eng = facts["engine_on_this_message"]
    assert eng["read_as"] == [{"symbol": "USO", "move_pct": -20.0}] and eng["over_weeks"] == 4.3
    assert set(eng["portfolios"]) == {"Portfolio A", "Portfolio B"}
    assert set(bot["engine"]["portfolios"]) == {"My portfolio", "Keren"}  # shown with real names
    c.post(f"/api/assistant/{rid}", json={"text": "explain the first point"})
    assert "engine_on_this_message" not in chat.chat_bodies[1]["messages"][1]["content"]  # not a scenario


def test_the_conversation_is_kept_and_sent_along(session, chat):
    c = _family(session)
    rid = c.post("/api/panel?wait=true", json={"text": "oil jumps 20%"}).json()["id"]
    c.post(f"/api/assistant/{rid}", json={"text": "first question"})
    c.post(f"/api/assistant/{rid}", json={"text": "second question"})
    roles = [m["role"] for m in chat.chat_bodies[1]["messages"]]
    assert roles == ["system", "user", "user", "assistant", "user"]
    assert len(c.get(f"/api/assistant/{rid}").json()) == 4
    assert session.query(ChatMessage).count() == 4


def test_it_works_without_a_run_and_refuses_an_amount(session, chat):
    c = _family(session)
    bot = c.post("/api/assistant/0", json={"text": "what do the portfolios hold?"}).json()[-1]
    assert bot["role"] == "assistant"
    facts = json.loads(chat.chat_bodies[0]["messages"][1]["content"].split("\n", 1)[1])
    assert facts["portfolios"]["Portfolio A"]["top_holdings"][0]["symbol"] in ("AAPL", "MSFT")
    r = c.post("/api/assistant/0", json={"text": "I deposited 12345.67 last year, was it smart?"})
    assert r.status_code == 400 and "amount" in r.json()["detail"]["message"] and len(chat.chat_bodies) == 1


def test_the_spotlight_tells_the_assistant_each_portfolio_s_exposure(session, chat):
    c = _family(session)
    rid = c.post("/api/panel?wait=true", json={"text": "the AAPL report: where does it go?"}).json()["id"]
    c.post(f"/api/assistant/{rid}", json={"text": "who is most exposed to AAPL?"})
    facts = json.loads(chat.chat_bodies[0]["messages"][1]["content"].split("\n", 1)[1])
    (f,) = facts["stocks_in_focus"]
    held = {h["portfolio"]: h for h in f["held_in"]}
    assert set(held) == {"Portfolio A", "Portfolio B"}
    for h in held.values():
        assert h["effect_on_portfolio_1m_pct"] == pytest.approx(h["weight_pct"] / 100 * f["models_average_1m_pct"], abs=0.05)
    assert "Never say an amount is unavailable" in chat.chat_bodies[0]["messages"][0]["content"]
