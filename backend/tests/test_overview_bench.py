import datetime as dt

from app import services
from app.models import Portfolio, Transaction
from app.portfolios import overview_payload
from tests.conftest import frame

SPY = {"2026-10-29": 750.0, "2026-10-30": 755.72, "2026-11-02": 752.1, "2026-11-03": 754.35, "2026-11-04": 766.81,
       "2026-11-05": 733.0, "2026-11-06": 735.29, "2026-11-09": 752.69, "2026-11-10": 749.92, "2026-11-11": 759.81,
       "2026-11-12": 757.12, "2026-11-13": 754.37, "2026-11-16": 739.95, "2026-11-17": 732.44, "2026-11-18": 730.2,
       "2026-11-19": 715.87, "2026-11-20": 689.81}


def test_spy_line_matches_prices_every_day(session, fake):
    services.bump_revision()
    us = sorted(SPY)
    ta = [d.isoformat() for d in (dt.date(2026, 10, 29) + dt.timedelta(days=i) for i in range(25)) if d.weekday() in (6, 0, 1, 2, 3)]
    for t in ("SPY", "QQQ", "SPMO", "AAPL"):
        fake.frames[t] = frame([(d, SPY[d]) for d in us])
    fake.frames["TEVA.TA"] = frame([(d, 5000 + i) for i, d in enumerate(ta)])
    fake.frames["ILS=X"] = frame([(d, 3.6 + i * 0.01) for i, d in enumerate(sorted(set(us) | set(ta)))])
    session.add(Portfolio(id=2, name="A"))
    session.add(Portfolio(id=3, name="B"))
    session.add(Transaction(portfolio_id=2, date=dt.date(2026, 10, 30), type="BUY", ticker="AAPL", quantity=1, price=755.72, currency="USD"))
    session.add(Transaction(portfolio_id=3, date=dt.date(2026, 11, 2), type="BUY", ticker="TEVA.TA", quantity=1, price=50, currency="ILS"))
    session.commit()
    o = overview_payload(session, "MAX")
    dates, spy = o["chart"]["dates"], o["chart"]["perf"]["SPY"]
    last = None
    for d, v in zip(dates, spy):
        last = SPY.get(d, last)
        if v is not None and last:
            assert abs(v - last / 755.72 * 100) < 0.01, d


def test_adjusted_price_is_rebuilt_from_closes_and_dividends():
    import pandas as pd
    from app.market.cache import adjusted_from_dividends

    idx = [dt.date(2026, 12, d) for d in (17, 18, 19, 22)]
    close = pd.Series([100.0, 101.0, 99.0, 100.0], index=idx)
    div = pd.Series([0.0, 0.0, 2.02, 0.0], index=idx)  # ex-dividend on the 19th, 2% of the previous close
    adj = adjusted_from_dividends(close, div)
    assert adj.iloc[-1] == 100.0 and adj.iloc[2] == 99.0
    assert abs(adj.iloc[1] - 101.0 * 0.98) < 1e-9 and abs(adj.iloc[0] - 100.0 * 0.98) < 1e-9
    # no dividends: the adjusted price is the close, whatever was stored
    assert (adjusted_from_dividends(close, div * 0) == close).all()
