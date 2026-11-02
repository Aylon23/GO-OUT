"""Database tables.

Conventions for Transaction:
- BUY / SELL: `quantity` shares at `price` per share, in `currency`. `fees` in the same currency.
- DIVIDEND: `ticker` set, `price` is the total cash received, `quantity` is 0.
- DEPOSIT / WITHDRAWAL: `ticker` empty, `price` is the cash amount, `quantity` is 0.
"""
from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class TxType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"


CASH_TYPES = {TxType.DEPOSIT, TxType.WITHDRAWAL}
TRADE_TYPES = {TxType.BUY, TxType.SELL}


class Portfolio(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    note: str = ""
    created_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


class Transaction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    portfolio_id: int = Field(default=1, index=True)
    ticker: str = Field(default="", index=True)
    type: TxType
    date: dt.date = Field(index=True)
    quantity: float = 0.0
    price: float = 0.0
    currency: str = "USD"
    fees: float = 0.0
    note: str = ""
    # Average cost per share when a position was imported from a holdings list (no purchase
    # history). The BUY itself is recorded at that day's close so performance starts from
    # market value; this field keeps the real cost basis for gain/loss.
    cost_price: Optional[float] = None

    @property
    def amount(self) -> float:
        """Cash amount of the transaction in its own currency (before fees)."""
        if self.type in TRADE_TYPES:
            return self.quantity * self.price
        return self.price


class PriceCache(SQLModel, table=True):
    """One row per ticker per trading day. Values are already in major units (agorot / 100 for .TA)
    and on the current split basis. `dividend` is per share, same basis."""

    ticker: str = Field(primary_key=True)
    date: dt.date = Field(primary_key=True)
    close: float
    adj_close: float
    dividend: float = 0.0
    currency: str = "USD"


class PriceCoverage(SQLModel, table=True):
    """Which date range is cached for a ticker, so we only ever fetch what is missing."""

    ticker: str = Field(primary_key=True)
    first_date: dt.date
    last_date: dt.date
    checked_at: dt.datetime


class SplitEvent(SQLModel, table=True):
    ticker: str = Field(primary_key=True)
    date: dt.date = Field(primary_key=True)
    ratio: float  # 4.0 means 4-for-1


class SymbolMeta(SQLModel, table=True):
    ticker: str = Field(primary_key=True)
    name: str = ""
    exchange: str = ""
    type: str = ""  # Stock / ETF / Index / Fund
    sector: str = ""
    country: str = ""
    currency: str = ""
    info_fetched: bool = False


class Fundamentals(SQLModel, table=True):
    """Valuation figures from Yahoo, refreshed at most once a day."""

    ticker: str = Field(primary_key=True)
    trailing_pe: Optional[float] = None
    forward_pe: Optional[float] = None
    beta: Optional[float] = None
    dividend_yield: Optional[float] = None
    fetched_at: dt.datetime


class RecentSymbol(SQLModel, table=True):
    ticker: str = Field(primary_key=True)
    selected_at: dt.datetime


class Settings(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    base_currency: str = "ILS"
    benchmarks: str = "SPY,QQQ"  # comma separated
    risk_free: float = 0.04
    theme: str = "system"
    auto_dividends: bool = True

    def benchmark_list(self) -> list[str]:
        return [b.strip().upper() for b in self.benchmarks.split(",") if b.strip()]
