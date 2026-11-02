import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import services
from app.main import app
from app.metrics import weighted_valuation
from tests.conftest import frame

TODAY = dt.date.today()


class InfoFake:
    pass


@pytest.fixture
def client(session, fake):
    services.bump_revision()
    ds = [TODAY - dt.timedelta(days=499 - i) for i in range(500)]
    import math
    spy = [100 * (1 + 0.01 * math.sin(i / 3) + 0.0005 * i) for i in range(500)]
    # LEV moves exactly 2x SPY's daily return -> beta 2
    lev = [100.0]
    for i in range(1, 500):
        lev.append(lev[-1] * (1 + 2 * (spy[i] / spy[i - 1] - 1)))
    fake.frames["ILS=X"] = frame([(d.isoformat(), 3.7) for d in ds])
    fake.frames["SPY"] = frame([(d.isoformat(), v) for d, v in zip(ds, spy)])
    fake.frames["LEV"] = frame([(d.isoformat(), v) for d, v in zip(ds, lev)])
    fake.frames["QQQ"] = frame([(d.isoformat(), v) for d, v in zip(ds, spy)])
    infos = {"SPY": {"trailingPE": 25.0}, "LEV": {"trailingPE": 50.0, "forwardPE": 40.0}, "QQQ": {"trailingPE": 20.0},
             "AAA": {"trailingPE": 10.0}, "BBB": {"trailingPE": 40.0}, "CCC": {"trailingPE": -5.0}}
    fake.info = lambda t: infos.get(t, {})
    return TestClient(app)


def test_harmonic_pe_by_hand(client, session):
    # 50% at P/E 10 and 50% at P/E 40: earnings yield 0.5/10 + 0.5/40 = 0.0625 -> P/E 16 (not the average 25)
    v = weighted_valuation(session, {"AAA": 0.5, "BBB": 0.5})
    assert abs(v["trailing_pe"] - 16.0) < 1e-9
    # negative earnings are left out and reported
    v = weighted_valuation(session, {"AAA": 0.5, "CCC": 0.5})
    assert abs(v["trailing_pe"] - 10.0) < 1e-9 and v["trailing_pe_missing"] == ["CCC"] and abs(v["trailing_pe_coverage"] - 0.5) < 1e-12


def test_beta_and_pe_rows(client):
    client.post("/api/transactions?portfolio=1", json={"ticker": "LEV", "type": "BUY", "date": (TODAY - dt.timedelta(days=400)).isoformat(),
                                                       "quantity": 10, "price": 100, "currency": "USD"})
    r = client.get("/api/metrics?portfolios=1&symbols=SPY,LEV&range=1Y")
    assert r.status_code == 200, r.text
    m = r.json()
    rows = {r["key"]: r for r in m["rows"]}
    assert abs(rows["SPY"]["beta"] - 1.0) < 1e-9
    assert abs(rows["LEV"]["beta"] - 2.0) < 1e-6
    assert abs(rows["p1"]["beta"] - 2.0) < 1e-6  # a portfolio holding only LEV
    assert rows["p1"]["trailing_pe"] == 50.0 and rows["SPY"]["trailing_pe"] == 25.0
    rb = [x for x in m["rolling"]["series"]["LEV"] if x is not None]
    assert rb and all(abs(x - 2.0) < 1e-6 for x in rb)
