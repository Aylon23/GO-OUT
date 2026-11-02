"""Impact engine: runs a Scenario Spec (researched, or built from a local-rule sentence) through the
factor model and gives each portfolio and the family a distribution of outcomes, in shekels.

Per branch, a holding moves by sum(beta x factor move), plus its sector ETF's move beyond the market
when the branch overrides that sector (through the holding's sector beta), plus any move given for
the holding itself (company news).
Uncertainty (Monte Carlo, default 10,000 draws, fixed seed):
  1. pick a branch by its probability;
  2. draw every move the branch names from a triangular distribution (low, mid, high), tied together
     with the factors' historical correlations (Gaussian copula) in the branch's regime: sell-off
     weeks for crisis causes (geopolitical, supply, demand), normal weeks otherwise;
  3. add each holding's own noise from its residual volatility, scaled to the horizon.
Factors a branch doesn't name don't move.
"""
from __future__ import annotations

import copy
import datetime as dt
import math
from collections import deque

import numpy as np
import pandas as pd
from sqlmodel import Session

from . import factors, services
from .engine import stress
from .scenarios import Book, Holding, books, replay, universe

N_DRAWS = 10_000
SEED = 7
HORIZON_DAYS = {"1w": 5, "2w": 10, "1m": 21, "3m": 63, "6m": 126, "12m": 252}
CRISIS_CAUSES = {"geopolitical", "supply", "demand"}
_erf = np.vectorize(math.erf, otypes=[float])


def norm_cdf(z: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + _erf(z / math.sqrt(2.0)))


def tri_ppf(u: np.ndarray, low: float, mid: float, high: float) -> np.ndarray:
    """Inverse CDF of the triangular distribution. Degenerate ranges return the mid."""
    if high - low <= 1e-12:
        return np.full_like(u, mid, dtype=float)
    c = (mid - low) / (high - low)
    left = low + np.sqrt(u * (high - low) * (mid - low))
    right = high - np.sqrt((1 - u) * (high - low) * (high - mid))
    return np.where(u < c, left, right)


def regime_of(branch: dict, override: str | None) -> str:
    if override in ("normal", "selloff"):
        return override
    return "selloff" if branch.get("cause") in CRISIS_CAUSES else "normal"


# ------------------------------------------------------------------ editing the transmission chain

def apply_edits(spec: dict, edits: list[dict]) -> dict:
    """Edits: [{branch, factor, mid}]. The edited factor takes the new middle (its range shifts with it);
    every factor downstream of it in the branch's transmission chain is scaled by the same ratio."""
    spec = copy.deepcopy(spec)
    for e in edits or []:
        b = next((x for x in spec["branches"] if x["name"] == e.get("branch")), None)
        if b is None:
            continue
        fid = e["factor"]
        moves = b.setdefault("factor_moves", {})
        old = moves.get(fid)
        new_mid = float(e["mid"])
        if old is None:
            moves[fid] = {"low": new_mid, "mid": new_mid, "high": new_mid}
            continue
        shift = new_mid - old["mid"]
        ratio = new_mid / old["mid"] if abs(old["mid"]) > 1e-9 else None
        moves[fid] = {**old, "low": old["low"] + shift, "mid": new_mid, "high": old["high"] + shift}
        if ratio is None:
            continue
        # walk downstream
        seen, queue = {fid}, deque([fid])
        while queue:
            src = queue.popleft()
            for link in b.get("transmission") or []:
                dst = link.get("to")
                if link.get("from") == src and dst not in seen and dst in moves:
                    m = moves[dst]
                    lo, mi, hi = m["low"] * ratio, m["mid"] * ratio, m["high"] * ratio
                    moves[dst] = {**m, "low": min(lo, hi), "mid": mi, "high": max(lo, hi)}
                    seen.add(dst)
                    queue.append(dst)
    return spec


# ------------------------------------------------------------------ correlations

