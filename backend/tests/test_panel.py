"""The analyst panel: three paid models, each alone, a move per holding; totals and summary computed here."""
import json

import pytest

from app import openrouter, panel
from app.models import AiCall, PanelRun
from tests.test_openrouter import _family, ai  # noqa: F401 (fixture)

# The preferred ids are not in this catalog, so every seat falls back to the vendor's newest main model.
CATALOG = [
    {"id": "anthropic/claude-opus-9", "created": 300, "pricing": {"prompt": "0.000005", "completion": "0.000025"}},
    {"id": "anthropic/claude-haiku-9", "created": 310, "pricing": {"prompt": "0.000001", "completion": "0.000005"}},  # small
    {"id": "openai/gpt-9", "created": 200, "pricing": {"prompt": "0.000002", "completion": "0.00001"}},
    {"id": "openai/gpt-9-pro", "created": 210, "pricing": {"prompt": "0.00003", "completion": "0.00018"}},  # too expensive
    {"id": "openai/gpt-9-mini", "created": 220, "pricing": {"prompt": "0.0000002", "completion": "0.000001"}},
    {"id": "moonshotai/kimi-9", "created": 100, "pricing": {"prompt": "0.000003", "completion": "0.000015"}},
    {"id": "moonshotai/kimi-9-flash", "created": 120, "pricing": {"prompt": "0.0000002", "completion": "0.0000005"}},
    {"id": "google/gemini-9", "created": 500, "pricing": {"prompt": "0.000003", "completion": "0.000015"}},  # no seat for it
    {"id": "x-ai/grok-9", "created": 400, "pricing": {"prompt": "0.000003", "completion": "0.000015"}},  # no seat for it
]
MOVES = {"anthropic/claude-opus-9": {"AAPL": -4.0, "MSFT": -2.0, "XOM": 6.0},
         "openai/gpt-9": {"AAPL": -6.0, "MSFT": -3.0, "XOM": 5.0},
         "moonshotai/kimi-9": {"AAPL": 3.0, "MSFT": -1.0, "XOM": 7.0}}  # Kimi is on the other side on AAPL


def answer(model, focus=()):
    return json.dumps({"method": "שיטה", "briefing": "תקציר", "market_view": "שוק", "assumptions": ["הנחה"], "sources": ["https://example.com"],
                       "focus": [{"symbol": f, "report_summary": "סיכום הדוח", "move_1m_pct": MOVES[model][f], "move_3m_pct": MOVES[model][f] * 2, "view": "דעה"} for f in focus],
                       "stocks": [{"symbol": s, "move_pct": m, "low_pct": m - 3, "high_pct": m + 3, "confidence": "medium", "reason": "נימוק"}
                                  for s, m in MOVES[model].items()]}, ensure_ascii=False)


