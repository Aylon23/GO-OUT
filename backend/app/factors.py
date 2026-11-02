"""Factor model: what each holding, portfolio and the family is exposed to.

Factors are weekly total returns (Friday to Friday) of liquid ETFs:
  market SPY · size IWM−SPY · value IWD−IWF · momentum MTUM−SPY · quality QUAL−SPY · rates TLT ·
  oil USO · dollar UUP · shekel ILS=X (shekels per dollar) · gold GLD · sector: own sector ETF − SPY.
Style and sector factors are measured against the S&P 500 so the market beta keeps its meaning.

Each holding is regressed on the factors over up to 3 years (156 weeks) in its own currency, and
shown two ways:
- "usd": the holding's return in dollars (security level);
- "ils": the holding's return in shekels (portfolio level). For a US stock this is its dollar return
  plus the USD/ILS move, so its shekel beta is its dollar one + 1: when the dollar weakens against
  the shekel, a shekel-based investor in US stocks loses.
The regression is ridge-shrunk toward the holding's sector ETF (market 1, own sector 1). Under a year of history the pull is strong and the holding is marked
"short history". Nothing after the estimation date is ever used.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import threading

import numpy as np
import pandas as pd
from sqlmodel import Session, select

from .engine import stress
from .market.cache import NoPriceData, currency_for, load_history
from .models import FactorExposure
from .scenario_library import SECTOR_PROXY

log = logging.getLogger(__name__)

WINDOW_WEEKS = 156
SHORT_WEEKS = 52
MIN_WEEKS = 8
LAMBDA = 1.0  # strength of the prior (0 = plain least squares)
# How far a holding's beta may plausibly sit from its sector ETF's (prior standard deviation).
# Tight where a factor moves little week to week, so noise can't masquerade as exposure.
PRIOR_SD = {"market": 0.5, "size": 0.5, "value": 0.5, "momentum": 0.5, "quality": 0.5, "rates": 0.3, "oil": 0.25,
            "dollar": 0.3, "shekel": 0.2, "gold": 0.25}
SECTOR_SD = 0.5
SHORT_PULL = 0.25  # under a year of history the prior is up to 4x tighter (16x the weight), easing off by one year
LOW_R2 = 0.3

# id, label, Hebrew label, what it is, plain-language shock (cause, size)
FACTORS: list[dict] = [
    {"id": "market", "label": "Market", "he": "שוק", "build": ("SPY",), "tip": "The S&P 500 (SPY). A beta of 1.2 means about 1.2% for every 1% the S&P moves.",
     "when": "If the S&P 500 falls 10%", "shock": -0.10},
    {"id": "size", "label": "Size", "he": "גודל", "build": ("IWM", "SPY"), "tip": "Small companies (IWM) minus the S&P 500. Positive = leans to small caps.",
     "when": "If small caps lag the S&P 500 by 5%", "shock": -0.05},
    {"id": "value", "label": "Value", "he": "ערך", "build": ("IWD", "IWF"), "tip": "Value stocks (IWD) minus growth stocks (IWF). Positive = leans to value, negative = growth.",
     "when": "If growth beats value by 5%", "shock": -0.05},
    {"id": "momentum", "label": "Momentum", "he": "מומנטום", "build": ("MTUM", "SPY"), "tip": "Recent winners (MTUM) minus the S&P 500. Positive = holds last year's winners.",
     "when": "If recent winners lag the market by 5%", "shock": -0.05},
    {"id": "quality", "label": "Quality", "he": "איכות", "build": ("QUAL", "SPY"), "tip": "Profitable, low-debt companies (QUAL) minus the S&P 500.",
     "when": "If quality companies lag the market by 5%", "shock": -0.05},
    {"id": "rates", "label": "Rates", "he": "ריבית", "build": ("TLT",), "tip": "Long US government bonds (TLT). Positive = gains when rates fall, loses when they rise.",
     "when": "If long bonds fall 10% (long rates up about 0.6%)", "shock": -0.10},
    {"id": "oil", "label": "Oil", "he": "נפט", "build": ("USO",), "tip": "Crude oil (USO). Positive = gains when oil rises; airlines are usually negative.",
     "when": "If oil rises 20%", "shock": 0.20},
    {"id": "dollar", "label": "Dollar", "he": "דולר", "build": ("UUP",), "tip": "The US dollar against major currencies (UUP), apart from its move against the shekel.",
     "when": "If the dollar rises 5% against major currencies", "shock": 0.05},
    {"id": "shekel", "label": "USD/ILS", "he": "דולר-שקל", "build": ("ILS=X",), "tip": "Shekels per dollar. For an Israeli investor in US stocks this is a big part of return and risk: when the dollar weakens against the shekel, US holdings lose value in shekels.",
     "when": "If the dollar weakens 5% against the shekel", "shock": -0.05},
    {"id": "gold", "label": "Gold", "he": "זהב", "build": ("GLD",), "tip": "Gold (GLD).",
     "when": "If gold rises 10%", "shock": 0.10},
]
BASE_IDS = [f["id"] for f in FACTORS]
SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"]
SECTOR_LABEL = {"XLK": "Technology", "XLF": "Financials", "XLE": "Energy", "XLV": "Healthcare", "XLI": "Industrials",
                "XLY": "Consumer discretionary", "XLP": "Consumer staples", "XLU": "Utilities", "XLB": "Materials",
                "XLRE": "Real estate", "XLC": "Communication"}
ALL_IDS = BASE_IDS + [f"sector_{e}" for e in SECTOR_ETFS]
FACTOR_SYMBOLS = sorted({s for f in FACTORS for s in f["build"]} | set(SECTOR_ETFS))


# ------------------------------------------------------------------ data

def _prices(session: Session, symbol: str, as_of: dt.date) -> pd.Series | None:
    try:
        df = load_history(session, symbol)
    except NoPriceData:
        return None
    col = "close" if symbol == "ILS=X" else "adj_close"
    p = df[col].astype(float)
    p = p[p.index <= as_of]  # never look past the estimation date
    return p if len(p) >= 10 else None


_factor_cache: dict[tuple, pd.DataFrame] = {}


def factor_returns(session: Session, as_of: dt.date) -> pd.DataFrame:
    """Weekly factor returns up to as_of. Columns: ALL_IDS (missing ones dropped)."""
    from . import services

    key = (services._rev, as_of)
    if key in _factor_cache:
        return _factor_cache[key]
    raw = {s: p for s in FACTOR_SYMBOLS if (p := _prices(session, s, as_of)) is not None}
    W = stress.weekly_returns(raw)
    out = {}
    for f in FACTORS:
        b = f["build"]
        if all(s in W for s in b):
            out[f["id"]] = W[b[0]] - (W[b[1]] if len(b) > 1 else 0.0)
    for e in SECTOR_ETFS:
        if e in W and "SPY" in W:
            out[f"sector_{e}"] = W[e] - W["SPY"]
    F = pd.DataFrame(out).dropna(how="all")
    if not F.empty:
        cut = pd.Timestamp(as_of) - pd.Timedelta(weeks=WINDOW_WEEKS + 1)
        F = F[F.index > cut]
    if len(_factor_cache) > 16:
        _factor_cache.clear()
    _factor_cache[key] = F
    return F


def holding_returns(session: Session, symbol: str, as_of: dt.date) -> dict[str, pd.Series]:
    """Weekly returns of one holding in dollars and in shekels."""
    p = _prices(session, symbol, as_of)
    fx = _prices(session, "ILS=X", as_of)
    if p is None or fx is None:
        return {}
    ccy = currency_for(session, symbol)
    cal = sorted(set(p.index) | set(fx.index))
    p_c = p.reindex(cal).ffill()
    fx_c = fx.reindex(cal).ffill().bfill()
    keep = p_c.notna()
    p_c, fx_c = p_c[keep], fx_c[keep]
    usd = p_c / fx_c if ccy == "ILS" else p_c
    ils = p_c if ccy == "ILS" else p_c * fx_c
    W = stress.weekly_returns({"usd": usd, "ils": ils})
    cut = pd.Timestamp(as_of) - pd.Timedelta(weeks=WINDOW_WEEKS + 1)
    W = W[W.index > cut]
    return {"usd": W["usd"].dropna(), "ils": W["ils"].dropna()} if "usd" in W else {}


# ------------------------------------------------------------------ estimation

def sector_of(session: Session, symbol: str) -> str | None:
    from .models import SymbolMeta

    if symbol in SECTOR_ETFS:
        return symbol
    m = session.get(SymbolMeta, symbol)
    if m is None or (m.type or "") in ("ETF", "Fund"):
        return None
    return SECTOR_PROXY.get(m.sector or "")


def prior(view: str, ccy: str, sector: str | None, cols: list[str]) -> np.ndarray:
    """What the holding is assumed to look like before its own data: its sector ETF, measured in
    the holding's own currency (so no currency effect)."""
    b = dict.fromkeys(cols, 0.0)
    b["market"] = 1.0
    if sector and f"sector_{sector}" in b:
        b[f"sector_{sector}"] = 1.0
    return np.array([b[c] for c in cols])


