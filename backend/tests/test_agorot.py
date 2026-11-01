import datetime as dt

import pandas as pd

from app.engine.series import MarketData, Tx, build_series
from app.market.cache import is_agorot, load_history, normalize_units, price_on
from tests.conftest import frame


def test_is_agorot():
    assert is_agorot("TEVA.TA")
    assert is_agorot("teva.ta")
    assert not is_agorot("TEVA")
    assert not is_agorot("^TA125.TA")  # index points, not agorot


def test_normalize_divides_by_100():
    f = frame([("2024-01-01", 5000, 4900, 50)])
    out = normalize_units(f, "TEVA.TA")
    assert out["close"].iloc[0] == 50.0
    assert out["adj_close"].iloc[0] == 49.0
    assert out["dividend"].iloc[0] == 0.5
    assert normalize_units(f, "AAPL")["close"].iloc[0] == 5000


def test_cache_stores_shekels_and_price_on_date(session, fake):
    fake.frames["TEVA.TA"] = frame([("2024-01-01", 5000), ("2024-01-02", 5200)])
    df = load_history(session, "TEVA.TA")
    assert list(df["close"]) == [50.0, 52.0]
    p = price_on(session, "TEVA.TA", dt.date(2024, 1, 5))  # nearest prior trading day
    assert p["close"] == 52.0 and p["date"] == "2024-01-02" and p["currency"] == "ILS"


def test_value_of_ta_holding_in_shekels():
    # 100 shares bought at 50 ILS (Yahoo quoted 5000 agorot); next day 52 ILS
    md = MarketData(normalize_units(frame([("2024-01-01", 5000), ("2024-01-02", 5200)]), "TEVA.TA"), "ILS")
    fx = pd.Series([3.7, 3.7], index=[dt.date(2024, 1, 1), dt.date(2024, 1, 2)])
    s = build_series([Tx("TEVA.TA", "BUY", dt.date(2024, 1, 1), 100, 50.0, "ILS")], {"TEVA.TA": md}, fx, "ILS")
    assert s.value.iloc[-1] == 5200.0
    assert abs(s.ret.iloc[-1] - 0.04) < 1e-12
