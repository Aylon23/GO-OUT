"""The only module that talks to Yahoo. Everything else goes through `MarketProvider`,
so tests (and offline dev) can swap in a fake.

All frames returned here are in raw Yahoo units (agorot for .TA). Unit fixes happen in cache.py.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from typing import Protocol

import pandas as pd

log = logging.getLogger(__name__)

HISTORY_COLUMNS = ["close", "adj_close", "dividend", "split"]


class ProviderError(Exception):
    pass


class MarketProvider(Protocol):
    def history(self, ticker: str, start: dt.date | None) -> pd.DataFrame:
        """Daily bars from `start` (inclusive) to today; `start=None` means full history.
        Index: datetime.date. Columns: close, adj_close, dividend, split (ratio or 0)."""

    def splits(self, ticker: str) -> list[tuple[dt.date, float]]: ...

    def search(self, query: str, max_results: int = 10) -> list[dict]:
        """Raw Yahoo quote dicts (symbol, shortname, longname, exchange, exchDisp, quoteType)."""

    def info(self, ticker: str) -> dict:
        """Name, sector, country, currency, exchange, quoteType. Missing keys are fine."""

    def recent_bars(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        """Optional: the last few daily bars for many tickers in one request."""

    def recent_closes(self, tickers: list[str]) -> dict[str, list[tuple[dt.date, float]]]:
        """Last few daily closes for each ticker (raw units), oldest first."""

    def fund_sectors(self, ticker: str) -> tuple[dict[str, float], str]:
        """A fund's sector weights ({"technology": 0.5, ...}) and its category, if any."""

    def earnings_dates(self, ticker: str) -> list[tuple[dt.date, str]]:
        """Past and upcoming earnings report dates with timing ("before_open" | "after_close")."""


def _frame_from_yahoo(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    out = pd.DataFrame(index=[d.date() for d in pd.to_datetime(df.index)])
    out["close"] = df["Close"].astype(float).values
    out["adj_close"] = (df["Adj Close"] if "Adj Close" in df else df["Close"]).astype(float).values
    out["dividend"] = (df["Dividends"] if "Dividends" in df else 0.0)
    out["split"] = (df["Stock Splits"] if "Stock Splits" in df else 0.0)
    out[["dividend", "split"]] = out[["dividend", "split"]].fillna(0.0).astype(float)
    out = out[out["close"].notna() & (out["close"] > 0)]
    out = out[~out.index.duplicated(keep="last")]
    return out[HISTORY_COLUMNS]


class YahooProvider:
    def __init__(self) -> None:
        import yfinance as yf  # imported here so tests never need network

        self.yf = yf

    def history(self, ticker: str, start: dt.date | None) -> pd.DataFrame:
        t = self.yf.Ticker(ticker)
        try:
            if start is None:
                df = t.history(period="max", auto_adjust=False, actions=True)
            else:
                end = dt.date.today() + dt.timedelta(days=1)
                df = t.history(start=start.isoformat(), end=end.isoformat(), auto_adjust=False, actions=True)
        except Exception as exc:  # yfinance raises many types
            raise ProviderError(str(exc)) from exc
        return _frame_from_yahoo(df)

    def splits(self, ticker: str) -> list[tuple[dt.date, float]]:
        try:
            s = self.yf.Ticker(ticker).splits
        except Exception as exc:
            raise ProviderError(str(exc)) from exc
        if s is None or len(s) == 0:
            return []
        return [(pd.Timestamp(d).date(), float(r)) for d, r in s.items() if r and r > 0]

    def search(self, query: str, max_results: int = 10) -> list[dict]:
        try:
            s = self.yf.Search(
                query,
                max_results=max_results,
                news_count=0,
                lists_count=0,
                recommended=0,
                include_cb=False,
                raise_errors=True,
            )
            return list(s.quotes or [])
        except Exception as exc:
            raise ProviderError(str(exc)) from exc

    def info(self, ticker: str) -> dict:
        try:
            return dict(self.yf.Ticker(ticker).info or {})
        except Exception as exc:
            raise ProviderError(str(exc)) from exc

    def fund_sectors(self, ticker: str) -> tuple[dict[str, float], str]:
        t = self.yf.Ticker(ticker)
        try:
            weights = dict(t.funds_data.sector_weightings or {})
        except Exception:
            weights = {}
        try:
            category = (t.info or {}).get("category") or ""
        except Exception:
            category = ""
        return weights, category

    def earnings_dates(self, ticker: str) -> list[tuple[dt.date, str]]:
        try:
            df = self.yf.Ticker(ticker).get_earnings_dates(limit=16)
        except Exception as exc:
            raise ProviderError(str(exc)) from exc
        out = []
        if df is None or df.empty:
            return out
        for ts in df.index:
            t = pd.Timestamp(ts)
            if t.tzinfo is not None:
                t = t.tz_convert("America/New_York")
            out.append((t.date(), "after_close" if t.hour >= 12 else "before_open"))
        return sorted(set(out))

    def recent_bars(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        """The last few daily bars (today's is the live price while the market is open) for many
        tickers in one request."""
        out: dict[str, pd.DataFrame] = {}
        if not tickers:
            return out
        try:
            df = self.yf.download(
                tickers, period="5d", interval="1d", auto_adjust=False, actions=True, group_by="ticker",
                progress=False, threads=True, multi_level_index=True,
            )
        except Exception as exc:
            raise ProviderError(str(exc)) from exc
        if df is None or df.empty:
            return out
        for t in tickers:
            try:
                sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
            except KeyError:
                continue
            out[t] = _frame_from_yahoo(sub.dropna(how="all"))
        return out

    def recent_closes(self, tickers: list[str]) -> dict[str, list[tuple[dt.date, float]]]:
        out: dict[str, list[tuple[dt.date, float]]] = {}
        if not tickers:
            return out
        try:
            df = self.yf.download(
                tickers, period="7d", interval="1d", auto_adjust=False, group_by="ticker",
                progress=False, threads=True, multi_level_index=True,
            )
        except Exception as exc:
            raise ProviderError(str(exc)) from exc
        if df is None or df.empty:
            return out
        for t in tickers:
            try:
                col = df[t]["Close"] if isinstance(df.columns, pd.MultiIndex) else df["Close"]
            except KeyError:
                continue
            col = col.dropna()
            out[t] = [(pd.Timestamp(d).date(), float(v)) for d, v in col.items()]
        return out


_provider: MarketProvider | None = None


def get_provider() -> MarketProvider:
    global _provider
    if _provider is None:
        if os.environ.get("PORTFOLIO_PROVIDER", "").lower() == "synthetic":
            from .synthetic import SyntheticProvider

            log.warning("Using SYNTHETIC market data. Prices are not real.")
            _provider = SyntheticProvider()
        else:
            _provider = YahooProvider()
    return _provider


def set_provider(p: MarketProvider | None) -> None:
    global _provider
    _provider = p