def _corr(session: Session, cols: list[str], regime: str) -> np.ndarray:
    """Correlation of the named moves (factor ids, 'sector:XLE', 'holding:AAPL') in the regime."""
    k = len(cols)
    if k <= 1:
        return np.eye(k)
    as_of = dt.date.today()
    F = factors.factor_returns(session, as_of)
    data = {}
    for c in cols:
        if c in F.columns:
            data[c] = F[c]
        else:
            sym = c.split(":", 1)[1]
            r = factors.holding_returns(session, sym, as_of).get("usd")
            if r is not None:
                data[c] = r
    df = pd.DataFrame(data)
    if "market" in F.columns:
        df["_m"] = F["market"]
    if regime == "selloff" and "_m" in df:
        cut = df["_m"].quantile(stress.STRESS_QUANTILE)
        df = df[df["_m"] <= cut]
    df = df.drop(columns=["_m"], errors="ignore")
    C = np.eye(k)
    for i, a in enumerate(cols):
        for j, b in enumerate(cols):
            if i < j and a in df and b in df:
                pair = df[[a, b]].dropna()
                if len(pair) >= 10 and pair[a].std() > 0 and pair[b].std() > 0:
                    C[i, j] = C[j, i] = float(np.corrcoef(pair[a], pair[b])[0, 1])
    return stress.nearest_psd(C, 1e-6)


# ------------------------------------------------------------------ the engine

def _fx_now(session: Session) -> float:
    fx = services.fx_series(session)
    return float(fx.iloc[-1]) if len(fx) else 1.0


