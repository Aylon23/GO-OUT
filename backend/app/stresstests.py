"""Stress tests on today's holdings, the reverse stress test, and the family risk summary.
Stock performance only: every return is in the security's own trading currency; currency effects are excluded.

Stress test: each holding's real move over a past window (last close on/before the start to the last close
on/before the end), in its own currency. A holding without prices then is estimated from its factor betas times the
factors' real moves over the window, and labeled "estimated".
Reverse stress: the most likely combination of factor moves (smallest in standard deviations, using the factors'
historical covariance over a month) that costs the family a given share: f = L * S b / (b'S b).
Risk summary: historical 1-week and 1-month 95% loss (VaR) and the average loss beyond it (CVaR) for today's
holdings, each holding's share of risk vs its share of value, and the effective number of independent bets
(Meucci: exponential entropy of the variance spread over principal components).
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd
from sqlmodel import Session

from . import factors, services
from .market.cache import NoPriceData, load_history
from .scenarios import Book, books
from .specs import factor_move

STRESSES = [
    {"id": "gfc08", "name": "2008 financial crisis", "start": "2008-09-15", "end": "2008-11-20"},
    {"id": "covid20", "name": "Covid crash", "start": "2020-02-19", "end": "2020-03-23"},
    {"id": "rates22", "name": "2022 rate shock", "start": "2022-01-03", "end": "2022-10-12"},
    {"id": "q4_18", "name": "Q4 2018 sell-off", "start": "2018-10-03", "end": "2018-12-24"},
    {"id": "oct7_23", "name": "7 October 2023 week", "start": "2023-10-06", "end": "2023-10-13"},
    {"id": "tariffs25", "name": "April 2025 tariff shock", "start": "2025-04-02", "end": "2025-04-08"},
]
MONTH_WEEKS = 52 / 12


def _to_ils(session: Session) -> float:
    return 1.0  # amounts are already in the display currency


def local_prices(session: Session, symbol: str) -> pd.Series | None:
    """Daily total-return prices in the security's own trading currency (stock performance only)."""
    try:
        df = load_history(session, symbol)
    except NoPriceData:
        return None
    return df["adj_close"].astype(float) if df is not None and len(df) else None


def window_move(p: pd.Series | None, start: dt.date, end: dt.date) -> tuple[float, dt.date, dt.date] | None:
    if p is None:
        return None
    a, b = p[p.index <= start], p[p.index <= end]
    if not len(a) or not len(b) or (start - a.index[-1]).days > 6 or b.index[-1] <= a.index[-1] or a.iloc[-1] <= 0:
        return None
    return float(b.iloc[-1] / a.iloc[-1] - 1), a.index[-1], b.index[-1]


