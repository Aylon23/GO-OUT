"""Optional demo portfolio: AAPL, MSFT, NVDA, VOO, TEVA.TA bought on different dates, with deposits.
Quantities are derived from each day's real close so the numbers are realistic."""
from __future__ import annotations

import datetime as dt
import math

from sqlmodel import Session

from .market.cache import price_on
from .market.search import remember_selection
from .models import Transaction, TxType

PLAN = [
    ("2021-03-01", "DEPOSIT", "", 60000, "ILS"),
    ("2021-03-02", "BUY", "VOO", 7000, None),
    ("2021-03-02", "BUY", "AAPL", 4000, None),
    ("2021-06-15", "BUY", "MSFT", 4000, None),
    ("2022-01-10", "DEPOSIT", "", 30000, "ILS"),
    ("2022-01-10", "BUY", "TEVA.TA", 12000, None),
    ("2022-01-11", "BUY", "NVDA", 4000, None),
    ("2023-03-01", "DEPOSIT", "", 25000, "ILS"),
    ("2023-03-01", "BUY", "VOO", 6000, None),
    ("2024-02-01", "DEPOSIT", "", 20000, "ILS"),
    ("2024-02-01", "BUY", "AAPL", 2500, None),
    ("2024-02-01", "BUY", "MSFT", 2500, None),
    ("2024-09-03", "SELL", "NVDA", 0.5, None),  # half of the position
    ("2025-01-15", "DEPOSIT", "", 15000, "ILS"),
    ("2025-01-15", "BUY", "VOO", 4000, None),
]
META = {
    "VOO": ("Vanguard S&P 500 ETF", "NYSE Arca", "ETF", "USD"),
    "AAPL": ("Apple Inc.", "NASDAQ", "Stock", "USD"),
    "MSFT": ("Microsoft Corporation", "NASDAQ", "Stock", "USD"),
    "NVDA": ("NVIDIA Corporation", "NASDAQ", "Stock", "USD"),
    "TEVA.TA": ("Teva Pharmaceutical Industries", "Tel Aviv", "Stock", "ILS"),
}


def load_demo(session: Session) -> int:
    for sym, (name, exch, typ, cur) in META.items():
        remember_selection(session, {"symbol": sym, "name": name, "exchange": exch, "type": typ, "currency": cur})
    # Fetch every price first: the price cache commits, so nothing is added until all lookups succeed.
    priced = []
    for d, typ, ticker, amount, cur in PLAN:
        date = dt.date.fromisoformat(d)
        p = price_on(session, ticker, date) if ticker else None
        priced.append((date, typ, ticker, amount, cur, p))
    held: dict[str, float] = {}
    n = 0
    for date, typ, ticker, amount, cur, p in priced:
        if typ == "DEPOSIT":
            session.add(Transaction(type=TxType.DEPOSIT, date=date, price=amount, currency=cur, note="Demo"))
        else:
            date = dt.date.fromisoformat(p["date"])
            if typ == "BUY":
                qty = max(math.floor(amount / p["close"]), 1)
                held[ticker] = held.get(ticker, 0) + qty
            else:
                qty = math.floor(held[ticker] * amount)
                held[ticker] -= qty
            session.add(Transaction(ticker=ticker, type=TxType(typ), date=date, quantity=qty,
                                    price=round(p["close"], 2), currency=p["currency"], fees=0, note="Demo"))
        n += 1
    session.commit()
    return n
