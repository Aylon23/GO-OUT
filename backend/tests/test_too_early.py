"""P0-1: below 20 trading days nothing compares with the S&P 500: no ahead/behind anywhere."""
import datetime as dt

from app import person_reports, reports
from tests.test_portfolios import client, days  # noqa: F401 (fixture)

AHEAD = ["לפני המדד", "אחרי המדד", "ahead", "behind"]


def _buy(client, n_days_ago, pid=1):
    d = days(400)[-1 - n_days_ago].isoformat()
    r = client.post(f"/api/transactions?portfolio={pid}", json={"ticker": "AAPL", "type": "BUY", "date": d, "quantity": 1, "price": 100, "currency": "USD"})
    assert r.status_code < 300, r.text


def test_three_days_says_too_early_everywhere(client, session):
    _buy(client, 3)
    s = client.get("/api/portfolio?portfolio=1&range=MAX").json()["summary"]
    assert s["compare_ok"] is False and s["tracking_days"] == 3 and s["min_compare_days"] == 20
    row = client.get("/api/overview?range=MAX").json()["rows"][0]
    assert row["compare_ok"] is False
    one = reports.portfolio_report(session, 1, "week")
    assert one["spy"] is None and "מוקדם להשוות" in one["text"]
    fam = reports.all_report(session, "week")["text"]
    assert "מוקדם להשוות" in fam
    for text in (one["text"], fam):
        assert not any(w in text for w in AHEAD), text
    today = dt.date.today()
    pd = person_reports.person_data(session, 1, today - dt.timedelta(days=7), today)
    assert pd["compare_ok"] is False and pd["driver"]["sp500"] is None


def test_comparison_switches_on_at_20_days(client, session):
    _buy(client, 30)
    s = client.get("/api/portfolio?portfolio=1&range=MAX").json()["summary"]
    assert s["compare_ok"] is True
    assert client.get("/api/overview?range=MAX").json()["rows"][0]["compare_ok"] is True
    assert any(w in reports.portfolio_report(session, 1, "month")["text"] for w in AHEAD[:2])
