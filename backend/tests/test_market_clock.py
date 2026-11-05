"""P0-4: "Today" is not today. Yesterday's close is "Last session, Tue 3 Nov (close)", plus a market state."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.market import clock

U = dt.timezone.utc
TUE, WED = dt.date(2026, 11, 3), dt.date(2026, 11, 4)


def at(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=U)


@pytest.mark.parametrize("utc,state,last", [
    ("2026-11-04T11:40", "pre", TUE),      # 13:40 in Israel, 06:40 in New York
    ("2026-11-04T15:00", "open", TUE),
    ("2026-11-04T22:00", "after", WED),
    ("2026-11-05T02:00", "closed", WED),
    ("2026-11-07T15:00", "closed", dt.date(2026, 11, 6)),   # Saturday
    ("2026-11-26T16:00", "closed", dt.date(2026, 11, 25)),  # Thanksgiving
    ("2026-11-27T19:00", "after", dt.date(2026, 11, 27)),   # half day closes at 13:00
])
def test_status(utc, state, last):
    st = clock.status(at(utc))
    assert st["state"] == state and st["last_session"] == last.isoformat()


def test_labels():
    # the review's case: Wednesday 13:40 Israel time, the last bar is Tuesday's close
    assert clock.session_label(TUE, at("2026-11-04T11:40"))["title"] == "Last session, Tue 3 Nov (close)"
    assert clock.session_label(WED, at("2026-11-04T15:00"))["title"] == "Today, Wed 4 Nov (live)"
    assert clock.session_label(WED, at("2026-11-04T22:00"))["title"] == "Today, Wed 4 Nov (close)"
    # after 20:00 New York time it is already the next day in Israel: the last session, not today
    assert clock.session_label(WED, at("2026-11-05T02:00"))["word"] == "Last session"


def test_endpoint_and_summary(session, fake):
    from tests.conftest import frame
    days = [dt.date.today() - dt.timedelta(days=9 - i) for i in range(10)]
    for t in ("SPY", "QQQ", "AAPL"):
        fake.frames[t] = frame([(d.isoformat(), 100 + i) for i, d in enumerate(days)])
    fake.frames["ILS=X"] = frame([(d.isoformat(), 3.7) for d in days])
    c = TestClient(app)
    c.post("/api/transactions?portfolio=1", json={"ticker": "AAPL", "type": "BUY", "date": days[2].isoformat(), "quantity": 1, "price": 102, "currency": "USD"})
    st = c.get("/api/market/status").json()
    assert st["label"] in ("Pre-market", "Open", "After hours", "Closed") and st["as_of"] and st["day_word"] in ("Today", "Last session")
    s = c.get("/api/portfolio?portfolio=1&range=MAX").json()["summary"]
    assert s["today_word"] in ("Today", "Last session") and s["today_label"].startswith(s["today_word"] + ", ")
    assert "·" not in s["today_label"]
