"""Sector look-through for funds: an ETF's weight is split across the sectors it holds, so the
sector bar never shows "ETF". Breakdowns come from Yahoo and are cached for 30 days."""
from __future__ import annotations

import datetime as dt
import json

from sqlmodel import Field, Session, SQLModel

from .provider import ProviderError, get_provider

TTL = dt.timedelta(days=30)
LABELS = {
    "technology": "Technology", "financial_services": "Financial Services", "healthcare": "Healthcare",
    "consumer_cyclical": "Consumer Cyclical", "consumer_defensive": "Consumer Defensive", "industrials": "Industrials",
    "communication_services": "Communication Services", "energy": "Energy", "utilities": "Utilities",
    "realestate": "Real Estate", "real_estate": "Real Estate", "basic_materials": "Basic Materials",
}


class FundSectors(SQLModel, table=True):
    ticker: str = Field(primary_key=True)
    data: str = "{}"  # JSON {sector label: weight}, weights sum to ~1; "{}" = no breakdown
    category: str = ""  # Yahoo fund category, used when there is no breakdown (e.g. "Digital Assets")
    fetched_at: dt.datetime


def fund_sectors(session: Session, ticker: str) -> tuple[dict[str, float], str]:
    ticker = ticker.upper()
    now = dt.datetime.now(dt.timezone.utc)
    row = session.get(FundSectors, ticker)
    if row is not None:
        fetched = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=dt.timezone.utc)
        if now - fetched < TTL:
            return json.loads(row.data), row.category
    try:
        raw, category = get_provider().fund_sectors(ticker)
    except (ProviderError, AttributeError):
        raw, category = {}, ""
    weights = {LABELS.get(k, k.replace("_", " ").title()): float(v) for k, v in (raw or {}).items() if v and float(v) > 0}
    total = sum(weights.values())
    if total > 0:
        weights = {k: v / total for k, v in weights.items()}
    row = row or FundSectors(ticker=ticker, fetched_at=now)
    row.data, row.category, row.fetched_at = json.dumps(weights), category or "", now
    session.merge(row)
    session.commit()
    return weights, category or ""