def simulate(session: Session, spec: dict, syms: list[str], base: str, regime: str | None = None, n: int = N_DRAWS,
             seed: int = SEED, noise: bool = True, exp: dict | None = None, band_scale: float = 1.0) -> dict:
    """Simulated moves (in shekels) of any list of symbols under a spec.
    Returns H (draws x symbols), cash (draws: the move of dollar cash in shekels), and per branch:
    the rows drawn, its middle-case moves and the factor contributions behind them."""
    exp = exp if exp is not None else factors.exposures(session, syms)
    days = HORIZON_DAYS.get(spec.get("horizon", "1m"), 21)
    hyears = days / 252
    cols = factors.ALL_IDS
    B = np.array([[exp[s]["ils"]["betas"].get(c, 0.0) for c in cols] for s in syms]).reshape(len(syms), len(cols))
    resid = np.array([(exp[s]["ils"]["resid_vol"] or 0.3) * math.sqrt(hyears) for s in syms])
    cash_betas = np.array([1.0 if (c == "shekel" and base == "USD") else 0.0 for c in cols])
    rng = np.random.default_rng(seed)
    branches = spec["branches"]
    probs = np.array([b["probability"] for b in branches], dtype=float)
    probs = probs / probs.sum()
    pick = rng.choice(len(branches), size=n, p=probs)
    eps = rng.standard_normal((n, len(syms))) * resid * (band_scale if noise else 0.0)  # shared by every portfolio holding the same stock
    H = np.zeros((n, len(syms)))
    cash = np.zeros(n)
    branch_info = []
    col_idx = {c: i for i, c in enumerate(cols)}
    for bi, br in enumerate(branches):
        rows = np.where(pick == bi)[0]
        reg = regime_of(br, regime)
        names = [("factor", k, r) for k, r in (br.get("factor_moves") or {}).items() if k in col_idx]
        names += [("sector", k, r) for k, r in (br.get("sector_overrides") or {}).items() if f"sector_{k}" in col_idx]
        names += [("holding", k, r) for k, r in (br.get("holding_overrides") or {}).items()]
        keys = [k if t == "factor" else f"{t}:{k}" for t, k, _ in names]
        m = len(rows)
        draws = np.zeros((m, len(names)))
        if m and names:
            R = _corr(session, keys, reg)
            z = rng.standard_normal((m, len(names))) @ np.linalg.cholesky(R).T
            u = np.clip(norm_cdf(z), 1e-9, 1 - 1e-9)
            for j, (_, _, r) in enumerate(names):
                lo = r["mid"] - band_scale * (r["mid"] - r["low"])  # widened when past bands were too narrow
                hi = r["mid"] + band_scale * (r["high"] - r["mid"])
                draws[:, j] = tri_ppf(u[:, j], lo / 100, r["mid"] / 100, hi / 100)
        mid_vec = np.array([r["mid"] / 100 for _, _, r in names])

        def factor_matrix(vals: np.ndarray) -> tuple[np.ndarray, dict]:
            """(rows x factors) moves and {symbol: own move} for holding overrides."""
            Fm = np.zeros((vals.shape[0], len(cols)))
            own = {}
            for j, (t, k, _) in enumerate(names):
                if t == "factor":
                    Fm[:, col_idx[k]] = vals[:, j]
            mkt = Fm[:, col_idx["market"]].copy()
            for j, (t, k, _) in enumerate(names):
                if t == "sector":
                    Fm[:, col_idx[f"sector_{k}"]] = vals[:, j] - mkt  # the sector beyond the market
                elif t == "holding":
                    own[k] = vals[:, j]
            return Fm, own

        def holding_moves(vals: np.ndarray, with_noise: np.ndarray | None) -> np.ndarray:
            Fm, own = factor_matrix(vals)
            H = Fm @ B.T
            if with_noise is not None:
                H = H + with_noise
            for s, v in own.items():
                if s in syms:
                    i = syms.index(s)
                    # company news replaces the holding's own move in its currency; add the shekel effect
                    H[:, i] = v + Fm[:, col_idx["shekel"]] * (1.0 if exp[s]["ils"]["betas"].get("shekel", 0) > 0.5 else 0.0)
            return H

        if m:
            H[rows] = holding_moves(draws, eps[rows])
            cash[rows] = factor_matrix(draws)[0] @ cash_betas
        # the branch's middle case, no noise: for "why" and analog comparisons
        Hmid = holding_moves(mid_vec[None, :], None)[0] if names else np.zeros(len(syms))
        Fmid, _ = factor_matrix(mid_vec[None, :]) if names else (np.zeros((1, len(cols))), {})
        contrib = B * Fmid[0][None, :]  # holdings x factors
        branch_info.append({"name": br["name"], "probability": float(probs[bi]), "cause": br.get("cause"), "regime": reg,
                            "rows": rows, "mid_moves": Hmid, "contrib": contrib, "cash_mid": float(Fmid[0] @ cash_betas),
                            "factor_mid": {cols[j]: float(Fmid[0][j]) for j in range(len(cols)) if abs(Fmid[0][j]) > 1e-12}})
    return {"H": H, "cash": cash, "pick": pick, "branches": branch_info, "cols": cols, "B": B, "days": days}



