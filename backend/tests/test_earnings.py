"""Earnings briefs: import, reaction history, the prediction journal, candidates."""
import datetime as dt
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import earnings, specs
from app.main import app
from app.models import EarningsItem
from tests.conftest import frame

EXAMPLE = json.loads((Path(__file__).resolve().parents[2] / "docs/examples/earnings-brief.example.json").read_text(encoding="utf-8"))


def bdays(start: dt.date, n: int) -> list[dt.date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


@pytest.fixture
def market(session, fake):
    days = bdays(dt.date(2025, 1, 2), 400)
    fake.frames["ILS=X"] = frame([(d.isoformat(), 3.5) for d in days])
    fake.frames["SPY"] = frame([(d.isoformat(), 100.0) for d in days])
    # ACME: flat at 100; reports after the close on days[100] -> +10% next day, then drifts to 112 by 20 sessions
    acme = []
    for i, d in enumerate(days):
        if i <= 100:
            px = 100.0
        elif i <= 119:
            px = 110.0 + (i - 101) * (2.0 / 18)  # 110 on the reaction day -> 112 on its 19th session after
        else:
            px = 112.0
        acme.append((d.isoformat(), px))
    fake.frames["ACME"] = frame(acme)
    fake.earnings = {"ACME": [(days[100], "after_close"), (days[300], "after_close")], "MSFT": [(days[300], "after_close")]}
    session.days = days
    return session


def test_example_brief_is_valid_and_imports(market):
    assert earnings.validate(EXAMPLE) == []
    bad = json.loads(json.dumps(EXAMPLE))
    bad["companies"][0]["guidance"] = "great"
    assert any("guidance" in e for e in earnings.validate(bad))
    c = TestClient(app)
    text = "Morning brief:\n```json\n" + json.dumps(EXAMPLE) + "\n```"
    assert c.post("/api/earnings/import", json={"text": text}).json() == {"date": "2026-10-29", "companies": 2}
    assert c.get("/api/earnings").json()["date"] == "2026-10-29"
    # importing again replaces, it doesn't duplicate
    c.post("/api/earnings/import", json={"text": text})
    assert len(market.exec(earnings.select(EarningsItem)).all()) == 2


def test_inbox_accepts_earnings_brief(market, tmp_path):
    (tmp_path / "brief.json").write_text(json.dumps(EXAMPLE), encoding="utf-8")
    out = specs.process_inbox(market, tmp_path)
    assert out[0]["ok"] and "earnings brief" in out[0]["message"]
    assert (tmp_path / "processed" / "brief.json").exists()


def test_history_stats_for_a_known_symbol(market):
    days = market.days
    h = earnings.history(market, "ACME", days[300])
    assert h["summary"]["n"] == 1
    e = h["events"][0]
    assert e["reaction_date"] == days[101].isoformat()  # after the close -> next session
    assert e["reaction"] == pytest.approx(0.10)
    assert e["d5"] == pytest.approx((110 + 4 * 2 / 18) / 100 - 1)
    assert e["d20"] == pytest.approx(0.12)
    assert e["continued"] is True and h["summary"]["continued_share"] == 1.0
    before = earnings.moves_after(earnings._prices(market, "ACME"), days[100], "before_open")
    assert before["reaction_date"] == days[100].isoformat() and before["reaction"] == pytest.approx(0.0)


def test_journal_scores_after_5_and_20_sessions_without_look_ahead(market):
    days = market.days
    brief = {"kind": "earnings_brief", "spec_version": 1, "date": days[100].isoformat(), "companies": [{
        "symbol": "ACME", "name": "Acme", "reported": "after_close", "results": {}, "guidance": "raised", "key_points": [],
        "outlook": {"days": "up", "weeks": "flat", "days_direction": "up", "weeks_direction": "flat", "confidence": "high"}}]}
    earnings.import_brief(market, brief, today=days[100])  # imported the morning after the report
    it = market.exec(earnings.select(EarningsItem)).first()
    assert it.base_close is None  # the reaction session hasn't happened yet
    # 10 sessions later: only the 5-day score exists
    earnings.score_item(market, it, today=days[111])
    assert it.base_date == days[101] and it.base_close == pytest.approx(110)
    assert it.move_5d == pytest.approx((110 + 5 * 2 / 18) / 110 - 1) and it.score_5d == "hit"
    assert it.move_20d is None and it.score_20d == "" and it.score == ""
    earnings.score_item(market, it, today=days[200])
    assert it.move_20d == pytest.approx(112 / 110 - 1) and it.score_20d == "hit"  # +1.8% is within the 2% "flat" band
    assert it.score == "hit"
    j = earnings.journal(market)
    assert j["accuracy"]["high"]["overall"] == {"n": 1, "hit": 1.0}


def test_candidates_family_first_and_capped(market):
    days = market.days
    c = TestClient(app)
    c.post("/api/transactions", json={"ticker": "ACME", "type": "BUY", "date": days[150].isoformat(), "quantity": 1, "price": 112, "currency": "USD"})
    out = c.get(f"/api/earnings/candidates?date={days[300].isoformat()}").json()
    syms = [x["symbol"] for x in out["candidates"]]
    assert syms[0] == "ACME" and out["candidates"][0]["family"] is True
    assert "MSFT" in syms and len(syms) <= 8
    assert c.get(f"/api/earnings/candidates?date={days[299].isoformat()}").json()["candidates"] == []
