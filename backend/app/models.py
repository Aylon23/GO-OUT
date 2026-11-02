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
    aliases: str = ""  # other names for the command box and the bot, comma separated ("קרן, keren")
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
    rec_id: Optional[int] = Field(default=None, index=True)  # the recommendation this trade followed

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


class Recommendation(SQLModel, table=True):
    """A call you made ("buy AVGO", "sell TSLA"), followed by one or more portfolios."""

    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str = Field(index=True)
    action: str = "BUY"  # BUY or SELL
    date: dt.date
    price: Optional[float] = None  # close on that date, filled in when created
    thesis: str = ""
    target: Optional[float] = None
    closed_date: Optional[dt.date] = None  # a BUY call ends when you call SELL on the same ticker
    created_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


class RealizedEntry(SQLModel, table=True):
    """Profit or loss already taken, from records without full trade history (e.g. a spreadsheet)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    portfolio_id: int = Field(index=True)
    ticker: str = ""
    amount: float = 0.0
    currency: str = "USD"
    date: Optional[dt.date] = None
    note: str = ""


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
    base_currency: str = "USD"
    benchmarks: str = "SPY,QQQ"  # comma separated
    risk_free: float = 0.04
    theme: str = "system"
    auto_dividends: bool = True
    telegram_token: str = ""
    telegram_chat_id: str = ""  # the only chat the bot listens to, set by pairing
    telegram_pair_code: str = ""
    weekly_report: bool = True
    weekly_report_sent: str = ""  # date of the last Sunday summary sent
    allow_short_hedges: bool = False
    inbox_dir: str = ""
    memo_language: str = "he"  # he | en (memo and periodic reports)
    report_last_weekly: str = ""  # period end of the last weekly reports generated
    report_last_monthly: str = ""
    report_auto_send: bool = True  # send new reports to my Telegram chat
    band_scale: float = 1.0  # widens scenario ranges when past outcomes fell outside them too often
    band_note: str = ""  # folder watched for researched files; empty = ./inbox next to the app  # protection ideas may include shorts and inverse funds
    anthropic_key: str = ""  # optional: lets Claude read scenarios; the free local rules work without it
    aliases_backfilled: bool = False  # nicknames generated once for portfolios made before nicknames existed

    def benchmark_list(self) -> list[str]:
        return [b.strip().upper() for b in self.benchmarks.split(",") if b.strip()]


class Scenario(SQLModel, table=True):
    """A saved stress-test scenario: the sentence and the (edited) shocks."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = ""
    text: str = ""
    shocks: str = "[]"  # JSON list of {symbol, kind, move}
    events: str = "[]"  # JSON list of event ids shown
    used_events: str = "[]"  # JSON list of event ids the sizes came from
    horizon_weeks: float = 1.0
    created_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


class BookVersion(SQLModel, table=True):
    """One saved state of the scenario book: which scenarios, with my probability for each."""

    id: Optional[int] = Field(default=None, primary_key=True)
    entries: str = "[]"  # JSON: [{scenario_id, title, probability}]
    note: str = ""
    created_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


class EarningsDates(SQLModel, table=True):
    """Cached report dates per symbol (refreshed weekly)."""

    symbol: str = Field(primary_key=True)
    dates: str = "[]"  # JSON: [[YYYY-MM-DD, timing], ...]
    fetched_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


class EarningsItem(SQLModel, table=True):
    """One company from an imported earnings brief, with its prediction-journal scores."""

    id: Optional[int] = Field(default=None, primary_key=True)
    date: dt.date = Field(index=True)  # the trading day reported
    symbol: str = Field(index=True)
    data: str = "{}"  # the company entry as imported
    confidence: str = ""
    days_direction: str = ""  # up | down | flat | "" (not scored)
    weeks_direction: str = ""
    base_close: Optional[float] = None  # close of the reaction day, the journal's starting point
    base_date: Optional[dt.date] = None
    move_5d: Optional[float] = None
    move_20d: Optional[float] = None
    score_5d: str = ""  # hit | miss | mixed
    score_20d: str = ""
    score: str = ""  # overall
    imported_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


class ScenarioPrediction(SQLModel, table=True):
    """What the engine predicted for an imported scenario, scored once its horizon has passed."""

    id: Optional[int] = Field(default=None, primary_key=True)
    spec_id: str = Field(index=True)
    title: str = ""
    start: dt.date
    end: dt.date
    horizon: str = "1m"
    predicted: str = "{}"  # JSON: factor mids, family median/p5/p95, holdings snapshot
    actual: str = "{}"  # JSON once scored
    abs_error: Optional[float] = None
    inside: Optional[bool] = None
    scored_at: Optional[dt.datetime] = None


class PersonReport(SQLModel, table=True):
    """A generated weekly or monthly report (a person's, or the family summary), with its files."""

    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = "weekly"  # weekly | monthly
    portfolio_id: Optional[int] = None  # None = family summary
    name: str = ""
    period_start: dt.date
    period_end: dt.date
    lang: str = "he"
    png: str = ""  # file names in data/reports
    pdf: str = ""
    data: str = "{}"
    created_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    sent_at: Optional[dt.datetime] = None


class Watch(SQLModel, table=True):
    """A symbol on the watchlist (candidates for new money)."""

    symbol: str = Field(primary_key=True)
    added_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


class ResearchedSpec(SQLModel, table=True):
    """A Scenario Spec researched outside the app and imported (docs/scenario-spec.schema.json)."""

    id: str = Field(primary_key=True)
    title: str = ""
    spec: str = "{}"  # the spec as imported, never edited
    report: str = "{}"  # the validation report at import
    source: str = "paste"  # paste | inbox
    imported_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


class FactorExposure(SQLModel, table=True):
    """A holding's factor betas (see factors.py), in dollars ("usd") or shekels ("ils")."""

    symbol: str = Field(primary_key=True)
    view: str = Field(primary_key=True)
    betas: str = "{}"
    r2: Optional[float] = None
    resid_vol: Optional[float] = None  # annualized
    weeks: int = 0
    short_history: bool = False
    sector: str = ""  # sector ETF used, if any
    as_of: dt.date
    estimated_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


from .market.funds import FundSectors  # noqa: E402,F401  (registers the table)
from .logos import LogoCache  # noqa: E402,F401  (registers the table)
