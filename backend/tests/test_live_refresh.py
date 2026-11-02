"""A page load shows the latest prices, not the bar saved at the open."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import services
from app.main import app
from app.market import cache
from tests.conftest import frame

TODAY = dt.date.today()
DAYS = [TODAY - dt.timedelta(days=9 - i) for i in range(10)]


def setup(fake, today_close):
    services.bump_revision()
    fake.frames["SPY"] = frame([(d.isoformat(), 100.0 if d < TODAY else today_close) for d in DAYS])
    fake.frames["QQQ"] = frame([(d.isoformat(), 200.0) for d in DAYS])
    fake.frames["ILS=X"] = frame([(d.isoformat(), 3.7) for d in DAYS])


def value(c):
    return c.get("/api/portfolio?portfolio=1&range=MAX").json()["summary"]["value"]


def test_refresh_replaces_todays_bar_saved_at_the_open(session, fake, monkeypatch):
    setup(fake, 100.0)  # the open
    c = TestClient(app)
    c.put("/api/settings", json={"base_currency": "USD"})
    c.post("/api/transactions", json={"ticker": "SPY", "type": "BUY", "date": DAYS[2].isoformat(), "quantity": 10,
                                      "price": 100.0, "currency": "USD"})
    assert value(c) == pytest.approx(1000.0)

    fake.frames["SPY"] = frame([(d.isoformat(), 100.0 if d < TODAY else 110.0) for d in DAYS])  # later that day
    assert value(c) == pytest.approx(1000.0)  # within a minute: the same fetch serves every request of one page load
    monkeypatch.setattr(cache, "LIVE_AFTER", dt.timedelta(0))  # a minute later: the next page load
    assert value(c) == pytest.approx(1100.0)
    assert c.get("/api/portfolio?portfolio=1&range=1M").json()["summary"]["twr"] == pytest.approx(0.10)


class BatchProvider:
    """Wraps the fake provider and adds the batched latest-bars call."""

    def __init__(self, fake):
        self.fake, self.batches = fake, []

    def __getattr__(self, name):
        return getattr(self.fake, name)

    def recent_bars(self, tickers):
        self.batches.append(list(tickers))
        start = TODAY - dt.timedelta(days=6)
        return {t: self.fake.frames[t][self.fake.frames[t].index >= start] for t in tickers if t in self.fake.frames}


def test_one_batched_request_for_all_tickers(session, fake, monkeypatch):
    from app.market import provider

    setup(fake, 100.0)
    c = TestClient(app)
    c.post("/api/transactions", json={"ticker": "SPY", "type": "BUY", "date": DAYS[2].isoformat(), "quantity": 10,
                                      "price": 100.0, "currency": "USD"})
    c.post("/api/transactions", json={"ticker": "QQQ", "type": "BUY", "date": DAYS[2].isoformat(), "quantity": 1,
                                      "price": 200.0, "currency": "USD"})
    value(c)
    bp = BatchProvider(fake)
    provider.set_provider(bp)
    fake.calls.clear()
    fake.frames["SPY"] = frame([(d.isoformat(), 100.0 if d < TODAY else 120.0) for d in DAYS])
    monkeypatch.setattr(cache, "LIVE_AFTER", dt.timedelta(0))
    assert value(c) == pytest.approx(10 * 120.0 + 200.0)
    assert sorted(bp.batches[0]) == ["ILS=X", "QQQ", "SPY"]  # one request covers holdings, benchmarks and FX
    assert fake.calls == []  # no per-ticker history fetches


def test_intraday_moves_never_rescale_history(session, fake, monkeypatch):
    """Today's partial bar keeps changing; the adj_close splice must anchor on a completed day."""
    fake.frames["XYZ"] = frame([(d.isoformat(), 50.0, 49.0) for d in DAYS[:-1]] + [(TODAY.isoformat(), 50.0, 50.0)])
    old = cache.load_history(session, "XYZ")["adj_close"].copy()
    fake.frames["XYZ"] = frame([(d.isoformat(), 50.0, 49.0) for d in DAYS[:-1]] + [(TODAY.isoformat(), 55.0, 55.0)])
    monkeypatch.setattr(cache, "LIVE_AFTER", dt.timedelta(0))
    cache.refresh_latest(session, ["XYZ"])
    new = cache.load_history(session, "XYZ")["adj_close"]
    assert list(new.iloc[:-1]) == list(old.iloc[:-1])  # history untouched
    assert new.iloc[-1] == 55.0


def test_split_seen_at_the_open_is_not_applied_twice(session, fake, monkeypatch):
    rows = [(d.isoformat(), 400.0, 400.0) for d in DAYS[:-1]]
    fake.frames["ABC"] = frame(rows)
    cache.load_history(session, "ABC")
    # the open: a 4-for-1 split today
    fake.frames["ABC"] = frame([(d, 100.0, 100.0) for d, *_ in rows] + [(TODAY.isoformat(), 101.0, 101.0, 0, 4)])
    monkeypatch.setattr(cache, "LIVE_AFTER", dt.timedelta(0))
    cache.refresh_latest(session, ["ABC"])
    # later the same day: same split, new price
    fake.frames["ABC"] = frame([(d, 100.0, 100.0) for d, *_ in rows] + [(TODAY.isoformat(), 102.0, 102.0, 0, 4)])
    cache.refresh_latest(session, ["ABC"])
    df = cache.load_history(session, "ABC")
    assert list(df["close"]) == [100.0] * (len(DAYS) - 1) + [102.0]