def fit(y: pd.Series, X: pd.DataFrame, b0: np.ndarray) -> dict:
    """Bayesian ridge regression of y on X, shrunk toward b0.

    Each factor's prior width comes from PRIOR_SD, weighed against the holding's own residual
    noise: beta = (X'X/s2 + P)^-1 (X'y/s2 + P b0), P = LAMBDA / sd^2. With less history the data
    carries less weight and, under a year, the prior is also tightened (SHORT_PULL)."""
    df = pd.concat([y.rename("_y"), X], axis=1, join="inner").dropna()
    n = len(df)
    cols = list(X.columns)
    if n < MIN_WEEKS:
        return {"betas": dict(zip(cols, b0.tolist())), "r2": None, "resid_vol": None, "weeks": n, "short_history": True}
    Xv = df[cols].to_numpy()
    yv = df["_y"].to_numpy()
    Xc = Xv - Xv.mean(axis=0)
    yc = yv - yv.mean()
    XtX = Xc.T @ Xc
    # residual noise from a lightly regularized first pass
    first = np.linalg.solve(XtX + 1e-6 * np.trace(XtX) * np.eye(len(cols)), Xc.T @ yc)
    dof = max(n - len(cols) - 1, 1)
    s2 = max(float(np.sum((yc - Xc @ first) ** 2)) / dof, 1e-10)
    sd = np.array([PRIOR_SD.get(c, SECTOR_SD) for c in cols])
    if n < SHORT_WEEKS:
        sd = sd * (SHORT_PULL + (1 - SHORT_PULL) * n / SHORT_WEEKS)
    P = np.diag(LAMBDA / sd**2)
    beta = np.linalg.solve(XtX / s2 + P, Xc.T @ yc / s2 + P @ b0)
    resid = yc - Xc @ beta
    vy = float(np.var(yc))
    r2 = 1.0 - float(np.var(resid)) / vy if vy > 0 else None
    return {"betas": dict(zip(cols, map(float, beta))), "r2": r2, "resid_vol": float(np.std(resid, ddof=1) * math.sqrt(52)),
            "weeks": n, "short_history": n < SHORT_WEEKS}


