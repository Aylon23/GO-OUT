"""Price / FX / split cache.

Rules:
- First request for a ticker fetches its full history once. After that we only fetch from the
  last cached day forward (one overlapping day is refetched to replace a partial bar).
- `.TA` listings are quoted in agorot by Yahoo: divide by 100 at ingest (indices excluded).
- Cached closes stay on the *current* split basis: when a new split shows up in an incremental
  fetch, already-cached rows are rescaled instead of refetched.
- adj_close is spliced so its ratio series stays continuous across fetches.
"""
from __future__ import annotations

import datetime as dt
import threading

import pandas as pd
from sqlalchemy.dialects.sqlite import insert
from sqlmodel import Session, select, update

from ..models import PriceCache, PriceCoverage, SplitEvent, SymbolMeta
from .provider import ProviderError, get_provider

FX_TICKER = "ILS=X"
STALE_AFTER = dt.timedelta(hours=6)

_lock = threading.RLock()
_mem: dict[str, tuple[dt.date, pd.DataFrame]] = {}


class NoPriceData(Exception):
    def __init__(self, ticker: str, detail: str = ""):
        self.ticker = ticker
        self.detail = detail
        if detail:  # the request itself failed (network, rate limit)
            msg = f"Couldn't reach Yahoo Finance for {ticker} prices. Check your internet connection and try again."
        else:
            msg = f"Yahoo didn't return prices for {ticker}. Check the ticker or try again."
        super().__init__(msg)


def is_agorot(ticker: str) -> bool:
    t = ticker.upper()
    return t.endswith(".TA") and not t.startswith("^")


