import datetime as dt

import pandas as pd

from app.engine.series import MarketData, Tx, build_series
from tests.conftest import frame

D0, D1, D2 = dt.date(2024, 1, 2), dt.date(2024, 1, 3), dt.date(2024, 1, 4)


def test_conversion_uses_that_days_rate():
    md = MarketData(frame([("2024-01-02", 100), ("2024-01-03", 100), ("2024-01-04", 110)]), "USD")
    fx = pd.Series([3.5, 3.7, 3.6], index=[D0, D1, D2])
    txs = [
        Tx("AAPL", "BUY", D0, 10, 100.0, "USD"),  # 1000 USD at 3.5 -> 3500 ILS implicit deposit
        Tx("", "DEPOSIT", D1, 0, 100.0, "USD"),  # 100 USD at 3.7 -> 370 ILS
    ]
    s = build_series(txs, {"AAPL": md}, fx, "ILS")
    assert s.flow.loc[D0] == 3500.0
    assert abs(s.flow.loc[D1] - 370.0) < 1e-9
    # D1: stock 10*100*3.7 = 3700, cash 370 -> 4070
    assert abs(s.value.loc[D1] - 4070.0) < 1e-9
    # FX move alone is a return for an ILS investor: (4070 - 370 - 3500) / 3500
    assert abs(s.ret.loc[D1] - 200 / 3500) < 1e-12
    # D2: stock 10*110*3.6 = 3960, cash 370 at D1 rate stays 370 ILS -> 4330
    assert abs(s.value.loc[D2] - 4330.0) < 1e-9


def test_usd_base_converts_ils_holdings():
    md = MarketData(frame([("2024-01-02", 50), ("2024-01-03", 50)]), "ILS")
    fx = pd.Series([4.0, 5.0], index=[D0, D1])
    s = build_series([Tx("LUMI.TA", "BUY", D0, 100, 50.0, "ILS")], {"LUMI.TA": md}, fx, "USD")
    assert s.value.loc[D0] == 1250.0  # 5000 ILS / 4.0
    assert s.value.loc[D1] == 1000.0  # 5000 ILS / 5.0


def test_calendar_forward_fills_across_exchanges():
    # TASE trades on Sunday 2024-01-07, US does not. US price must be carried forward.
    us = MarketData(frame([("2024-01-05", 100), ("2024-01-08", 102)]), "USD")
    ta = MarketData(frame([("2024-01-05", 10), ("2024-01-07", 11), ("2024-01-08", 11)]), "ILS")
    fx = pd.Series([3.6], index=[dt.date(2024, 1, 5)])
    txs = [Tx("AAPL", "BUY", dt.date(2024, 1, 5), 1, 100, "USD"), Tx("X.TA", "BUY", dt.date(2024, 1, 5), 10, 10, "ILS")]
    s = build_series(txs, {"AAPL": us, "X.TA": ta}, fx, "ILS")
    sunday = dt.date(2024, 1, 7)
    assert sunday in s.dates
    assert s.holding_value.loc[sunday, "AAPL"] == 360.0
    assert s.holding_value.loc[sunday, "X.TA"] == 110.0
