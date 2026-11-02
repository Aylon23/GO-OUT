import datetime as dt

import pandas as pd

from app.engine.series import MarketData, Tx, build_series, holding_lots
from app.market.cache import load_history, load_splits, price_on
from tests.conftest import frame

SPLIT_DAY = dt.date(2024, 1, 4)


def test_split_keeps_quantity_and_value_continuous():
    # Yahoo closes are split-adjusted: pre-split 400 shows as 100.
    md = MarketData(frame([("2024-01-02", 100), ("2024-01-03", 105), ("2024-01-04", 105), ("2024-01-05", 110)]),
                    "USD", splits=[(SPLIT_DAY, 4.0)])
    fx = pd.Series([3.5], index=[dt.date(2024, 1, 2)])
    txs = [Tx("NVDA", "BUY", dt.date(2024, 1, 2), 10, 400.0, "USD"),  # as traded: 10 @ 400
           Tx("NVDA", "BUY", dt.date(2024, 1, 5), 5, 110.0, "USD")]   # after split
    s = build_series(txs, {"NVDA": md}, fx, "USD")
    assert s.holding_qty.loc[dt.date(2024, 1, 2), "NVDA"] == 40.0
    assert s.value.loc[dt.date(2024, 1, 2)] == 4000.0  # 10 * 400 == 40 * 100
    assert s.value.loc[dt.date(2024, 1, 4)] == 4200.0  # no jump on split day
    assert s.holding_qty.iloc[-1]["NVDA"] == 45.0
    assert abs(s.ret.loc[dt.date(2024, 1, 4)]) < 1e-12
    lots = holding_lots(txs, "NVDA", md.splits, fx, "USD")
    assert lots["quantity"] == 45.0
    assert abs(lots["avg_cost"] - (4000 + 550) / 45) < 1e-9


def test_sell_after_split_uses_post_split_shares():
    md = MarketData(frame([("2024-01-02", 100), ("2024-01-04", 100)]), "USD", splits=[(SPLIT_DAY, 4.0)])
    fx = pd.Series([1.0], index=[dt.date(2024, 1, 2)])
    txs = [Tx("NVDA", "BUY", dt.date(2024, 1, 2), 10, 400.0, "USD"), Tx("NVDA", "SELL", dt.date(2024, 1, 4), 20, 100.0, "USD")]
    s = build_series(txs, {"NVDA": md}, fx, "USD")
    assert s.holding_qty.iloc[-1]["NVDA"] == 20.0


def test_cache_rescales_old_rows_when_new_split_arrives(session, fake):
    fake.frames["ABC"] = frame([("2024-01-02", 400, 400), ("2024-01-03", 420, 420)])
    load_history(session, "ABC")
    # later Yahoo returns the overlap day split-adjusted plus the split day
    fake.frames["ABC"] = frame([("2024-01-03", 105, 105), ("2024-01-04", 106, 106, 0, 4)])
    from sqlmodel import select
    from app.models import PriceCoverage
    cov = session.exec(select(PriceCoverage)).one()
    cov.checked_at = cov.checked_at - dt.timedelta(days=2)
    session.add(cov); session.commit()
    df = load_history(session, "ABC")
    assert list(df["close"]) == [100.0, 105.0, 106.0]
    assert load_splits(session, "ABC") == [(dt.date(2024, 1, 4), 4.0)]
    # the second time only recent bars were requested: from a week before the last cached day
    assert fake.calls[-1] == ("ABC", dt.date(2024, 1, 3) - dt.timedelta(days=7))
    # price on the first day is reported as traded (pre-split)
    assert price_on(session, "ABC", dt.date(2024, 1, 2))["close"] == 400.0
