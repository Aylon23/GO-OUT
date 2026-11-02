"""Deterministic fake market data for offline development and UI work.

Enabled only with PORTFOLIO_PROVIDER=synthetic. Prices are random walks, NOT real data.
Behaves like Yahoo: .TA prices in agorot, split-adjusted closes, adj_close includes dividends.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re

import numpy as np
import pandas as pd

from .provider import HISTORY_COLUMNS

CATALOG = {
    # symbol: (name, exchange, quoteType, start_price, drift, vol, first_date, div_yield)
    "AAPL": ("Apple Inc.", "NMS", "EQUITY", 27, 0.17, 0.27, "1990-01-02", 0.006),
    "MSFT": ("Microsoft Corporation", "NMS", "EQUITY", 46, 0.17, 0.25, "1990-01-02", 0.009),
    "NVDA": ("NVIDIA Corporation", "NMS", "EQUITY", 0.5, 0.42, 0.48, "1999-01-22", 0.001),
    "INTC": ("Intel Corporation", "NMS", "EQUITY", 34, 0.01, 0.32, "1990-01-02", 0.02),
    "GOOGL": ("Alphabet Inc.", "NMS", "EQUITY", 60, 0.18, 0.28, "2004-08-19", 0.0),
    "AMZN": ("Amazon.com, Inc.", "NMS", "EQUITY", 80, 0.17, 0.32, "1997-05-15", 0.0),
    "TSLA": ("Tesla, Inc.", "NMS", "EQUITY", 20, 0.30, 0.60, "2010-06-29", 0.0),
    "VOO": ("Vanguard S&P 500 ETF", "PCX", "ETF", 190, 0.11, 0.17, "2010-09-09", 0.014),
    "SPY": ("SPDR S&P 500 ETF Trust", "PCX", "ETF", 205, 0.11, 0.17, "1993-01-29", 0.014),
    "QQQ": ("Invesco QQQ Trust", "NMS", "ETF", 100, 0.15, 0.22, "1999-03-10", 0.006),
    "XLK": ("Technology Select Sector SPDR Fund", "PCX", "ETF", 40, 0.17, 0.24, "1998-12-22", 0.007),
    "TEVA": ("Teva Pharmaceutical Industries", "NYQ", "EQUITY", 60, -0.06, 0.40, "1990-01-02", 0.0),
    "TEVA.TA": ("Teva Pharmaceutical Industries", "TLV", "EQUITY", 22000, -0.06, 0.40, "1990-01-02", 0.0),
    "^GSPC": ("S&P 500", "SNP", "INDEX", 2500, 0.09, 0.17, "1990-01-02", 0.0),
    "^TA125.TA": ("TA-125", "TLV", "INDEX", 150000, 0.07, 0.16, "1992-01-02", 0.0),
    "VFIAX": ("Vanguard 500 Index Fund Admiral", "NAS", "MUTUALFUND", 250, 0.11, 0.17, "2000-11-13", 0.014),
    "ILS=X": ("USD/ILS", "CCY", "CURRENCY", 3.6, 0.0, 0.07, "2003-12-01", 0.0),
}
SPLITS = {"NVDA": [(dt.date(2021, 7, 20), 4.0), (dt.date(2024, 6, 10), 10.0)], "AAPL": [(dt.date(2020, 8, 31), 4.0)]}
SECTORS = {"AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology", "INTC": "Technology",
           "GOOGL": "Communication Services", "AMZN": "Consumer Cyclical", "TSLA": "Consumer Cyclical",
           "TEVA": "Healthcare", "TEVA.TA": "Healthcare"}

DISPLAY_START = dt.date(2015, 1, 1)


def _seed(ticker: str) -> int:
    return int(hashlib.sha256(ticker.encode()).hexdigest()[:8], 16)


class SyntheticProvider:
    def _spec(self, ticker: str):
        if ticker in CATALOG:
            return CATALOG[ticker]
        if re.match(r"^[A-Z]{1,5}(-[A-Z])?$", ticker):  # any plausible US ticker gets a made-up walk
            rng = np.random.default_rng(_seed(ticker))
            return (ticker, "NMS", "EQUITY", float(rng.uniform(20, 400)), float(rng.uniform(-0.05, 0.25)), float(rng.uniform(0.2, 0.5)), "2015-01-02", 0.0)
        return None

    def _full(self, ticker: str) -> pd.DataFrame:
        spec = self._spec(ticker)
        if spec is None:
            return pd.DataFrame(columns=HISTORY_COLUMNS)
        name, exch, qt, p0, mu, sigma, first, dy = spec
        start = max(dt.date.fromisoformat(first), DISPLAY_START)
        end = dt.date.today()
        if ticker.endswith(".TA"):
            days = pd.bdate_range(start, end, freq="C", weekmask="Sun Mon Tue Wed Thu")
        else:
            days = pd.bdate_range(start, end)
        rng = np.random.default_rng(_seed(ticker))
        n = len(days)
        r = rng.normal(mu / 252 - 0.5 * sigma**2 / 252, sigma / np.sqrt(252), n)
        close = p0 * np.exp(np.cumsum(r))
        dates = [d.date() for d in days]
        df = pd.DataFrame({"close": close}, index=dates)
        df["dividend"] = 0.0
        if dy > 0:
            for i in range(60, n, 63):
                df.iloc[i, df.columns.get_loc("dividend")] = round(close[i] * dy / 4, 4)
        # adj_close: back-adjust for dividends like Yahoo does
        factor = np.ones(n)
        k = 1.0
        for i in range(n - 1, 0, -1):
            factor[i] = k
            d = df["dividend"].iloc[i]
            if d > 0:
                k *= 1 - d / df["close"].iloc[i - 1]
        factor[0] = k
        df["adj_close"] = df["close"] * factor
        df["split"] = 0.0
        for d, ratio in SPLITS.get(ticker, []):
            if d in df.index:
                df.loc[d, "split"] = ratio
        return df[HISTORY_COLUMNS]

    def history(self, ticker: str, start: dt.date | None) -> pd.DataFrame:
        df = self._full(ticker)
        if start is not None:
            df = df[df.index >= start]
        return df

    def splits(self, ticker: str) -> list[tuple[dt.date, float]]:
        return [s for s in SPLITS.get(ticker, []) if s[0] >= DISPLAY_START]

    def search(self, query: str, max_results: int = 10) -> list[dict]:
        q = query.lower().strip()
        out = []
        for sym, (name, exch, qt, *_rest) in CATALOG.items():
            if q in sym.lower() or q in name.lower() or (q == "s&p 500" and "s&p" in name.lower()):
                out.append({"symbol": sym, "shortname": name, "longname": name, "exchange": exch,
                            "exchDisp": exch, "quoteType": qt})
        return out[:max_results]

    def info(self, ticker: str) -> dict:
        spec = self._spec(ticker)
        if spec is None:
            return {}
        name, exch, qt, *_ = spec
        pe = {"AAPL": 33.1, "MSFT": 36.4, "NVDA": 52.0, "INTC": -12.0, "GOOGL": 24.5, "AMZN": 41.0, "TSLA": 95.0, "VOO": 27.2,
              "SPY": 27.3, "QQQ": 34.8, "XLK": 39.5, "TEVA": 15.2, "TEVA.TA": 15.4, "VFIAX": 27.1}.get(ticker)
        return {"trailingPE": pe, "forwardPE": pe * 0.85 if pe and pe > 0 else None, "beta": 1.1, "trailingAnnualDividendYield": 0.006,
                "longName": name, "exchange": exch, "quoteType": qt, "sector": SECTORS.get(ticker, ""),
                "country": "Israel" if ticker.endswith(".TA") else "United States",
                "currency": "ILA" if ticker.endswith(".TA") else "USD"}

    def recent_closes(self, tickers: list[str]) -> dict[str, list[tuple[dt.date, float]]]:
        out = {}
        for t in tickers:
            df = self._full(t).tail(5)
            out[t] = [(d, float(v)) for d, v in df["close"].items()]
        return out