def run(session: Session, spec: dict, regime: str | None = None, n: int = N_DRAWS, seed: int = SEED,
        edits: list[dict] | None = None, pids: list[int] | None = None, analogs: bool = True, noise: bool = True,
        keep: bool = False) -> dict:
    spec = apply_edits(spec, edits or [])
    ports, family = books(session, pids)
    if not family.holdings:
        return {"empty": True}
    base = services.get_settings(session).base_currency
    to_ils = _fx_now(session) if base == "USD" else 1.0
    syms = [h.symbol for h in family.holdings]
    exp = factors.exposures(session, syms)
    days = HORIZON_DAYS.get(spec.get("horizon", "1m"), 21)

    settings = services.get_settings(session)
    k = float(settings.band_scale or 1.0)
    sim = simulate(session, spec, syms, base, regime=regime, n=n, seed=seed, noise=noise, exp=exp, band_scale=k)
    cols, B = sim["cols"], sim["B"]

    def weights(b: Book) -> tuple[np.ndarray, float]:
        vals = {h.symbol: h.value for h in b.holdings}
        w = np.array([vals.get(s, 0.0) / b.value for s in syms])
        return w, max(0.0, 1 - w.sum())

    book_list = [family] + ports
    W = [weights(b) for b in book_list]
    outcomes = np.column_stack([sim["H"] @ w + cw * sim["cash"] for w, cw in W])
    probs = np.array([i["probability"] for i in sim["branches"]])
    branch_info = sim["branches"]
    branches = spec["branches"]

    def stats(x: np.ndarray, value_ils: float) -> dict:
        if len(x) == 0:
            return {"median": None, "p5": None, "p95": None, "prob_loss": None, "value_ils": value_ils}
        q5, q50, q95 = np.percentile(x, [5, 50, 95])
        return {"median": float(q50), "p5": float(q5), "p95": float(q95), "prob_loss": float(np.mean(x < 0)),
                "median_ils": float(q50 * value_ils), "p5_ils": float(q5 * value_ils), "p95_ils": float(q95 * value_ils),
                "mean": float(np.mean(x)), "value_ils": value_ils}

    vals_ils = [b.value * to_ils for b in book_list]
    labels = [{"id": b.pid, "name": "Family" if b.pid is None else b.name} for b in book_list]
    blended = [{**labels[k], **stats(outcomes[:, k], vals_ils[k])} for k in range(len(book_list))]
    br_out = []
    for info in branch_info:
        rows = info["rows"]
        br_out.append({"name": info["name"], "probability": info["probability"], "cause": info["cause"], "regime": info["regime"],
                       "draws": int(len(rows)),
                       "books": [{**labels[k], **stats(outcomes[rows, k], vals_ils[k]),
                                  "mid": float(info["mid_moves"] @ W[k][0] + W[k][1] * info["cash_mid"])} for k in range(len(book_list))]})

    # per-holding view: probability-weighted middle move and the factors behind it
    exp_move = sum(i["probability"] * i["mid_moves"] for i in branch_info)
    exp_contrib = sum(i["probability"] * i["contrib"] for i in branch_info)
    holdings, drivers = {}, {}
    for k, b in enumerate(book_list):
        w = W[k][0]
        rows = []
        for i, s in enumerate(syms):
            if w[i] <= 0:
                continue
            c = exp_contrib[i]
            order = np.argsort(-np.abs(c))[:3]
            why = [{"factor": cols[j], "effect": float(c[j])} for j in order if abs(c[j]) >= 0.001]
            h = next(x for x in family.holdings if x.symbol == s)
            v = (b.value * w[i]) * to_ils
            rows.append({"symbol": s, "name": h.name, "weight": float(w[i]), "value_ils": v, "move": float(exp_move[i]),
                         "change_ils": float(exp_move[i] * v), "why": why,
                         "own_news": any(s in (br.get("holding_overrides") or {}) for br in branches)})
        rows.sort(key=lambda r: r["change_ils"])
        key = str(b.pid) if b.pid is not None else "family"
        holdings[key] = rows
        fac = w @ exp_contrib
        order = np.argsort(fac)
        drivers[key] = [{"factor": cols[j], "effect": float(fac[j])} for j in order if abs(fac[j]) >= 0.0005][:4]

    edges = np.percentile(outcomes[:, 0], [0.5, 99.5])
    bins = np.linspace(edges[0], edges[1], 41) if edges[1] > edges[0] else np.linspace(edges[0] - 0.01, edges[0] + 0.01, 41)
    counts, _ = np.histogram(np.clip(outcomes[:, 0], bins[0], bins[-1]), bins=bins)

    out = {
        "empty": False, "spec": spec, "n": n, "seed": seed, "horizon": spec.get("horizon"), "horizon_days": days,
        "base_currency": base, "usd_ils": to_ils if base == "USD" else _fx_now(session),
        "blended": blended, "branches": br_out, "holdings": holdings, "drivers": drivers,
        "histogram": {"edges": bins.tolist(), "counts": counts.tolist()},
        "exposures": {"family": factors.aggregate([(float(W[0][0][i]), exp[s]["ils"]["betas"]) for i, s in enumerate(syms)], W[0][1], 1.0 if base == "USD" else 0.0)},
        "assumptions": [
            "Each holding moves by its factor betas times the factor moves, in shekels; factors a branch doesn't name stay flat.",
            "A sector override moves holdings of that sector through their sector beta, beyond the market.",
            f"{n:,} draws: branch by probability, moves from triangular low/mid/high ranges with historical correlations, plus each holding's own noise over {days} trading days.",
            "Crisis causes (geopolitical, supply, demand) use correlations from sell-off weeks; others use normal weeks, unless you choose.",
            "Cash in dollars moves with USD/ILS when counted in shekels." if base == "USD" else "Cash is in shekels and doesn't move.",
        ] + ([f"Ranges and noise are widened ×{k:.2f}: {settings.band_note}"] if abs(k - 1) > 1e-9 else []),
        "warnings": [],
    }
    if keep:  # raw draws for callers that need more than the summaries (not serialized)
        out["_outcomes"], out["_pick"] = outcomes, sim["pick"]
    fam = blended[0]
    out["answer"] = {"median_ils": fam["median_ils"], "median": fam["median"], "p5_ils": fam["p5_ils"], "p5": fam["p5"],
                     "prob_loss": fam["prob_loss"]}
    if analogs:
        out["analogs"] = _analogs(session, spec, ports, family, br_out, out["warnings"])
    return out