def run_stress(session: Session, st: dict, ports: list[Book], family: Book, exp: dict, to_amt: float) -> dict:
    s, e = dt.date.fromisoformat(st["start"]), dt.date.fromisoformat(st["end"])
    fmoves = {f: (m / 100 if (m := factor_move(session, f, s, e)) is not None else None) for f in factors.BASE_IDS}
    smoves = {x: factor_move(session, x, s, e) for x in factors.SECTOR_ETFS}
    spy = window_move(local_prices(session, "SPY"), s, e)
    spy_usd = factor_move(session, "market", s, e)
    rows = {}
    for h in family.holdings:
        w = window_move(local_prices(session, h.symbol), s, e)
        if w is not None:
            rows[h.symbol] = {"move": w[0], "estimated": False}
            continue
        b = exp[h.symbol]["local"]["betas"]
        known = [k for k, v in fmoves.items() if v is not None]
        if "market" not in known:
            rows[h.symbol] = {"move": None, "estimated": True}
            continue
        mv = sum(b.get(k, 0.0) * fmoves[k] for k in known)
        for k, v in b.items():
            if k.startswith("sector_") and smoves.get(k[7:]) is not None:
                mv += v * (smoves[k[7:]] / 100 - fmoves["market"])
        rows[h.symbol] = {"move": mv, "estimated": True}

    def book(bk: Book) -> dict:
        tot = est = covered = 0.0
        for h in bk.holdings:
            r = rows[h.symbol]
            if r["move"] is None:
                continue
            tot += r["move"] * h.value
            covered += h.value
            est += h.value if r["estimated"] else 0.0
        ok = covered > 0
        return {"id": bk.pid, "name": "Family" if bk.pid is None else bk.name, "impact": tot / bk.value if ok else None,
                "change_amt": tot * to_amt if ok else None, "estimated_share": est / bk.value if ok else None,
                "missing_share": 1 - covered / max(sum(h.value for h in bk.holdings), 1e-9)}

    worst = sorted([(h.symbol, rows[h.symbol]) for h in family.holdings if rows[h.symbol]["move"] is not None],
                   key=lambda x: x[1]["move"] * next(y.value for y in family.holdings if y.symbol == x[0]))[:3]
    return {**st, "data": spy is not None, "used_start": spy[1].isoformat() if spy else None, "used_end": spy[2].isoformat() if spy else None,
            "sp500_usd": spy_usd / 100 if spy_usd is not None else None, "sp500_amt": spy[0] if spy else None,
            "factor_moves": {k: v for k, v in fmoves.items() if v is not None},
            "family": book(family), "portfolios": [book(b) for b in ports],
            "worst": [{"symbol": s_, "move": r["move"], "estimated": r["estimated"]} for s_, r in worst]}


def stress_suite(session: Session) -> dict:
    ports, family = books(session, None)
    if not family.holdings:
        return {"empty": True}
    exp = factors.exposures(session, [h.symbol for h in family.holdings])
    to_amt = _to_ils(session)
    return {"empty": False, "tests": [run_stress(session, st, ports, family, exp, to_amt) for st in STRESSES], "value_amt": family.value * to_amt}


# ------------------------------------------------------------------ reverse stress

def _family_betas(session: Session, bk: Book, exp: dict) -> np.ndarray:
    base = services.get_settings(session).base_currency
    agg = factors.aggregate([(h.value / bk.value, exp[h.symbol]["local"]["betas"]) for h in bk.holdings])
    return np.array([agg.get(k, 0.0) for k in factors.BASE_IDS])


def reverse(session: Session, losses: tuple[float, ...] = (-0.10, -0.20), stress: dict | None = None) -> dict:
    ports, family = books(session, None)
    if not family.holdings:
        return {"empty": True}
    exp = factors.exposures(session, [h.symbol for h in family.holdings])
    F = factors.factor_returns(session, dt.date.today())
    cols = [c for c in factors.BASE_IDS if c in F.columns]
    S = F[cols].dropna().cov().to_numpy() * MONTH_WEEKS
    b_all = _family_betas(session, family, exp)
    b = np.array([b_all[factors.BASE_IDS.index(c)] for c in cols])
    var = float(b @ S @ b)
    sd = np.sqrt(np.diag(S))
    stress = stress or stress_suite(session)
    out = []
    for L in losses:
        f = L * (S @ b) / var
        sigmas = abs(L) / math.sqrt(var)
        order = np.argsort(-np.abs(f / sd))
        top = [{"factor": cols[i], "move": float(f[i]), "sigmas": float(f[i] / sd[i])} for i in order if abs(f[i] / sd[i]) >= 0.25][:4]
        # closest past stress: most similar direction of factor moves
        best, best_cos = None, -2.0
        for t in stress.get("tests", []):
            v = np.array([t["factor_moves"].get(c, np.nan) for c in cols])
            ok = ~np.isnan(v)
            if ok.sum() < 3:
                continue
            a_, c_ = f[ok] / sd[ok], v[ok] / sd[ok]
            cos = float(a_ @ c_ / (np.linalg.norm(a_) * np.linalg.norm(c_) + 1e-12))
            if cos > best_cos:
                best, best_cos = t, cos
        names = {fd["id"]: fd["label"] for fd in factors.FACTORS}
        parts = ", ".join(f"{names[t['factor']]} {t['move'] * 100:+.1f}%" for t in top)
        sentence = (f"A month that costs the family {abs(L):.0%} most likely looks like: {parts}. "
                    f"That is about {sigmas:.1f} standard deviations of a normal month.")
        out.append({"loss": L, "moves": {c: float(f[i]) for i, c in enumerate(cols)}, "top": top, "sigmas": sigmas, "sentence": sentence,
                    "forward": float(b @ f), "closest": {"id": best["id"], "name": best["name"], "family": best["family"]["impact"], "similarity": best_cos} if best else None})
    return {"empty": False, "results": out, "monthly_sd": math.sqrt(var), "betas": {c: float(b[i]) for i, c in enumerate(cols)}}


