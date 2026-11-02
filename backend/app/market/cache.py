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
STALE_AFTER = dt.timedelta(minutes=30)  # background lookups (search, scenarios): at most every 30 minutes
LIVE_AFTER = dt.timedelta(seconds=60)  # a page load re-reads the latest prices if older than this
BACK = dt.timedelta(days=7)  # refetch a week back, so a completed day anchors the splice
RECENT_DAYS = 5  # the batch refresh covers gaps up to this many calendar days; longer gaps use history()

_lock = threading.RLock()
_mem: dict[str, tuple[dt.date, pd.DataFrame]] = {}
_version = 0  # bumped whenever cached prices change, so anything built from them is rebuilt


def data_version() -> int:
    return _version


def _changed(ticker: str) -> None:
    global _version
    _version += 1
    _mem.pop(ticker, None)


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
            _changed(ticker)
            return

        # Today's bar is refetched too: a bar saved at the open is replaced by the latest price.
        if now - _aware(cov.checked_at) < STALE_AFTER:
            return
        try:
            raw = provider.history(ticker, cov.last_date - BACK)
        except ProviderError:
            return  # keep serving cached data; next call retries
        _apply_increment(session, ticker, cov, normalize_units(raw, ticker), now)


def _aware(t: dt.datetime) -> dt.datetime:
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _apply_increment(session: Session, ticker: str, cov: PriceCoverage, new: pd.DataFrame, now: dt.datetime) -> None:
    """Merge freshly fetched bars that overlap the cached ones.

    The adj_close splice is anchored on the last *completed* cached day the fetch covers, never on
    today's partial bar (its price keeps moving; anchoring on it would rescale all of history)."""
    cov.checked_at = now
    cached = {r.date: r for r in session.exec(select(PriceCache).where(
        PriceCache.ticker == ticker, PriceCache.date >= (new.index.min() if not new.empty else cov.last_date))).all()}
    common = [d for d in new.index if d in cached and d <= cov.last_date]
    if new.empty or not common:
        session.add(cov)
        session.commit()
        return
    done = [d for d in common if d < now.date()]
    overlap = max(done) if done else max(common)
    new = new[new.index >= overlap]
    changed = any(d not in cached or abs(cached[d].close - float(r.close)) > 1e-12
                  or abs(cached[d].adj_close - float(r.adj_close)) > 1e-12
                  or abs(cached[d].dividend - float(r.dividend)) > 1e-12 for d, r in new.iterrows())
    if changed:
        splice_new_segment(session, ticker, overlap, new)
        _upsert_prices(session, ticker, new, currency_for(session, ticker))
        _store_splits(session, ticker, [(d, r) for d, r in new["split"].items() if r > 0 and d > overlap])
        cov.last_date = max(cov.last_date, new.index.max())
    session.add(cov)
    session.commit()
    if changed:
        _changed(ticker)


def refresh_latest(session: Session, tickers, now: dt.datetime | None = None) -> None:
    """Bring the latest prices of these (already cached) tickers up to date in one batched request.

    Called when a page loads, so a refresh shows current prices, not the ones saved at the open.
    Tickers checked in the last minute are skipped, so the many requests of one page load cost one
    fetch. Failures keep the cached prices."""
    now = now or dt.datetime.now(dt.timezone.utc)
    with _lock:
        due: dict[str, PriceCoverage] = {}
        for t in {x.upper() for x in tickers if x}:
            cov = session.get(PriceCoverage, t)
            if cov is not None and now - _aware(cov.checked_at) >= LIVE_AFTER:
                due[t] = cov
        if not due:
            return
        provider = get_provider()
        batch = {t: c for t, c in due.items() if (now.date() - c.last_date).days <= RECENT_DAYS}
        bars: dict[str, pd.DataFrame] = {}
        if batch and hasattr(provider, "recent_bars"):
            try:
                bars = provider.recent_bars(sorted(batch))
            except ProviderError:
                bars = {}
        for t, cov in due.items():
            raw = bars.get(t)
            if raw is None or raw.empty or raw.index.min() > cov.last_date:
                try:  # not in the batch, or a longer gap: the per-ticker path
                    raw = provider.history(t, cov.last_date - BACK)
                except ProviderError:  # keep the cached prices; try again in a minute, not on every request
                    cov.checked_at = now
                    session.add(cov)
                    session.commit()
                    continue
            _apply_increment(session, t, cov, normalize_units(raw, t), now)


def splice_new_segment(session: Session, ticker: str, overlap: dt.date, new: pd.DataFrame) -> None:
    """Rescale cached rows so they line up with a freshly fetched segment that starts at `overlap`."""
    split_ratio = 1.0
    for d, r in new["split"].items():  # only splits not seen in an earlier fetch
        if d > overlap and r > 0 and session.get(SplitEvent, (ticker, d)) is None:
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