def estimate(session: Session, symbol: str, as_of: dt.date) -> dict[str, dict]:
    """{"usd": {...}, "ils": {...}} for one symbol, using only data up to as_of.

    The holding is regressed once, in its own currency. The other view follows exactly from
    r_ils = r_usd + r_usdils (to first order): a dollar asset's shekel beta is its dollar
    shekel beta + 1, a shekel asset's dollar shekel beta is its shekel one − 1. Fitting both
    views separately would only add noise to a relationship that is an identity."""
    F = factor_returns(session, as_of)
    sector = sector_of(session, symbol)
    cols = [c for c in BASE_IDS if c in F.columns]
    if sector and f"sector_{sector}" in F.columns and symbol != sector:
        cols.append(f"sector_{sector}")
    ccy = currency_for(session, symbol)
    native = "ils" if ccy == "ILS" else "usd"
    if not cols:  # no factor prices at all (offline, first start): no exposures yet
        empty = {"betas": {}, "r2": None, "resid_vol": None, "weeks": 0, "short_history": True, "sector": sector or ""}
        return {"usd": dict(empty), "ils": dict(empty, betas={"shekel": 1.0} if native == "usd" else {})}
    other = "usd" if native == "ils" else "ils"
    R = holding_returns(session, symbol, as_of)
    b0 = prior(native, ccy, sector, cols)
    y = R.get(native)
    res = fit(y, F[cols], b0) if y is not None else {"betas": dict(zip(cols, b0.tolist())), "r2": None, "resid_vol": None, "weeks": 0, "short_history": True}
    res["sector"] = sector or ""
    derived = {**res, "betas": dict(res["betas"])}
    if "shekel" in derived["betas"]:
        derived["betas"]["shekel"] += 1.0 if other == "ils" else -1.0
    return {native: res, other: derived}


