import datetime as dt

import pandas as pd

from app.engine.risk import beta_corr, cagr, calendar_year_returns, max_drawdown, monthly_returns, sharpe


def idx(values, start=dt.date(2024, 1, 1)):
    return pd.Series(values, index=[start + dt.timedelta(days=i) for i in range(len(values))], dtype=float)


def test_max_drawdown_with_dates():
    s = idx([100, 120, 90, 110, 130, 117])
    dd = max_drawdown(s)
    assert abs(dd["value"] + 0.25) < 1e-12  # 90 / 120 - 1
    assert dd["peak"] == "2024-01-02" and dd["trough"] == "2024-01-03" and dd["recovery"] == "2024-01-05"


def test_max_drawdown_not_recovered():
    dd = max_drawdown(idx([100, 80, 90]))
    assert abs(dd["value"] + 0.2) < 1e-12 and dd["recovery"] is None


def _from_returns(rs):
    v = [100.0]
    for r in rs:
        v.append(v[-1] * (1 + r))
    return idx(v)


def test_beta_hand_computed():
    # rb = [1%, 2%, -1%], rp = [2%, 1%, 0%]  -> cov = 0.0001, var(rb) = 0.000233.. -> beta = 3/7
    b, c = beta_corr(_from_returns([0.02, 0.01, 0.0]), _from_returns([0.01, 0.02, -0.01]))
    assert abs(b - 3 / 7) < 1e-9
    # corr = 0.0001 / sqrt(0.0002333 * 0.0001)
    assert abs(c - 0.0001 / (0.00023333333333 * 0.0001) ** 0.5) < 1e-6


def test_beta_two_times_market():
    b, c = beta_corr(_from_returns([0.02, -0.04, 0.06, 0.01]), _from_returns([0.01, -0.02, 0.03, 0.005]))
    assert abs(b - 2.0) < 1e-9 and abs(c - 1.0) < 1e-9


def test_cagr():
    s = pd.Series([100.0, 121.0], index=[dt.date(2020, 1, 1), dt.date(2022, 1, 1)])
    assert abs(cagr(s) - (1.21 ** (365.25 / 731) - 1)) < 1e-12
    short = pd.Series([100.0, 105.0], index=[dt.date(2024, 1, 1), dt.date(2024, 6, 1)])
    assert abs(cagr(short) - 0.05) < 1e-12  # under a year: not annualized


def test_calendar_and_monthly_returns():
    s = pd.Series([100.0, 110.0, 99.0, 108.9],
                  index=[dt.date(2022, 12, 30), dt.date(2023, 6, 30), dt.date(2023, 12, 29), dt.date(2024, 1, 31)])
    years = calendar_year_returns(s)
    assert abs(years[2023] + 0.01) < 1e-12  # 99 / 100 - 1
    assert abs(years[2024] - 0.10) < 1e-12
    months = monthly_returns(s)
    assert abs(months["2024-01"] - 0.10) < 1e-12
    assert abs(months["2023-06"] - 0.10) < 1e-12


def test_sharpe_zero_vol_is_none():
    assert sharpe(idx([100, 100, 100]), 0.04) is None