def _analogs(session: Session, spec: dict, ports: list[Book], family: Book, br_out: list[dict], warnings: list[str]) -> list[dict]:
    """Today's holdings through each analog's real dates, next to the model's result."""
    u = universe(session, [h.symbol for h in family.holdings] + ["^TA125.TA"])
    res = []
    for b, bo in zip(spec["branches"], br_out):
        moves = []
        for a in b.get("analogs") or []:
            ev = {"id": f"{b['name']}:{a['name']}", "name": a["name"], "he": "", "start": a["start"], "end": a["end"]}
            r = replay(u, ev, ports, family)
            if r is None:
                res.append({"branch": b["name"], "name": a["name"], "start": a["start"], "end": a["end"], "no_data": True})
                continue
            r = {k: v for k, v in r.items() if not k.startswith("_")}
            r["branch"] = b["name"]
            res.append(r)
            moves.append(r["family"]["impact"])
        fam = bo["books"][0]
        if moves and fam["median"] is not None:
            lo, hi = min(moves), max(moves)
            if fam["median"] < lo - 0.0025 or fam["median"] > hi + 0.0025:
                warnings.append(f"{b['name']}: the model's median for the family ({fam['median']:+.1%}) is outside what its past analogs did "
                                f"({lo:+.1%} to {hi:+.1%}).")
    return res


# ------------------------------------------------------------------ local-rule scenarios as specs

SYMBOL_FACTOR = {"SPY": "market", "TLT": "rates", "USO": "oil", "UUP": "dollar", "ILS=X": "shekel", "GLD": "gold"}
EXTRA_FACTOR_SYMBOLS = ["IWM", "IWD", "IWF", "MTUM", "QUAL"]


