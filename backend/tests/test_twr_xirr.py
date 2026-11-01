import datetime as dt

import pandas as pd

from app.engine.returns import range_xirr, shadow, twr, xirr
from app.engine.series import MarketData, Tx, build_series
from tests.conftest import frame

D = [dt.date(2024, 1, d) for d in (2, 3, 4)]


def _two_deposit_case():
    md = MarketData(frame([("2024-01-01", 100), ("2024-01-02", 100), ("2024-01-03", 110), ("2024-01-04", 99)]), "USD")
    fx = pd.Series([1.0], index=[dt.date(2024, 1, 1)])
    txs = [
        Tx("", "DEPOSIT", D[0], 0, 1000, "USD"), Tx("X", "BUY", D[0], 10, 100, "USD"),
        Tx("", "DEPOSIT", D[1], 0, 1100, "USD"), Tx("X", "BUY", D[1], 10, 110, "USD"),
    ]
    return build_series(txs, {"X": md}, fx, "USD")


def test_twr_ignores_deposits():
    s = _two_deposit_case()
    # day returns: +10%, then -10%. Deposits must not distort it.
    assert abs(s.ret.loc[D[1]] - 0.10) < 1e-12
    assert abs(s.ret.loc[D[2]] + 0.10) < 1e-12
    assert abs(twr(s.ret, 0, len(s.dates) - 1) - (1.1 * 0.9 - 1)) < 1e-12  # -1.0%
    assert s.value.iloc[-1] == 1980.0 and s.invested.iloc[-1] == 2100.0


def test_xirr_simple_year():
    r = xirr([(dt.date(2021, 1, 1), -1000), (dt.date(2022, 1, 1), 1100)])
    assert abs(r - 0.10) < 1e-9


def test_xirr_excel_reference():
    # Example from the Excel XIRR documentation: 0.373362535
    flows = [(dt.date(2008, 1, 1), -10000), (dt.date(2008, 3, 1), 2750), (dt.date(2008, 10, 30), 4250),
             (dt.date(2009, 2, 15), 3250), (dt.date(2009, 4, 1), 2750)]
    assert abs(xirr(flows) - 0.373362535) < 1e-6


def test_xirr_undefined_without_sign_change():
    assert xirr([(dt.date(2021, 1, 1), -1000)]) is None


def test_range_xirr_uses_flows():
    s = _two_deposit_case()
    r = range_xirr(s.value, s.flow, 0, len(s.dates) - 1)
    # -1000 on Jan 2, -1100 on Jan 3, +1980 on Jan 4: a loss, money-weighted
    assert r is not None and r < 0


def test_shadow_replays_flows():
    idx = [dt.date(2024, 1, d) for d in (1, 2, 3, 4)]
    value = pd.Series([0, 1000, 1000, 2000.0], index=idx)
    flow = pd.Series([0, 1000, 0, 1000.0], index=idx)
    price = pd.Series([100, 100, 125, 150.0], index=idx)
    # Jan 2: 1000/100 = 10 units. Jan 4: 1000/150 = 6.667 units. Final 16.667 * 150 = 2500
    out = shadow(value, flow, price, 0, 3)
    assert abs(out.iloc[-1] - 2500.0) < 1e-9
    price2 = pd.Series([100, 100, 125, 125.0], index=idx)
    flow2 = pd.Series([0, 1000, 1000, 0.0], index=idx)
    # 10 units + 8 units = 18 units * 125 = 2250
    assert abs(shadow(value, flow2, price2, 0, 3).iloc[-1] - 2250.0) < 1e-9


def test_auto_dividends_count_toward_return():
    md = MarketData(frame([("2024-01-01", 100), ("2024-01-02", 100), ("2024-01-03", 100, None, 2.0)]), "USD")
    fx = pd.Series([1.0], index=[dt.date(2024, 1, 1)])
    s = build_series([Tx("X", "BUY", D[0], 10, 100, "USD")], {"X": md}, fx, "USD")
    assert s.cash.iloc[-1] == 20.0
    assert abs(s.ret.iloc[-1] - 0.02) < 1e-12


def test_recorded_dividend_replaces_auto():
    md = MarketData(frame([("2024-01-01", 100), ("2024-01-02", 100), ("2024-01-03", 100, None, 2.0)]), "USD")
    fx = pd.Series([1.0], index=[dt.date(2024, 1, 1)])
    txs = [Tx("X", "BUY", D[0], 10, 100, "USD"), Tx("X", "DIVIDEND", D[1], 0, 17.0, "USD")]  # after 15% tax
    s = build_series(txs, {"X": md}, fx, "USD")
    assert s.cash.iloc[-1] == 17.0


def test_contributions_sum_to_daily_return():
    s = _two_deposit_case()
    total = s.contribution.sum(axis=1)
    assert ((total - s.ret).abs() < 1e-12).all()
