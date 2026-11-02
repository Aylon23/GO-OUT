import datetime as dt

import pandas as pd
import pytest

from app.engine.series import MarketData, Tx, build_series
from tests.conftest import frame

D0, D1, D2 = dt.date(2024, 1, 2), dt.date(2024, 1, 3), dt.date(2024, 1, 4)


def test_conversion_uses_one_rate_so_currency_moves_never_count():
    """Stock performance only: every amount is converted at today's (latest) rate, so a USD/ILS move
    is never a gain or a loss."""
    md = MarketData(frame([("2024-01-02", 100), ("2024-01-03", 100), ("2024-01-04", 110)]), "USD")
    fx = pd.Series([3.5, 3.7, 3.6], index=[D0, D1, D2])  # today's (latest) rate is 3.6
    txs = [
        Tx("AAPL", "BUY", D0, 10, 100.0, "USD"),  # 1000 USD -> 3600 ILS implicit deposit
        Tx("", "DEPOSIT", D1, 0, 100.0, "USD"),  # 100 USD -> 360 ILS
    ]
    s = build_series(txs, {"AAPL": md}, fx, "ILS")
    assert s.flow.loc[D0] == pytest.approx(3600.0)
    assert s.flow.loc[D1] == pytest.approx(360.0)
    assert s.value.loc[D1] == pytest.approx(3600.0 + 360.0)
    assert s.ret.loc[D1] == pytest.approx(0.0)  # the dollar rose 5.7% against the shekel: not a return
    # D2: the stock rose 10% in dollars; the shekel figure rises 10% on the stock part only
    assert s.value.loc[D2] == pytest.approx(10 * 110 * 3.6 + 360.0)
    assert s.ret.loc[D2] == pytest.approx((10 * 110 * 3.6 + 360) / (3600 + 360) - 1)


def test_usd_display_converts_ils_holdings_at_one_rate():
    md = MarketData(frame([("2024-01-02", 50), ("2024-01-03", 50)]), "ILS")
    fx = pd.Series([4.0, 5.0], index=[D0, D1])
    s = build_series([Tx("LUMI.TA", "BUY", D0, 100, 50.0, "ILS")], {"LUMI.TA": md}, fx, "USD")
    assert s.value.loc[D0] == pytest.approx(1000.0)  # 5000 ILS / 5.0 (today's rate)
    assert s.value.loc[D1] == pytest.approx(1000.0)  # the shekel weakened 25%: no loss, the stock didn't move
    assert s.ret.loc[D1] == pytest.approx(0.0)


def test_calendar_forward_fills_across_exchanges():
    # TASE trades on Sunday 2024-01-07, US does not. US price must be carried forward.
    us = MarketData(frame([("2024-01-05", 100), ("2024-01-08", 102)]), "USD")
    ta = MarketData(frame([("2024-01-05", 10), ("2024-01-07", 11), ("2024-01-08", 11)]), "ILS")
    fx = pd.Series([3.6], index=[dt.date(2024, 1, 5)])
    txs = [Tx("AAPL", "BUY", dt.date(2024, 1, 5), 1, 100, "USD"), Tx("X.TA", "BUY", dt.date(2024, 1, 5), 10, 10, "ILS")]
    s = build_series(txs, {"AAPL": us, "X.TA": ta}, fx, "ILS")
    sunday = dt.date(2024, 1, 7)
    assert sunday in s.dates
    assert s.holding_value.loc[sunday, "AAPL"] == 360.0  # 100 USD at the (single) rate 3.6
    assert s.holding_value.loc[sunday, "X.TA"] == 110.0
