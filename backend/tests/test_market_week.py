"""The weekly market summary: numbers from the price data, words from one model, every number checked."""
import datetime as dt
import json

import pytest

from app import market_week, person_reports
from app.models import MarketWeek
from tests.test_openrouter import _family, ai  # noqa: F401 (fixture)
from tests.test_panel import CATALOG, paid  # noqa: F401 (fixture)

FS = [{"symbol": "^GSPC", "name": "S&P 500", "what": "", "kind": "index", "level": 6812.4, "change_pct": 1.6},
      {"symbol": "ILS=X", "name": "דולר-שקל", "what": "", "kind": "rate", "level": 3.71, "change_pct": -0.8},
      {"symbol": "^TNX", "name": "10y", "what": "", "kind": "yield", "level": 4.12, "change_pts": -0.15}]


def test_only_the_app_s_numbers_survive():
    dropped = []
    text = ("ה-S&P 500 עלה 1.6% השבוע. הדולר ירד ב-0.8% מול השקל. התשואה ירדה ל-4.12%. "
            "אנבידיה זינקה 7% אחרי הדוח. המדד הגיע ל-6,812 נקודות. ב-12 בנובמבר פורסם מדד המחירים של 2026.")
    out = market_week.checked(text, FS, dropped)
    assert "7%" not in out and dropped == ["אנבידיה זינקה 7% אחרי הדוח."]
    assert market_week.checked("החברה הרוויחה 5 מיליארד דולר.", FS, []) == ""
    for kept in ("1.6%", "0.8%", "4.12%", "6,812", "12 בנובמבר"):
        assert kept in out, kept


def answer(model):
    return json.dumps({"headline": "שבוע חיובי בוול סטריט.", "what_happened": "ה-S&P 500 עלה. אנבידיה זינקה 9.3% השבוע.",
                       "drivers": [{"title": "הפד", "text": "הבנק המרכזי של ארה\"ב רמז על הורדת ריבית ([cnbc.com](https://cnbc.com/x))."}],
                       "next_week": "בשבוע הבא: יתפרסם מדד המחירים לצרכן.", "sources": ["Reuters"]}, ensure_ascii=False)


@pytest.fixture
def week(paid, monkeypatch):  # noqa: F811
    inner = paid.request
    paid.market_bodies = []

    def request(m, url, h, body, t):
        if body and "(weekly_market_summary)" in body["messages"][0]["content"]:
            paid.market_bodies.append(body)
            return 200, {}, {"model": body["models"][0], "choices": [{"message": {"content": answer(body["models"][0])}}], "usage": {"cost": 0.04}}
        return inner(m, url, h, body, t)

    monkeypatch.setattr(paid, "request", request)
    return paid


def test_made_once_a_week_with_web_search_and_no_portfolio_data(session, week):
    _family(session)
    end = dt.date.today() - dt.timedelta(days=1)
    m = market_week.payload(market_week.make(session, end))
    assert m["status"] == "done" and m["cost"] == 0.04
    assert m["text"]["what_happened"] == "ה-S&P 500 עלה." and "אנבידיה זינקה 9.3% השבוע." in m["dropped"]
    assert m["text"]["drivers"][0]["text"].endswith("רמז על הורדת ריבית.") and "https://cnbc.com/x" in m["text"]["sources"]
    assert {f["symbol"] for f in m["facts"]} >= {"^GSPC", "ILS=X"}
    assert m["text"]["next_week"] == "יתפרסם מדד המחירים לצרכן."  # the card adds "בשבוע הבא" itself
    (body,) = week.market_bodies
    assert body["plugins"] and body["reasoning"] == {"effort": "medium"}
    sent = json.dumps(body, ensure_ascii=False)
    for private in ("Keren", "קרן", "AAPL", "MSFT", "XOM", "12345"):
        assert private not in sent, private
    market_week.make(session, end)  # the same week again: kept, not asked again
    assert len(week.market_bodies) == 1 and session.query(MarketWeek).count() == 1


def test_weekly_reports_include_the_market_card_only_while_switched_on(session, week, tmp_path, monkeypatch):
    c = _family(session)
    monkeypatch.setattr(person_reports, "reports_dir", lambda: tmp_path)
    end = dt.date.today() - dt.timedelta(days=1)
    made = person_reports.generate(session, "weekly", end - dt.timedelta(days=6), end, "he")
    assert made[0].portfolio_id == 0 and made[0].name == "Market" and (tmp_path / made[0].png).stat().st_size > 10_000
    c.put("/api/settings", json={"market_summary": False})
    made = person_reports.generate(session, "weekly", end - dt.timedelta(days=13), end - dt.timedelta(days=7), "he")
    assert all(r.portfolio_id != 0 for r in made) and len(week.market_bodies) == 1
    made = person_reports.generate(session, "monthly", end - dt.timedelta(days=29), end, "he")
    assert all(r.portfolio_id != 0 for r in made)


def test_without_a_key_the_numbers_still_come(session, week, monkeypatch):
    _family(session)
    monkeypatch.delenv("OPENROUTER_API_KEY")
    from app import openrouter
    monkeypatch.setattr(openrouter, "api_key", lambda: "")
    m = market_week.payload(market_week.make(session, dt.date.today() - dt.timedelta(days=1)))
    assert m["status"] == "failed" and m["facts"] and not week.market_bodies
    png = person_reports.market_card(m, "2026-11-08", "2026-11-14")
    assert png[:4] == b"\x89PNG"