def normalize_units(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Rule 1: .TA prices come in agorot. Convert to shekels."""
    if df.empty or not is_agorot(ticker):
        return df
    df = df.copy()
    for col in ("close", "adj_close", "dividend"):
        df[col] = df[col] / 100.0
    return df


def guess_currency(ticker: str) -> str:
    t = ticker.upper()
    if t.endswith(".TA"):
        return "ILS"
    return "USD"


def currency_for(session: Session, ticker: str) -> str:
    meta = session.get(SymbolMeta, ticker.upper())
    if meta and meta.currency in ("USD", "ILS"):
        return meta.currency
    return guess_currency(ticker)


def _upsert_prices(session: Session, ticker: str, df: pd.DataFrame, currency: str) -> None:
    rows = [
        {"ticker": ticker, "date": d, "close": float(r.close), "adj_close": float(r.adj_close),
         "dividend": float(r.dividend), "currency": currency}
        for d, r in df.iterrows()
    ]
    for i in range(0, len(rows), 500):
        chunk = rows[i : i + 500]
        stmt = insert(PriceCache).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["ticker", "date"],
            set_={"close": stmt.excluded.close, "adj_close": stmt.excluded.adj_close,
                  "dividend": stmt.excluded.dividend, "currency": stmt.excluded.currency},
        )
        session.execute(stmt)


def _store_splits(session: Session, ticker: str, splits: list[tuple[dt.date, float]]) -> None:
    for d, ratio in splits:
        if ratio and ratio > 0 and ratio != 1:
            session.merge(SplitEvent(ticker=ticker, date=d, ratio=float(ratio)))


def ensure_history(session: Session, ticker: str, now: dt.datetime | None = None) -> None:
    """Make sure the cache holds this ticker's history up to the latest available day."""
    ticker = ticker.upper()
    now = now or dt.datetime.now(dt.timezone.utc)
    with _lock:
        cov = session.get(PriceCoverage, ticker)
        provider = get_provider()
        currency = currency_for(session, ticker)
        if cov is None:
            try:
                raw = provider.history(ticker, None)
            except ProviderError as exc:
                raise NoPriceData(ticker, str(exc)) from exc
            df = normalize_units(raw, ticker)
            if df.empty:
                raise NoPriceData(ticker)
            _upsert_prices(session, ticker, df, currency)
            try:
                splits = provider.splits(ticker)
            except ProviderError:
                splits = [(d, r) for d, r in df["split"].items() if r > 0]
            _store_splits(session, ticker, splits)
            session.add(PriceCoverage(ticker=ticker, first_date=df.index.min(), last_date=df.index.max(), checked_at=now))
            session.commit()
            _mem.pop(ticker, None)
            return

        checked = cov.checked_at if cov.checked_at.tzinfo else cov.checked_at.replace(tzinfo=dt.timezone.utc)
        if cov.last_date >= now.date() or now - checked < STALE_AFTER:
            return
        try:
            raw = provider.history(ticker, cov.last_date)
        except ProviderError:
            return  # keep serving cached data; next call retries
        new = normalize_units(raw, ticker)
        cov.checked_at = now
        if new.empty:
            session.add(cov)
            session.commit()
            return
        splice_new_segment(session, ticker, cov.last_date, new)
        _upsert_prices(session, ticker, new, currency)
        _store_splits(session, ticker, [(d, r) for d, r in new["split"].items() if r > 0 and d > cov.last_date])
        cov.last_date = max(cov.last_date, new.index.max())
        session.add(cov)
        session.commit()
        _mem.pop(ticker, None)


def splice_new_segment(session: Session, ticker: str, overlap: dt.date, new: pd.DataFrame) -> None:
    """Rescale cached rows so they line up with a freshly fetched segment that starts at `overlap`."""
    split_ratio = 1.0
    for d, r in new["split"].items():
        if d > overlap and r > 0:
            split_ratio *= r
    if split_ratio != 1.0:
        session.execute(
            update(PriceCache).where(PriceCache.ticker == ticker).values(
                close=PriceCache.close / split_ratio,
                adj_close=PriceCache.adj_close / split_ratio,
                dividend=PriceCache.dividend / split_ratio,
            )
        )
    cached = session.get(PriceCache, (ticker, overlap))
    if cached is not None and overlap in new.index and cached.adj_close > 0:
        session.refresh(cached)
        factor = float(new.loc[overlap, "adj_close"]) / cached.adj_close
        if abs(factor - 1) > 1e-9:
            session.execute(
                update(PriceCache).where(PriceCache.ticker == ticker).values(adj_close=PriceCache.adj_close * factor)
            )


def load_history(session: Session, ticker: str, fetch: bool = True) -> pd.DataFrame:
    """Cached daily bars for a ticker: columns close, adj_close, dividend; index = date."""
    ticker = ticker.upper()
    if fetch:
        ensure_history(session, ticker)
    cov = session.get(PriceCoverage, ticker)
    if cov is None:
        raise NoPriceData(ticker)
    hit = _mem.get(ticker)
    if hit and hit[0] == cov.last_date:
        return hit[1]
    rows = session.exec(select(PriceCache).where(PriceCache.ticker == ticker).order_by(PriceCache.date)).all()
    df = pd.DataFrame(
        {"close": [r.close for r in rows], "adj_close": [r.adj_close for r in rows], "dividend": [r.dividend for r in rows]},
        index=[r.date for r in rows],
    )
    _mem[ticker] = (cov.last_date, df)
    return df


def load_splits(session: Session, ticker: str) -> list[tuple[dt.date, float]]:
    rows = session.exec(select(SplitEvent).where(SplitEvent.ticker == ticker.upper()).order_by(SplitEvent.date)).all()
    return [(r.date, r.ratio) for r in rows]


def first_trading_date(session: Session, ticker: str) -> dt.date:
    ensure_history(session, ticker)
    cov = session.get(PriceCoverage, ticker.upper())
    return cov.first_date


def split_factor_after(splits: list[tuple[dt.date, float]], date: dt.date) -> float:
    """How many of today's shares one share bought on `date` became."""
    f = 1.0
    for d, r in splits:
        if d > date:
            f *= r
    return f


def price_on(session: Session, ticker: str, date: dt.date) -> dict:
    """Close on `date` or the nearest prior trading day, as it traded that day (not split-adjusted)."""
    df = load_history(session, ticker)
    prior = df[df.index <= date]
    if prior.empty:
        first = df.index.min()
        raise ValueError(f"{ticker.upper()} has no prices before {first.isoformat()}. First trading day is {first.isoformat()}.")
    d = prior.index[-1]
    factor = split_factor_after(load_splits(session, ticker), d)
    return {
        "symbol": ticker.upper(),
        "date": d.isoformat(),
        "requested_date": date.isoformat(),
        "close": float(prior["close"].iloc[-1]) * factor,
        "currency": currency_for(session, ticker),
        "first_date": df.index.min().isoformat(),
    }


def clear_memory_cache() -> None:
    _mem.clear()
