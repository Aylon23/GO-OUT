"""Deterministic fake market data for offline development and UI work.

Enabled only with PORTFOLIO_PROVIDER=synthetic. Prices are random walks, NOT real data.
The walks share a few hidden factors (market, tech, chips, rates, oil, gold, dollar, Israel),
so correlations look like a real market: tech moves with QQQ, energy with oil, and so on.
Behaves like Yahoo: .TA prices in agorot, split-adjusted closes, adj_close includes dividends.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
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
    # factor proxies used by the scenario page
    "IWM": ("iShares Russell 2000 ETF", "PCX", "ETF", 110, 0.08, 0.22, "2000-05-26", 0.012),
    "SOXX": ("iShares Semiconductor ETF", "NMS", "ETF", 90, 0.20, 0.32, "2001-07-13", 0.008),
    "TLT": ("iShares 20+ Year Treasury Bond ETF", "NMS", "ETF", 120, 0.01, 0.14, "2002-07-30", 0.03),
    "IEF": ("iShares 7-10 Year Treasury Bond ETF", "NMS", "ETF", 105, 0.01, 0.07, "2002-07-30", 0.025),
    "HYG": ("iShares iBoxx High Yield Corporate Bond ETF", "PCX", "ETF", 85, 0.02, 0.08, "2007-04-11", 0.05),
    "USO": ("United States Oil Fund", "PCX", "ETF", 60, -0.02, 0.36, "2006-04-10", 0.0),
    "GLD": ("SPDR Gold Shares", "PCX", "ETF", 115, 0.07, 0.15, "2004-11-18", 0.0),
    "UUP": ("Invesco DB US Dollar Index Bullish Fund", "PCX", "ETF", 24, 0.01, 0.07, "2007-02-20", 0.0),
    "ITA": ("iShares U.S. Aerospace & Defense ETF", "BTS", "ETF", 60, 0.10, 0.20, "2006-05-05", 0.01),
    "EIS": ("iShares MSCI Israel ETF", "BTS", "ETF", 45, 0.05, 0.20, "2008-03-28", 0.01),
    "XLF": ("Financial Select Sector SPDR Fund", "PCX", "ETF", 20, 0.09, 0.21, "1998-12-22", 0.017),
    "XLE": ("Energy Select Sector SPDR Fund", "PCX", "ETF", 70, 0.04, 0.27, "1998-12-22", 0.035),
    "XLV": ("Health Care Select Sector SPDR Fund", "PCX", "ETF", 65, 0.09, 0.15, "1998-12-22", 0.015),
    "XLY": ("Consumer Discretionary Select Sector SPDR Fund", "PCX", "ETF", 70, 0.11, 0.20, "1998-12-22", 0.008),
    "XLP": ("Consumer Staples Select Sector SPDR Fund", "PCX", "ETF", 45, 0.07, 0.13, "1998-12-22", 0.025),
    "XLI": ("Industrial Select Sector SPDR Fund", "PCX", "ETF", 55, 0.09, 0.19, "1998-12-22", 0.016),
    "XLU": ("Utilities Select Sector SPDR Fund", "PCX", "ETF", 42, 0.07, 0.16, "1998-12-22", 0.03),
    "XLB": ("Materials Select Sector SPDR Fund", "PCX", "ETF", 45, 0.07, 0.20, "1998-12-22", 0.019),
    "XLRE": ("Real Estate Select Sector SPDR Fund", "PCX", "ETF", 32, 0.05, 0.19, "2015-10-08", 0.033),
    "XLC": ("Communication Services Select Sector SPDR Fund", "PCX", "ETF", 50, 0.10, 0.21, "2018-06-19", 0.008),
    "^VIX": ("CBOE Volatility Index", "CBO", "INDEX", 16, 0.0, 0.85, "1990-01-02", 0.0),
    "BTC-USD": ("Bitcoin USD", "CCC", "CRYPTOCURRENCY", 300, 0.45, 0.65, "2014-09-17", 0.0),
    # style factors and test stocks for the factor model
    "IWD": ("iShares Russell 1000 Value ETF", "PCX", "ETF", 100, 0.08, 0.17, "2000-05-22", 0.02),
    "IWF": ("iShares Russell 1000 Growth ETF", "PCX", "ETF", 100, 0.13, 0.19, "2000-05-22", 0.006),
    "MTUM": ("iShares MSCI USA Momentum Factor ETF", "BTS", "ETF", 70, 0.12, 0.18, "2013-04-16", 0.008),
    "QUAL": ("iShares MSCI USA Quality Factor ETF", "BTS", "ETF", 65, 0.11, 0.16, "2013-07-16", 0.01),
    "XOM": ("Exxon Mobil Corporation", "NYQ", "EQUITY", 80, 0.06, 0.28, "1990-01-02", 0.035),
    "DAL": ("Delta Air Lines, Inc.", "NYQ", "EQUITY", 40, 0.05, 0.40, "2007-05-03", 0.0),
    "NEE": ("NextEra Energy, Inc.", "NYQ", "EQUITY", 40, 0.08, 0.20, "1990-01-02", 0.025),
    "^TNX": ("CBOE 10-Year Treasury Yield", "CBO", "INDEX", 2.2, 0.03, 0.25, "1990-01-02", 0.0),
    "CL=F": ("Crude Oil Futures", "NYM", "FUTURE", 50, 0.02, 0.36, "2000-08-23", 0.0),
    "BZ=F": ("Brent Crude Futures", "NYM", "FUTURE", 55, 0.02, 0.34, "2007-07-30", 0.0),
}
SPLITS = {"NVDA": [(dt.date(2021, 7, 20), 4.0), (dt.date(2024, 6, 10), 10.0)], "AAPL": [(dt.date(2020, 8, 31), 4.0)]}
SECTORS = {"XOM": "Energy", "DAL": "Industrials", "NEE": "Utilities", "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology", "INTC": "Technology",
           "GOOGL": "Communication Services", "AMZN": "Consumer Cyclical", "TSLA": "Consumer Cyclical",
           "TEVA": "Healthcare", "TEVA.TA": "Healthcare"}

DISPLAY_START = dt.date(2015, 1, 1)

# Hidden factor loadings. Annual vol of each factor:
FACTOR_VOL = {"M": 0.16, "T": 0.12, "S": 0.18, "R": 0.12, "O": 0.35, "G": 0.14, "D": 0.07, "I": 0.14, "E": 0.12, "F": 0.10}
LOADINGS = {
    "IWD": {"M": 0.95, "F": 0.35, "E": 0.15}, "IWF": {"M": 1.05, "T": 0.55}, "MTUM": {"M": 1.0, "T": 0.35}, "QUAL": {"M": 0.95, "T": 0.15},
    "^TNX": {"R": -2.0}, "CL=F": {"O": 1.0}, "BZ=F": {"O": 0.95},
    "XOM": {"M": 0.7, "E": 1.0, "O": 0.5}, "DAL": {"M": 1.3, "O": -0.7}, "NEE": {"M": 0.45, "R": 0.55},
    "SPY": {"M": 1.0}, "VOO": {"M": 1.0}, "VFIAX": {"M": 1.0}, "^GSPC": {"M": 1.0},
    "QQQ": {"M": 1.1, "T": 0.9}, "XLK": {"M": 1.1, "T": 1.2}, "SOXX": {"M": 1.3, "T": 0.9, "S": 1.0},
    "IWM": {"M": 1.15, "F": 0.4, "R": 0.2}, "XLF": {"M": 1.05, "F": 1.0, "R": -0.25}, "XLE": {"M": 0.75, "E": 1.0, "O": 0.45},
    "USO": {"O": 1.0, "M": 0.15}, "TLT": {"R": 1.0, "M": -0.1}, "IEF": {"R": 0.5}, "HYG": {"M": 0.3, "R": 0.3},
    "UUP": {"D": 1.0}, "GLD": {"G": 1.0, "D": -0.4, "R": 0.2}, "ITA": {"M": 0.9, "O": 0.05}, "EIS": {"M": 0.6, "I": 1.0, "D": -0.3},
    "XLV": {"M": 0.7}, "XLY": {"M": 1.15, "T": 0.3}, "XLP": {"M": 0.5, "R": 0.15}, "XLI": {"M": 1.0, "O": 0.05},
    "XLU": {"M": 0.45, "R": 0.45}, "XLB": {"M": 1.0, "O": 0.1, "G": 0.2}, "XLRE": {"M": 0.85, "R": 0.55}, "XLC": {"M": 1.0, "T": 0.5},
    "^VIX": {"M": -4.0}, "BTC-USD": {"M": 1.1, "T": 0.6}, "ILS=X": {"I": -0.25, "D": 0.5, "M": -0.1},
    "AAPL": {"M": 1.1, "T": 0.8}, "MSFT": {"M": 1.05, "T": 0.9}, "NVDA": {"M": 1.4, "T": 1.1, "S": 1.5}, "INTC": {"M": 1.1, "T": 0.5, "S": 0.9},
    "GOOGL": {"M": 1.05, "T": 0.8}, "AMZN": {"M": 1.15, "T": 0.8}, "TSLA": {"M": 1.6, "T": 1.0},
    "TEVA": {"M": 0.8, "I": 0.5}, "TEVA.TA": {"M": 0.8, "I": 0.5}, "^TA125.TA": {"M": 0.6, "I": 1.0},
}
_factor_cache: dict = {}
_full_cache: dict = {}


def _factors() -> pd.DataFrame:
    """Cumulative factor levels on every calendar day (so any trading calendar can use them)."""
    today = dt.date.today()
    if _factor_cache.get("day") == today:
        return _factor_cache["f"]
    days = pd.date_range(dt.date(1990, 1, 1), today, freq="D")
    rng = np.random.default_rng(20240613)
    data = {}
    for k, v in FACTOR_VOL.items():
        r = rng.standard_t(4, len(days)) / np.sqrt(2.0) * v / np.sqrt(365)  # fat tails like real markets
        data[k] = np.cumsum(r)
    f = pd.DataFrame(data, index=[d.date() for d in days])
    _factor_cache.update(day=today, f=f)
    return f


def _loadings(ticker: str) -> dict:
    if ticker in LOADINGS:
        return LOADINGS[ticker]
    rng = np.random.default_rng(_seed(ticker) + 7)
    load = {"M": float(rng.uniform(0.7, 1.5)), "T": float(rng.uniform(0.0, 1.0))}
    if rng.uniform() < 0.25:
        load["S"] = float(rng.uniform(0.3, 1.2))
    return load


def _seed(ticker: str) -> int:
    return int(hashlib.sha256(ticker.encode()).hexdigest()[:8], 16)


class SyntheticProvider:
    def _spec(self, ticker: str):
        import os

        if ticker in os.environ.get("SYNTHETIC_MISSING", "").split(","):
            return None  # simulate a ticker Yahoo stops returning
        if ticker in CATALOG:
            return CATALOG[ticker]
        if re.match(r"^[A-Z]{1,5}(-[A-Z])?$", ticker):  # any plausible US ticker gets a made-up walk
            rng = np.random.default_rng(_seed(ticker))
            return (ticker, "NMS", "EQUITY", float(rng.uniform(20, 400)), float(rng.uniform(-0.05, 0.25)), float(rng.uniform(0.2, 0.5)), "2015-01-02", 0.0)
        return None

    def _full(self, ticker: str) -> pd.DataFrame:
        key = (ticker, dt.date.today(), os.environ.get("SYNTHETIC_MISSING", ""))
        hit = _full_cache.get(key)
        if hit is None:
            hit = _full_cache[key] = self._build(ticker)
        return hit.copy()

    def _build(self, ticker: str) -> pd.DataFrame:
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
        dates = [d.date() for d in days]
        load = _loadings(ticker)
        sys_var = sum((b * FACTOR_VOL[k]) ** 2 for k, b in load.items())
        idio = np.sqrt(max(sigma**2 - sys_var, (0.3 * sigma) ** 2))
        f = _factors().reindex(dates)
        common = sum(b * np.diff(f[k].to_numpy(), prepend=f[k].iloc[0]) for k, b in load.items())
        r = rng.normal(mu / 252 - 0.5 * sigma**2 / 252, idio / np.sqrt(252), n) + common
        close = p0 * np.exp(np.cumsum(r))
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

    def fund_sectors(self, ticker: str) -> tuple[dict[str, float], str]:
        if ticker in ("IBIT", "ETHA"):
            return {}, "Digital Assets"
        if ticker in ("XLK", "SOXX"):
            return {"technology": 1.0}, "Technology"
        if ticker == "XLI":
            return {"industrials": 0.98, "technology": 0.02}, "Industrials"
        if ticker in ("QQQ",):
            return {"technology": 0.5, "communication_services": 0.16, "consumer_cyclical": 0.14, "healthcare": 0.06,
                    "consumer_defensive": 0.06, "industrials": 0.05, "utilities": 0.01, "basic_materials": 0.02}, "Large Growth"
        if ticker in ("SPY", "VOO", "SPMO", "VFIAX"):
            return {"technology": 0.32, "financial_services": 0.13, "healthcare": 0.11, "consumer_cyclical": 0.1,
                    "communication_services": 0.09, "industrials": 0.08, "consumer_defensive": 0.06, "energy": 0.03,
                    "utilities": 0.02, "realestate": 0.02, "basic_materials": 0.02}, "Large Blend"
        return {}, ""

    def earnings_dates(self, ticker: str) -> list[tuple[dt.date, str]]:
        """Four made-up report dates a year (late Jan/Apr/Jul/Oct), from 2015 to a quarter ahead."""
        spec = self._spec(ticker)
        if spec is None or spec[2] not in ("EQUITY",):
            return []
        rng = np.random.default_rng(_seed(ticker) + 11)
        offset = int(rng.integers(0, 14))
        timing = "after_close" if rng.uniform() < 0.6 else "before_open"
        out = []
        horizon = dt.date.today() + dt.timedelta(days=100)
        for y in range(2015, horizon.year + 1):
            for m in (1, 4, 7, 10):
                d = dt.date(y, m, 18) + dt.timedelta(days=offset)
                while d.weekday() >= 5:
                    d += dt.timedelta(days=1)
                if d <= horizon:
                    out.append((d, timing))
        return out

    def recent_bars(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        start = dt.date.today() - dt.timedelta(days=6)
        return {t: self.history(t, start) for t in tickers}

    def recent_closes(self, tickers: list[str]) -> dict[str, list[tuple[dt.date, float]]]:
        out = {}
        for t in tickers:
            df = self._full(t).tail(5)
            out[t] = [(d, float(v)) for d, v in df["close"].items()]
        return out