def exposures(session: Session, symbols: list[str], as_of: dt.date | None = None, refresh: bool = False) -> dict[str, dict]:
    """Cached exposures (estimated today) for symbols; missing or stale ones are computed now."""
    from .services import ensure_meta_info

    as_of = as_of or dt.date.today()
    ensure_meta_info(session, [s for s in symbols if s not in SECTOR_ETFS])
    out: dict[str, dict] = {}
    rows = session.exec(select(FactorExposure).where(FactorExposure.symbol.in_(symbols))).all() if symbols else []
    have = {(r.symbol, r.view): r for r in rows}
    for s in symbols:
        u, i = have.get((s, "usd")), have.get((s, "ils"))
        if not refresh and u and i and u.as_of == as_of:
            out[s] = {v: _row_dict(r) for v, r in (("usd", u), ("ils", i))}
            continue
        est = estimate(session, s, as_of)
        for view, res in est.items():
            row = have.get((s, view)) or FactorExposure(symbol=s, view=view, as_of=as_of)
            row.betas = json.dumps(res["betas"])
            row.r2, row.resid_vol, row.weeks, row.short_history, row.sector = res["r2"], res["resid_vol"], res["weeks"], res["short_history"], res["sector"]
            row.as_of = as_of
            row.estimated_at = dt.datetime.now(dt.timezone.utc)
            session.add(row)
        session.commit()
        out[s] = est
    return out


def _row_dict(r: FactorExposure) -> dict:
    return {"betas": json.loads(r.betas), "r2": r.r2, "resid_vol": r.resid_vol, "weeks": r.weeks,
            "short_history": r.short_history, "sector": r.sector}


# ------------------------------------------------------------------ portfolios

def aggregate(holdings: list[tuple[float, dict]], cash_weight: float, cash_shekel_beta: float) -> dict[str, float]:
    """Value-weighted betas. holdings: (weight, betas). Cash has no exposure except its currency."""
    out: dict[str, float] = {}
    for w, b in holdings:
        for k, v in b.items():
            out[k] = out.get(k, 0.0) + w * v
    if cash_weight:
        out["shekel"] = out.get("shekel", 0.0) + cash_weight * cash_shekel_beta
    return out


def payload(session: Session, as_of: dt.date | None = None) -> dict:
    """Exposures for the family and each portfolio, in shekels, with per-holding detail."""
    from . import services
    from .scenarios import books

    ports, family = books(session, None)
    base = services.get_settings(session).base_currency
    syms = [h.symbol for h in family.holdings]
    exp = exposures(session, syms, as_of)
    cash_beta = 1.0 if base == "USD" else 0.0  # cash in dollars moves with USD/ILS when counted in shekels

    def book(b) -> dict:
        rows = []
        for h in b.holdings:
            e = exp[h.symbol]
            w = h.value / b.value
            r2 = e["ils"]["r2"]
            rows.append({"symbol": h.symbol, "name": h.name, "weight": w, "value": h.value,
                         "betas": e["ils"]["betas"], "betas_usd": e["usd"]["betas"], "r2": r2, "r2_usd": e["usd"]["r2"],
                         "resid_vol": e["ils"]["resid_vol"], "weeks": e["ils"]["weeks"], "short_history": e["ils"]["short_history"],
                         "low_confidence": r2 is None or r2 < LOW_R2, "sector": e["ils"]["sector"]})
        cash_w = max(0.0, (b.value - sum(h.value for h in b.holdings)) / b.value) if b.value else 0.0
        betas = aggregate([(r["weight"], r["betas"]) for r in rows], cash_w, cash_beta)
        return {"id": b.pid, "name": b.name, "value": b.value, "cash_weight": cash_w, "betas": betas,
                "holdings": sorted(rows, key=lambda r: -r["value"])}

    out_books = [book(family)] + [book(b) for b in ports] if family.holdings else []
    if out_books:
        out_books[0]["name"] = "Family" if len(ports) > 1 else ports[0].name
    return {"as_of": (as_of or dt.date.today()).isoformat(), "window_weeks": WINDOW_WEEKS, "currency": "ILS",
            "factors": [{k: f[k] for k in ("id", "label", "he", "tip", "when", "shock")} for f in FACTORS],
            "sectors": {f"sector_{e}": SECTOR_LABEL[e] for e in SECTOR_ETFS}, "low_r2": LOW_R2, "books": out_books}


# ------------------------------------------------------------------ nightly

_nightly_started = False


def start_nightly() -> None:
    """Recompute every held symbol's exposures once a day, in the background."""
    global _nightly_started
    if _nightly_started:
        return
    _nightly_started = True

    def loop():
        import time

        from . import db
        from .scenarios import books

        last = None
        while True:
            today = dt.date.today()
            if last != today:
                try:
                    with Session(db.get_engine()) as s:
                        _, fam = books(s, None)
                        exposures(s, [h.symbol for h in fam.holdings], today)
                    last = today
                except Exception as exc:  # retried next hour
                    log.warning("Nightly exposures failed: %s", exc)
            time.sleep(3600)

    threading.Thread(target=loop, name="exposures-nightly", daemon=True).start()