# ------------------------------------------------------------------ risk summary

def risk_summary(session: Session) -> dict:
    ports, family = books(session, None)
    if not family.holdings:
        return {"empty": True}
    to_amt = _to_ils(session)
    today = dt.date.today()
    R = {}
    for h in family.holdings:
        r = factors.holding_returns(session, h.symbol, today).get("local")
        if r is not None:
            R[h.symbol] = r
    df = pd.DataFrame(R)
    df["_cash"] = 0.0  # cash doesn't move
    df = df.dropna(how="all").fillna(0.0)

    def summary(bk: Book) -> dict:
        syms = [h.symbol for h in bk.holdings if h.symbol in df.columns]
        w = np.array([next(h.value for h in bk.holdings if h.symbol == s) / bk.value for s in syms])
        cash_w = max(0.0, 1 - sum(h.value for h in bk.holdings) / bk.value)
        cols = syms + (["_cash"] if "_cash" in df.columns and cash_w > 0 else [])
        wv = np.append(w, cash_w) if len(cols) > len(syms) else w
        X = df[cols].to_numpy()
        port = X @ wv
        month = pd.Series(1 + port).rolling(4).apply(np.prod, raw=True).dropna().to_numpy() - 1
        value_amt = bk.value * to_amt

        def var_cvar(x):
            q = float(np.quantile(x, 0.05))
            return -q, float(-x[x <= q].mean())

        v1, c1 = var_cvar(port)
        vm, cm = var_cvar(month) if len(month) > 20 else (None, None)
        S = np.cov(X, rowvar=False)
        S = np.atleast_2d(S)
        sigma2 = float(wv @ S @ wv)
        rc = wv * (S @ wv) / sigma2 if sigma2 > 0 else np.zeros_like(wv)
        vals, vecs = np.linalg.eigh(S)
        vals = np.maximum(vals, 0)
        e = vecs.T @ wv
        contrib = e**2 * vals
        p = contrib / contrib.sum() if contrib.sum() > 0 else contrib
        p = p[p > 1e-12]
        enb = float(math.exp(-(p * np.log(p)).sum())) if len(p) else None
        rows = [{"symbol": c if c != "_cash" else "Cash", "value_share": float(wv[i]), "risk_share": float(rc[i])} for i, c in enumerate(cols)]
        rows.sort(key=lambda r: -r["risk_share"])
        return {"id": bk.pid, "name": "Family" if bk.pid is None else bk.name, "weeks": int(len(port)),
                "var_1w": v1, "cvar_1w": c1, "var_1m": vm, "cvar_1m": cm,
                "var_1w_amt": v1 * value_amt, "cvar_1w_amt": c1 * value_amt,
                "var_1m_amt": vm * value_amt if vm is not None else None, "cvar_1m_amt": cm * value_amt if cm is not None else None,
                "holdings": len(syms), "effective_bets": enb, "risk": rows,
                "vol_annual": math.sqrt(max(sigma2, 0) * 52)}

    return {"empty": False, "books": [summary(family)] + [summary(b) for b in ports]}