def spec_from_shocks(session: Session, shocks: list[dict], horizon_weeks: float = 1.0, title: str = "", question: str = "",
                     used_events: list[str] | None = None, regime: str = "normal") -> dict:
    """A one-branch spec from local-rule shocks. Factors not shocked take their expected move given the
    shocks (historical relationship, normal or sell-off weeks), with a one-standard-deviation range;
    shocked moves get a range of half their size either way."""
    from .scenario_library import EVENT_BY_ID

    as_of = dt.date.today()
    F = factors.factor_returns(session, as_of)
    data = {c: F[c] for c in F.columns}
    shocked: dict[str, float] = {}
    own: dict[str, float] = {}
    for s in shocks:
        sym = s["symbol"].upper()
        mv = float(s["move"])
        col = SYMBOL_FACTOR.get(sym)
        if col and col in F.columns:
            shocked[col] = mv
        else:
            r = factors.holding_returns(session, sym, as_of).get("usd")
            if r is None:
                continue
            data[f"raw:{sym}"] = r
            shocked[f"raw:{sym}"] = mv
            if s.get("kind") == "stock" or sym not in factors.SECTOR_ETFS:
                own[sym] = mv
    R = pd.DataFrame(data).dropna(how="all")
    keys = list(R.columns)
    cov = stress.covariances(R.rename(columns={"market": "SPY"}))
    C = cov["stress" if regime == "selloff" else "calm"]
    S = [keys.index(k) for k in shocked]
    sv = np.array([shocked[k] for k in shocked])
    # company news alone: hold the market still
    if shocked and all(k.startswith("raw:") for k in shocked) and all(s.get("kind") == "stock" for s in shocks) and "market" in keys:
        S.append(keys.index("market"))
        sv = np.append(sv, 0.0)
    cond = stress.conditional(C, S, sv, max(horizon_weeks, 1.0)) if S else {"mu": np.zeros(len(keys)), "sd": np.zeros(len(keys))}

    def rng_(mid: float, sd: float, fixed: bool) -> dict:
        if fixed:
            half = abs(mid) * 0.5
            return {"low": round((mid - half) * 100, 3), "mid": round(mid * 100, 3), "high": round((mid + half) * 100, 3)}
        return {"low": round((mid - sd) * 100, 3), "mid": round(mid * 100, 3), "high": round((mid + sd) * 100, 3)}

    moves, sectors = {}, {}
    for fid in factors.BASE_IDS:
        if fid in keys:
            i = keys.index(fid)
            moves[fid] = rng_(float(cond["mu"][i]), float(cond["sd"][i]), fid in shocked)
    mkt = cond["mu"][keys.index("market")] if "market" in keys else 0.0
    for e in factors.SECTOR_ETFS:
        k = f"sector_{e}"
        if f"raw:{e}" in shocked:
            sectors[e] = rng_(shocked[f"raw:{e}"], 0, True)
        elif k in keys:
            i = keys.index(k)
            sectors[e] = rng_(float(cond["mu"][i] + mkt), float(cond["sd"][i]), False)
    holding_over = {sym: rng_(v, 0, True) for sym, v in own.items()}
    hw = max(horizon_weeks, 1.0)
    horizon = "1w" if hw <= 1.5 else "2w" if hw <= 3 else "1m" if hw <= 6 else "3m" if hw <= 18 else "6m"
    analogs = [{"name": EVENT_BY_ID[e]["name"], "start": EVENT_BY_ID[e]["start"], "end": EVENT_BY_ID[e]["end"]}
               for e in (used_events or []) if e in EVENT_BY_ID]
    return {
        "spec_version": 1, "kind": "scenario", "id": "local", "title": title or "Scenario", "created_at": as_of.isoformat(),
        "author": "manual", "question": question, "horizon": horizon, "summary": "", "confidence": "low",
        "branches": [{"name": "Scenario", "probability": 1.0, "cause": "other" if regime != "selloff" else "demand",
                      "narrative": "", "factor_moves": moves, "sector_overrides": sectors, "holding_overrides": holding_over,
                      "analogs": analogs, "transmission": [{"from": "trigger", "to": SYMBOL_FACTOR.get(s["symbol"].upper(), "market"),
                                                            "direction": "+" if s["move"] >= 0 else "-"} for s in shocks
                                                           if SYMBOL_FACTOR.get(s["symbol"].upper())]}],
    }
