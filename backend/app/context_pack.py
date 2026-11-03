"""The context pack: what the AI models are given, so they rely on our data rather than memory.

Shared by the scenario debate and the stock council. It holds:
- a market snapshot (level, 1-month and 1-year change, 1-year range, as-of date per instrument);
- the verified analog library: past windows with the real factor moves measured from our prices;
- the family's holdings as tickers, combined weights and factor betas (no names, no amounts);
- per-stock data packs for the tickers asked for.

Stock performance only: every move is in the instrument's own currency.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json

from sqlmodel import Session

from . import privacy, stock_pack
from .market.cache import NoPriceData, load_history
from .scenario_library import EVENTS

MARKET = [
    ("^GSPC", "S&P 500", "index"), ("^NDX", "Nasdaq 100", "index"), ("^VIX", "VIX (fear index)", "index"),
    ("BZ=F", "Brent crude", "USD per barrel"), ("CL=F", "WTI crude", "USD per barrel"),
    ("^TNX", "US 10-year Treasury yield", "percent"), ("GC=F", "Gold", "USD per ounce"), ("DX-Y.NYB", "US dollar index", "index"),
]
BASIS = ("Stock performance only, currency effects excluded: every move is in the instrument's own currency. "
         "Holdings are tickers with combined family weights; no names or amounts.")


def _r(x, n=4):
    return None if x is None else round(float(x), n)


def market_snapshot(session: Session, today: dt.date | None = None) -> list[dict]:
    today = today or dt.date.today()
    out = []
    for sym, name, unit in MARKET:
        row = {"symbol": sym, "name": name, "unit": unit}
        try:
            c = load_history(session, sym)["close"]
            c = c[c.index <= today]
        except NoPriceData:
            c = None
        if c is None or c.empty:
            out.append({**row, "as_of": None, "level": None, "missing": True})
            continue
        as_of, level = c.index[-1], float(c.iloc[-1])

        def ago(days):
            b = c[c.index <= as_of - dt.timedelta(days=days)]
            return float(b.iloc[-1]) if not b.empty else None

        m1, y1 = ago(30), ago(365)
        yr = c[c.index >= as_of - dt.timedelta(days=365)]
        row.update({"as_of": as_of.isoformat(), "level": _r(level, 2),
                    "low_1y": _r(yr.min(), 2), "high_1y": _r(yr.max(), 2)})
        if sym == "^TNX":  # a yield: change in percentage points
            row["change_1m_pts"] = _r(level - m1, 2) if m1 is not None else None
            row["change_1y_pts"] = _r(level - y1, 2) if y1 is not None else None
        else:
            row["change_1m"] = _r(level / m1 - 1) if m1 else None
            row["change_1y"] = _r(level / y1 - 1) if y1 else None
        out.append(row)
    return out


def analog_library(session: Session) -> list[dict]:
    """Past windows with the real factor moves (percent) measured from our price data."""
    from .factors import BASE_IDS
    from .specs import factor_move, window_move

    out = []
    for e in EVENTS:
        s, en = dt.date.fromisoformat(e["start"]), dt.date.fromisoformat(e["end"])
        moves = {f: _r(factor_move(session, f, s, en), 2) for f in BASE_IDS}
        idx = {"sp500": _r(window_move(session, "^GSPC", s, en), 2), "nasdaq100": _r(window_move(session, "^NDX", s, en), 2),
               "brent": _r(window_move(session, "BZ=F", s, en), 2), "vix": _r(window_move(session, "^VIX", s, en), 2)}
        out.append({"id": e["id"], "name": e["name"], "start": e["start"], "end": e["end"], "tags": e.get("tags", []),
                    "factor_moves_pct": moves, "market_moves_pct": idx,
                    "verified": any(v is not None for v in moves.values()),
                    "missing": [k for k, v in {**moves, **idx}.items() if v is None]})
    return out


def holdings(session: Session) -> dict:
    """Combined family holdings: ticker, weight of the family total, factor betas. No amounts."""
    from . import factors
    from .scenarios import books

    _ports, fam = books(session, None)
    if not fam.value or not fam.holdings:
        return {"holdings": [], "cash_weight": None, "family_betas": {}}
    tickers = [h.symbol for h in fam.holdings]
    try:
        ex = factors.exposures(session, tickers)
    except Exception:
        ex = {}
    rows = []
    for h in fam.holdings:
        loc = (ex.get(h.symbol) or {}).get("local") or {}
        rows.append({"ticker": h.symbol, "weight": _r(h.value / fam.value),
                     "betas": {k: _r(v, 2) for k, v in (loc.get("betas") or {}).items()} or None,
                     "r2": _r(loc.get("r2"), 2)})
    fam_betas = factors.aggregate([(r["weight"], r["betas"]) for r in rows if r["betas"]])
    cash = 1 - sum(r["weight"] for r in rows)
    return {"holdings": rows, "cash_weight": _r(max(0.0, cash)),
            "family_betas": {k: _r(v, 2) for k, v in fam_betas.items()}}


def build(session: Session, tickers: list[str] | None = None, today: dt.date | None = None,
          include_analogs: bool = True) -> dict:
    today = today or dt.date.today()
    pack = {"pack_version": 1, "today": today.isoformat(), "basis": BASIS,
            "market": market_snapshot(session, today)}
    if include_analogs:
        pack["analogs"] = analog_library(session)
    pack["family"] = holdings(session)
    pack["stocks"] = [stock_pack.build(session, t, today) for t in (tickers or [])]
    return pack


def redacted_view(session: Session, pack: dict) -> dict:
    """The pack exactly as it would be sent, with the privacy check and a hash."""
    problems = privacy.scan(session, pack)
    body = json.dumps(pack, sort_keys=True, ensure_ascii=False)
    f = privacy.forbidden(session)
    return {"pack": pack, "privacy": {"ok": not problems, "problems": problems,
                                      "checked_names": len(f.terms), "checked_amounts": len(f.amounts)},
            "hash": hashlib.sha256(body.encode()).hexdigest(), "bytes": len(body.encode())}
