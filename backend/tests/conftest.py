import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402
from app.market import cache, provider  # noqa: E402


def frame(rows):
    """rows: list of (date_str, close[, adj_close[, dividend[, split]]])"""
    idx, data = [], []
    for r in rows:
        d = dt.date.fromisoformat(r[0])
        close = float(r[1])
        adj = float(r[2]) if len(r) > 2 and r[2] is not None else close
        div = float(r[3]) if len(r) > 3 else 0.0
        split = float(r[4]) if len(r) > 4 else 0.0
        idx.append(d)
        data.append((close, adj, div, split))
    return pd.DataFrame(data, index=idx, columns=["close", "adj_close", "dividend", "split"])


class FakeProvider:
    """Serves hand-built frames. `history(t, start)` filters by start like Yahoo."""

    def __init__(self, frames=None, splits=None, quotes=None, fail_search=False):
        self.frames = frames or {}
        self.split_map = splits or {}
        self.quotes = quotes or []
        self.fail_search = fail_search
        self.calls = []

    def history(self, ticker, start):
        self.calls.append((ticker, start))
        f = self.frames.get(ticker)
        if f is None:
            return pd.DataFrame(columns=["close", "adj_close", "dividend", "split"])
        return f if start is None else f[f.index >= start]

    def splits(self, ticker):
        return self.split_map.get(ticker, [])

    def search(self, query, max_results=10):
        if self.fail_search:
            raise provider.ProviderError("offline")
        return self.quotes

    def info(self, ticker):
        return {}

    def recent_closes(self, tickers):
        out = {}
        for t in tickers:
            f = self.frames.get(t)
            if f is not None:
                out[t] = [(d, float(v)) for d, v in f["close"].tail(2).items()]
        return out


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    db.set_engine(engine)
    cache.clear_memory_cache()
    with Session(engine) as s:
        yield s
    provider.set_provider(None)
    cache.clear_memory_cache()


@pytest.fixture
def fake(session):
    p = FakeProvider()
    provider.set_provider(p)
    return p


def _fund_sectors(self, ticker):
    return getattr(self, "funds", {}).get(ticker, ({}, ""))


FakeProvider.fund_sectors = _fund_sectors