@pytest.fixture
def paid(ai, monkeypatch):  # noqa: F811
    bodies = []
    fail = set()

    def request(method, url, headers, body, timeout):
        if url.endswith("/models"):
            return 200, {}, {"data": CATALOG}
        if url.endswith("/key"):
            return 200, {}, {"data": {}}
        bodies.append(body)
        m = body["models"][0]
        if m in fail:
            return 500, {}, {"error": {"message": "upstream down"}}
        focus = json.loads(body["messages"][1]["content"]).get("focus") or ()
        return 200, {}, {"model": m, "choices": [{"message": {"content": answer(m, focus)}}],
                         "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "cost": 0.05}}

    monkeypatch.setattr(ai, "request", request)
    monkeypatch.setattr(openrouter, "_catalog_cache", None)
    ai.bodies, ai.fail = bodies, fail
    return ai


def test_seats_pick_the_newest_flagship_at_a_normal_price(session, paid):
    seats = {s["key"]: s for s in openrouter.panel_seats(session)}
    assert seats["claude"]["model"] == "anthropic/claude-opus-9"  # not the newer haiku
    assert seats["gpt"]["model"] == "openai/gpt-9"  # not the pro tier, not the mini
    assert seats["kimi"]["model"] == "moonshotai/kimi-9" and "grok" not in seats and "gemini" not in seats
    assert seats["gpt"]["price_out"] == pytest.approx(10.0)


def test_a_run_gives_every_portfolio_a_table_and_totals_computed_here(session, paid):
    c = _family(session)
    r = c.post("/api/panel?wait=true", json={"text": "oil jumps 20%", "horizon": "1m"}).json()
    assert r["status"] == "done", r
    assert r["progress"]["pct"] == 100 and set(r["progress"]["models"].values()) == {"done"}
    assert r["cost"] == pytest.approx(0.15)  # three calls at $0.05
    fam, *ports = r["result"]["tables"]
    assert fam["name"] == "All portfolios" and len(ports) == 2
    for t in [fam, *ports]:
        for k, m in t["models"].items():
            by_hand = sum(row["weight"] * row["models"][k]["move"] for row in t["rows"])
            assert m["pct"] == pytest.approx(by_hand) and m["amt"] == pytest.approx(t["value_amt"] * by_hand / 100)
        assert t["avg"]["pct"] == pytest.approx(sum(row["weight"] * row["avg"] for row in t["rows"]))
    aapl = next(x for x in fam["rows"] if x["symbol"] == "AAPL")
    assert aapl["avg"] == pytest.approx((-4 - 6 + 3) / 3) and aapl["split"] and aapl["low"] == -6 and aapl["high"] == 3
    assert not next(x for x in fam["rows"] if x["symbol"] == "XOM")["split"]
    assert aapl["engine"] is not None and r["result"]["engine_shocks"]  # the local rules read "oil jumps 20%"
    assert any("חלוקים" in line and "AAPL" in line for line in r["result"]["summary_he"])
    assert r["analysts"]["claude"]["method"] == "שיטה"


def test_only_tickers_and_weights_leave_and_web_search_is_on(session, paid):
    c = _family(session)
    c.post("/api/panel?wait=true", json={"text": "the Fed cuts rates", "horizon": "1w", "web": True})
    assert len(paid.bodies) == 3
    for body in paid.bodies:
        text = json.dumps(body, ensure_ascii=False)
        assert "Keren" not in text and "קרן" not in text and "12345.67" not in text and "12,345" not in text
        assert body["plugins"] == [{"id": "web", "max_results": 3}] and body["usage"] == {"include": True}
        sent = json.loads(body["messages"][1]["content"])
        assert {h["symbol"] for h in sent["holdings"]} == {"AAPL", "MSFT", "XOM"}
        assert all(set(h) == {"symbol", "company", "sector", "weight_pct", "betas"} for h in sent["holdings"])
    assert all(c.paid for c in session.exec(__import__("sqlmodel").select(AiCall)).all())


def test_one_model_down_still_answers_and_says_who_is_missing(session, paid):
    c = _family(session)
    paid.fail.add("moonshotai/kimi-9")
    r = c.post("/api/panel?wait=true", json={"text": "oil jumps 20%"}).json()
    assert r["status"] == "done" and r["progress"]["models"]["kimi"] == "failed"
    assert set(r["result"]["tables"][0]["models"]) == {"claude", "gpt"}
    assert "upstream down" in r["result"]["errors"]["kimi"]
    assert any("Kimi" in line for line in r["result"]["summary_he"])


def test_budget_stops_a_run_before_anything_is_sent(session, paid):
    c = _family(session)
    c.put("/api/panel/settings", json={"budget_usd": 0.12})
    assert c.post("/api/panel?wait=true", json={"text": "oil jumps 20%"}).json()["status"] == "done"  # spends $0.15
    r = c.post("/api/panel?wait=true", json={"text": "oil jumps 20%"})
    assert r.status_code == 400 and "budget" in r.json()["detail"]["message"]
    assert len(paid.bodies) == 3
    assert c.get("/api/panel/setup").json()["budget"] == {"limit": 0.12, "spent": 0.15, "left": 0.0}


def test_budget_reached_mid_run_stops_the_remaining_models(session, paid):
    c = _family(session)
    c.put("/api/panel/settings", json={"budget_usd": 0.1})
    r = c.post("/api/panel?wait=true", json={"text": "oil jumps 20%"}).json()
    assert r["status"] == "done" and len(paid.bodies) == 2  # the third model is not asked
    assert "budget" in r["result"]["errors"]["kimi"]


def test_a_question_with_a_name_is_refused_before_sending(session, paid):
    c = _family(session)
    r = c.post("/api/panel?wait=true", json={"text": "what happens to Keren if oil jumps"})
    assert r.status_code == 400 and "names" in r.json()["detail"]["message"] and not paid.bodies


def test_free_calls_are_unchanged_and_paid_models_stay_blocked_there(session, paid):
    with pytest.raises(openrouter.FreeOnlyError):
        openrouter.chat_json(session, "x", ["openai/gpt-9"], [{"role": "user", "content": "hi"}], {"type": "object"})


def test_models_are_scored_against_prices_once_the_horizon_has_passed(session, paid):
    import datetime as dt

    from app.market.cache import load_history

    c = _family(session)
    rid = c.post("/api/panel?wait=true", json={"text": "oil jumps 20%", "horizon": "1w"}).json()["id"]
    assert c.get("/api/panel/leaderboard").json() == []  # five trading days have not passed yet
    run = session.get(PanelRun, rid)
    prices = {}
    for t in ("AAPL", "MSFT", "XOM"):  # pretend the run was made 30 sessions ago
        f = load_history(session, t)
        prices[t] = {"date": f.index[-31].isoformat(), "close": float(f["close"].iloc[-31])}
    run.prices = json.dumps(prices)
    session.add(run)
    session.commit()
    board = c.get("/api/panel/leaderboard").json()
    assert {b["key"] for b in board} == {"claude", "gpt", "kimi"} and all(b["stocks"] == 3 and b["runs"] == 1 for b in board)
    f = load_history(session, "AAPL")
    actual = (float(f["close"].iloc[-26]) / float(f["close"].iloc[-31]) - 1) * 100  # the 5th session after the run
    score = c.get(f"/api/panel/{rid}").json()["score"]
    assert score["actual"]["AAPL"] == pytest.approx(actual)
    assert board == sorted(board, key=lambda b: b["avg_error"])


def test_paid_calls_only_start_from_a_click():
    """Money is spent only when the user sends a question or a chat message: no scheduler, bot or page
    load may call a paid model."""
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    src = {p.relative_to(app_dir).as_posix(): p.read_text() for p in app_dir.rglob("*.py")}
    # only the panel and the assistant make paid calls (openrouter.py is where the switch is defined)
    assert sorted(n for n, t in src.items() if "paid=True" in t and n != "openrouter.py") == ["assistant.py", "panel.py"]
    # and each is started from exactly one place: its own POST route
    for call, route in (("panel.start(", '@router.post("/panel")'), ("assistant.ask(", '@router.post("/assistant/{run_id}")')):
        assert [n for n, t in src.items() if call in t] == ["api/routes.py"], call
        routes = src["api/routes.py"]
        assert routes.count(call) == 1 and route in routes[routes.index(call) - 600:routes.index(call)], call


def test_a_batch_route_is_never_picked_as_the_model(session, paid, monkeypatch):
    """The real catalog lists special routes of a model ("...:batch") that the chat endpoint refuses."""
    extra = [{"id": "anthropic/claude-fable-9:batch", "created": 999, "pricing": {"prompt": "0.000002", "completion": "0.00001"}},
             {"id": "openai/gpt-9-chat", "created": 999, "pricing": {"prompt": "0.000002", "completion": "0.00001"}},
             {"id": "moonshotai/kimi-9:free", "created": 999, "pricing": {"prompt": "0.000002", "completion": "0.00001"}}]
    inner = paid.request
    monkeypatch.setattr(paid, "request", lambda m, url, h, b, t: (200, {}, {"data": CATALOG + extra}) if url.endswith("/models") else inner(m, url, h, b, t))
    monkeypatch.setattr(openrouter, "_catalog_cache", None)
    assert [s["model"] for s in openrouter.panel_seats(session)] == ["openai/gpt-9", "anthropic/claude-opus-9", "moonshotai/kimi-9"]


def test_request_settings_and_market_levels_are_not_mistaken_for_amounts(session, paid):
    """A deposit of exactly 9,000 used to block every panel request, because max_tokens is 9000."""
    import datetime as dt

    from app import context_pack, privacy
    from app.models import Transaction

    c = _family(session)
    level = next(float(r["level"]) for r in context_pack.market_snapshot(session) if r.get("level") and float(r["level"]) >= 1000)
    for amount in (9000.0, round(level, 2)):  # the second equals a public index level
        session.add(Transaction(portfolio_id=1, date=dt.date.today() - dt.timedelta(days=30), type="DEPOSIT", ticker="", quantity=0, price=amount, currency="USD"))
    session.commit()
    privacy.clear_cache()
    r = c.post("/api/panel?wait=true", json={"text": "oil jumps 20%"}).json()
    assert r["status"] == "done" and len(paid.bodies) == 3 and paid.bodies[0]["max_tokens"] == panel.MAX_TOKENS
    # an amount typed into the question is still refused
    bad = c.post("/api/panel?wait=true", json={"text": "I added 9,000 last month, what now?"})
    assert bad.status_code == 400 and "amount" in bad.json()["detail"]["message"]


def test_a_failed_run_names_every_model_and_its_error(session, paid):
    c = _family(session)
    paid.fail.update({"anthropic/claude-opus-9", "openai/gpt-9", "moonshotai/kimi-9"})
    r = c.post("/api/panel?wait=true", json={"text": "oil jumps 20%"}).json()
    assert r["status"] == "failed"
    for part in ("Claude (anthropic/claude-opus-9)", "ChatGPT (openai/gpt-9)", "Kimi (moonshotai/kimi-9)", "upstream down"):
        assert part in r["note"], r["note"]


def test_the_picked_models_are_used_while_openrouter_lists_them(session, paid, monkeypatch):
    """The three seats were chosen for results per dollar; each asks with its own reasoning effort."""
    real = [{"id": i, "created": 1, "pricing": {"prompt": "0.000002", "completion": "0.00001"}}
            for i in ("openai/gpt-6.1-sol", "moonshotai/kimi-k3.1-pro", "anthropic/claude-opus-5.5", "anthropic/claude-fable-5.2", "google/gemini-4-carbon")]
    inner = paid.request
    MOVES.update({"openai/gpt-6.1-sol": MOVES["openai/gpt-9"], "moonshotai/kimi-k3.1-pro": MOVES["moonshotai/kimi-9"], "anthropic/claude-opus-5.5": MOVES["anthropic/claude-opus-9"]})
    monkeypatch.setattr(paid, "request", lambda m, url, h, b, t: (200, {}, {"data": real}) if url.endswith("/models") else inner(m, url, h, b, t))
    monkeypatch.setattr(openrouter, "_catalog_cache", None)
    seats = openrouter.panel_seats(session)
    assert [(s["label"], s["model"], s["effort"]) for s in seats] == [("ChatGPT", "openai/gpt-6.1-sol", "high"), ("Claude", "anthropic/claude-opus-5.5", "medium"),
                                                                    ("Kimi", "moonshotai/kimi-k3.1-pro", "high")]
    c = _family(session)
    run = c.post("/api/panel?wait=true", json={"text": "oil jumps 20%"}).json()
    assert run["status"] == "done", run["note"]
    assert {b["models"][0]: b["reasoning"]["effort"] for b in paid.bodies} == {"openai/gpt-6.1-sol": "high", "moonshotai/kimi-k3.1-pro": "high", "anthropic/claude-opus-5.5": "medium"}
    # a model set by hand in Settings wins
    c.put("/api/panel/settings", json={"models": {"claude": "anthropic/claude-fable-5.2"}})
    assert next(s for s in openrouter.panel_seats(session) if s["key"] == "claude")["model"] == "anthropic/claude-fable-5.2"


def test_a_question_about_one_holding_gets_a_spotlight_with_two_horizons(session, paid):
    """ "What does the Apple report mean?" opens with Apple: each model's summary and its 1 and 3 month call."""
    c = _family(session)
    assert panel.focus_symbols(session, "אתמול היה הדוח של AAPL, מה ההשפעה?") == ["AAPL"]
    assert panel.focus_symbols(session, "what does the XOM report mean for apple?") == ["XOM", "AAPL"]
    assert panel.focus_symbols(session, "oil jumps 20%") == []
    r = c.post("/api/panel?wait=true", json={"text": "Yesterday's AAPL report: summarise it and where does the stock go?"}).json()
    assert json.loads(paid.bodies[0]["messages"][1]["content"])["focus"] == ["AAPL"]
    (f,) = r["result"]["focus"]
    assert f["symbol"] == "AAPL" and f["avg_1m"] == pytest.approx((-4 - 6 + 3) / 3) and f["avg_3m"] == pytest.approx(2 * (-4 - 6 + 3) / 3)
    assert f["low_1m"] == -6 and f["high_1m"] == 3 and f["models"]["gpt"]["summary"] == "סיכום הדוח"
    # which portfolios hold it, and what the average means for each, computed here
    assert {h["name"] for h in f["held_in"]} == {"My portfolio", "Keren"}
    fam = r["result"]["tables"][0]
    aapl = next(x for x in fam["rows"] if x["symbol"] == "AAPL")
    assert f["amt_1m"] == pytest.approx(aapl["value_amt"] * f["avg_1m"] / 100)
    assert sum(h["amt_1m"] for h in f["held_in"]) == pytest.approx(f["amt_1m"])
    assert r["result"]["summary_he"][0].startswith("\u2066AAPL\u2069: ממוצע 3 המודלים")
    # a question that names no holding has no spotlight, and the models are not asked for one
    r2 = c.post("/api/panel?wait=true", json={"text": "oil jumps 20%"}).json()
    assert r2["result"]["focus"] == [] and "focus" not in json.loads(paid.bodies[-1]["messages"][1]["content"])


def test_web_search_turns_itself_on_for_news_and_holdings_only(session, paid):
    c = _family(session)
    for text, web in (("the Fed cuts rates by a quarter point", False), ("oil jumps 20%", False),
                      ("אתמול היה הדוח של AAPL", True), ("what did the Fed say today?", True), ("where does XOM go?", True)):
        assert c.get("/api/panel/web", params={"text": text}).json()["web"] is web, text
    r = c.post("/api/panel?wait=true", json={"text": "the Fed cuts rates by a quarter point"}).json()  # web not sent: decided here
    assert r["web"] is False and all("plugins" not in b for b in paid.bodies)
    r = c.post("/api/panel?wait=true", json={"text": "the Fed cuts rates", "web": True}).json()  # the switch wins
    assert r["web"] is True and all("plugins" in b for b in paid.bodies[3:])


def test_links_inside_the_prose_move_to_the_sources():
    found = []
    text = panel.unlink("Revenue 3.66bn ([investing.com](https://www.investing.com/a)) and backlog [CNBC](https://cnbc.com/b) grew.", found)
    assert text == "Revenue 3.66bn and backlog CNBC grew." and found == ["https://www.investing.com/a", "https://cnbc.com/b"]
    ans = {"method": "m", "briefing": "b ([x.com](https://x.com/1))", "market_view": "", "assumptions": [], "sources": ["Reuters"],
           "stocks": [{"symbol": "AAPL", "move_pct": 1, "low_pct": 0, "high_pct": 2, "confidence": "low", "reason": "r [y](https://y.com/2)"}]}
    out = panel.clean_answer(ans, ["AAPL"])
    assert out["briefing"] == "b" and out["stocks"]["AAPL"]["reason"] == "r y" and out["sources"] == ["Reuters", "https://x.com/1", "https://y.com/2"]


def test_a_repair_does_not_search_the_web_again(session, paid, monkeypatch):
    inner = paid.request
    first = {"n": 0}

    def request(m, url, h, body, t):
        if body and url.endswith("/chat/completions") and first["n"] == 0:
            first["n"] += 1
            paid.bodies.append(body)
            return 200, {}, {"model": body["models"][0], "choices": [{"message": {"content": "not json"}}], "usage": {"cost": 0.01}}
        return inner(m, url, h, body, t)

    monkeypatch.setattr(paid, "request", request)
    c = _family(session)
    c.post("/api/panel?wait=true", json={"text": "AAPL report"})
    repair = next(b for b in paid.bodies[1:] if b["messages"][-1]["content"].startswith("That answer failed"))
    assert "plugins" not in repair and "plugins" in paid.bodies[0]
