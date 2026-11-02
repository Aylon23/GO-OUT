"""Stock performance only: a portfolio's return never includes a currency move."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import services
from app.main import app
from tests.conftest import frame

TODAY = dt.date.today()


def days(n):
    start = TODAY - dt.timedelta(days=n - 1)
    return [start + dt.timedelta(days=i) for i in range(n)]


@pytest.mark.parametrize("display", ["USD", "ILS"])
def test_spy_only_portfolio_has_exactly_spys_dollar_return(session, fake, display):
    services.bump_revision()
    ds = days(120)
    n = len(ds)
    spy = {d: 400 * (1 + 0.25 * i / (n - 1)) + (3 if i % 7 == 0 else 0) for i, d in enumerate(ds)}
    # USD/ILS swings hard: 3.2 -> 4.1 with zig-zags. None of it may reach any return.
    fx = {d: 3.2 + 0.9 * i / (n - 1) + (0.15 if i % 3 == 0 else -0.1) for i, d in enumerate(ds)}
    fake.frames["SPY"] = frame([(d.isoformat(), spy[d]) for d in ds])
    fake.frames["QQQ"] = frame([(d.isoformat(), 300.0) for d in ds])
    fake.frames["ILS=X"] = frame([(d.isoformat(), fx[d]) for d in ds])
    c = TestClient(app)
    c.put("/api/settings", json={"base_currency": display})
    c.post("/api/transactions", json={"ticker": "SPY", "type": "BUY", "date": ds[5].isoformat(), "quantity": 10,
                                      "price": spy[ds[5]], "currency": "USD"})
    expected = spy[ds[-1]] / spy[ds[5]] - 1

    pf = c.get("/api/portfolio?portfolio=1&range=MAX").json()["summary"]
    assert pf["twr"] == pytest.approx(expected, rel=1e-9)
    bm = {b["symbol"]: b["twr"] for b in pf["benchmarks"]}
    assert bm["SPY"] == pytest.approx(expected, rel=1e-9)  # same basis: apples to apples

    # "today's holdings over the range" starts at the first day of the range
    lb = c.get("/api/lookback?portfolio=1&range=MAX").json()
    lb_bm = {b["symbol"]: b["twr"] for b in lb["summary"]["benchmarks"]}
    assert lb["summary"]["return"] == pytest.approx(lb_bm["SPY"], rel=1e-9)
    assert lb["summary"]["return"] == pytest.approx(spy[ds[-1]] / spy[ds[0]] - 1, rel=1e-9)

    o = c.get("/api/overview?range=MAX").json()
    me = next(r for r in o["rows"] if r["name"] == "My portfolio")
    assert me["since_return"] == pytest.approx(expected, rel=1e-9)
    assert me["since_vs_spy"] == pytest.approx(0, abs=1e-9)

    # only the displayed amount changes with the setting, at today's rate
    rate = fx[ds[-1]] if display == "ILS" else 1.0
    assert lb["summary"]["end_value"] == pytest.approx(10 * spy[ds[-1]] * rate, rel=1e-9)
