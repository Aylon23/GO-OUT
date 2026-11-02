import datetime as dt

import pytest

from app.models import Portfolio
from app.omnibox import parse
from tests.conftest import frame

TODAY = dt.date(2026, 11, 2)


@pytest.fixture
def ctx(session, fake):
    ds = [TODAY - dt.timedelta(days=10 - i) for i in range(11)]
    for t, px in {"QQQ": 500, "SPY": 600, "AAPL": 200, "ILS=X": 3.7}.items():
        fake.frames[t] = frame([(d.isoformat(), px) for d in ds])
    fake.frames["TEVA.TA"] = frame([(d.isoformat(), 5000) for d in ds])
    fake.quotes = [{"symbol": "AAPL", "shortname": "Apple Inc.", "exchange": "NMS", "quoteType": "EQUITY"}]
    session.add(Portfolio(id=2, name="אבא"))
    session.add(Portfolio(id=3, name="Mom"))
    session.commit()
    return session


def p(ctx, text, pid=1):
    return parse(ctx, text, pid, today=TODAY)


def test_hebrew_buy_shares(ctx):
    d = p(ctx, "קניתי 5 מניות של QQQ")
    assert (d["type"], d["ticker"], d["quantity"], d["price"], d["portfolio_id"]) == ("BUY", "QQQ", 5, 500, 1)
    assert d["price_source"] == "close" and d["missing"] == []


def test_portfolio_by_name_and_amount(ctx):
    d = p(ctx, "אבא קנה SPY ב-1200 דולר")
    assert d["portfolio_id"] == 2 and d["ticker"] == "SPY" and d["quantity"] == 2.0  # $1,200 / $600


def test_english_sell_with_price_and_date(ctx):
    d = p(ctx, "Mom sold 3 AAPL at 190 yesterday")
    assert (d["portfolio_id"], d["type"], d["quantity"], d["price"], d["price_source"]) == (3, "SELL", 3, 190, "you")
    assert d["date"] == "2026-11-01"


def test_hebrew_name_and_explicit_date(ctx):
    d = p(ctx, "קניתי 100 טבע ב-48 ב 28/10/2026")
    assert d["ticker"] == "TEVA.TA" and d["quantity"] == 100 and d["price"] == 48 and d["date"] == "2026-10-28"


def test_lowercase_company_name_goes_to_search(ctx):
    d = p(ctx, "bought 2 apple")
    assert d["ticker"] == "AAPL" and d["quantity"] == 2 and d["warnings"]


def test_deposit_and_dividend(ctx):
    d = p(ctx, "הפקדתי 5000 שקל לתיק של אבא")
    assert (d["type"], d["price"], d["currency"], d["portfolio_id"]) == ("DEPOSIT", 5000, "ILS", 2)
    d = p(ctx, "קיבלתי דיבידנד 12.5$ מ-AAPL")
    assert (d["type"], d["ticker"], d["price"], d["currency"]) == ("DIVIDEND", "AAPL", 12.5, "USD")


def test_missing_quantity_is_reported(ctx):
    d = p(ctx, "קניתי QQQ")
    assert "quantity" in d["missing"]
