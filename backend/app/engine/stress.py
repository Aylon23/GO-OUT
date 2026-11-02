"""Pure math for scenario stress tests. No database, no network.

Conventions:
- Returns are simple weekly returns (Friday to Friday), as fractions.
- Covariances are second moments around zero (weekly means are tiny next to weekly moves, and
  in sell-off weeks the co-movement itself is what we want to keep).
- A scenario is a few shocks: {asset index: move}. Everything else moves by its conditional
  expectation given the shocks (the "correlated stress test" of factor-model risk systems):
      E[r | r_S = s] = C[:, S] C[S, S]^-1 s
      Var[r | r_S = s] = diag(C - C[:, S] C[S, S]^-1 C[S, :])
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

HALF_LIFE_WEEKS = 52
MIN_WEEKS = 52  # an asset needs this many overlapping weeks to get its own estimate
STRESS_QUANTILE = 0.15  # sell-off weeks: the worst 15% of S&P weeks
STRESS_BLEND = 0.7  # stressed matrix = 70% sell-off weeks + 30% all weeks (few sell-off weeks are noisy)


def weekly_returns(prices: dict[str, pd.Series]) -> pd.DataFrame:
    """Friday-to-Friday simple returns. Each input is a price series indexed by date."""
    cols = {}
    for k, p in prices.items():
        p = p.dropna()
        p = p[p > 0]
        if len(p) < 10:
            continue
        s = pd.Series(p.to_numpy(dtype=float), index=pd.to_datetime(p.index))
        s = s[~s.index.duplicated(keep="last")].sort_index()
        w = s.resample("W-FRI").last()
        cols[k] = w.pct_change(fill_method=None)
    df = pd.DataFrame(cols)
    return df.iloc[1:] if len(df) else df


def ewma_weights(n: int, half_life: float = HALF_LIFE_WEEKS) -> np.ndarray:
    lam = 0.5 ** (1.0 / half_life)
    return lam ** np.arange(n - 1, -1, -1, dtype=float)


def pairwise_moment(R: pd.DataFrame, w: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Weighted second-moment matrix using, for each pair, only the weeks both have data.
    Returns (matrix, overlap count matrix)."""
    X = R.to_numpy(dtype=float)
    M = np.isfinite(X).astype(float)
    X = np.where(M > 0, X, 0.0)
    if w is None:
        w = np.ones(len(X))
    wx = X * w[:, None]
    num = wx.T @ X
    den = (M * w[:, None]).T @ M
    cnt = M.T @ M
    with np.errstate(invalid="ignore", divide="ignore"):
        C = np.where(den > 0, num / den, 0.0)
    return C, cnt


def nearest_psd(C: np.ndarray, floor: float = 1e-10) -> np.ndarray:
    C = (C + C.T) / 2
    vals, vecs = np.linalg.eigh(C)
    vals = np.maximum(vals, floor)
    out = (vecs * vals) @ vecs.T
    return (out + out.T) / 2


def covariances(R: pd.DataFrame, market: str = "SPY") -> dict[str, np.ndarray]:
    """{"calm": all weeks, exponentially weighted; "stress": sell-off weeks blended with all weeks;
    "count": overlapping weeks per pair}."""
    calm, cnt = pairwise_moment(R, ewma_weights(len(R)))
    flat, _ = pairwise_moment(R)
    out = {"calm": nearest_psd(calm), "count": cnt}
    if market in R.columns and R[market].notna().sum() >= 40:
        m = R[market]
        cut = m.quantile(STRESS_QUANTILE)
        rows = (m <= cut).to_numpy()
        sel, _ = pairwise_moment(R[rows])
        out["stress"] = nearest_psd(STRESS_BLEND * sel + (1 - STRESS_BLEND) * flat)
        out["stress_weeks"] = int(rows.sum())
    else:
        out["stress"] = out["calm"]
        out["stress_weeks"] = 0
    return out


def conditional(C: np.ndarray, shocked: list[int], s: np.ndarray, horizon: float = 1.0) -> dict:
    """Conditional mean and standard deviation of every asset given the shocked ones.
    `horizon` (weeks) scales the variance; the mean does not depend on it."""
    S = list(shocked)
    Css = C[np.ix_(S, S)]
    ridge = 1e-9 * max(np.trace(Css) / max(len(S), 1), 1e-12)
    inv = np.linalg.inv(Css + ridge * np.eye(len(S)))
    B = C[:, S] @ inv  # sensitivities: d E[r_i] / d s_j
    mu = B @ s
    Ccond = C - B @ C[S, :]
    Ccond = (Ccond + Ccond.T) / 2
    sd = np.sqrt(np.maximum(np.diag(Ccond), 0.0) * horizon)
    mu[S] = s
    sd[S] = 0.0
    return {"mu": mu, "sd": sd, "B": B, "cond_cov": Ccond * horizon, "inv": inv}


def portfolio_moments(w: np.ndarray, mu: np.ndarray, cond_cov: np.ndarray) -> tuple[float, float]:
    m = float(w @ mu)
    v = float(w @ cond_cov @ w)
    return m, math.sqrt(max(v, 0.0))


def chi2_sf(x: float, k: int) -> float:
    """P(chi-square with k degrees of freedom > x), via the regularized lower incomplete gamma."""
    if x <= 0:
        return 1.0
    a, z = k / 2.0, x / 2.0
    if z > a + 60:
        return 0.0
    term = 1.0 / a
    total = term
    n = 1
    while n < 2000:
        term *= z / (a + n)
        total += term
        if term < total * 1e-14:
            break
        n += 1
    lower = math.exp(-z + a * math.log(z) - math.lgamma(a)) * total
    return max(0.0, 1.0 - lower)


def plausibility(C: np.ndarray, shocked: list[int], s: np.ndarray, horizon: float = 1.0, R: pd.DataFrame | None = None) -> dict:
    """How unusual the shocks are together. The distance is Mahalanobis under the covariance;
    the frequency is counted from history (fat tails make bell-curve odds far too small):
    the share of past weeks at least this far out, with shocks scaled to one week."""
    S = list(shocked)
    Css = C[np.ix_(S, S)]
    inv = np.linalg.inv(Css + 1e-12 * np.eye(len(S)))
    s1 = s / math.sqrt(max(horizon, 1.0))
    d2 = float(s1 @ inv @ s1)
    z = [float(s1[i] / math.sqrt(Css[i, i])) if Css[i, i] > 0 else None for i in range(len(S))]
    seen = weeks = None
    years = None
    if R is not None:
        X = R.iloc[:, S].dropna().to_numpy()
        if len(X) >= 52:
            hist = np.einsum("ij,jk,ik->i", X, inv, X)
            weeks = int(len(X))
            seen = int(np.sum(hist >= d2 * 0.999))
            years = (weeks / 52.0) / seen if seen else None
    if seen == 0:
        label = "not seen in the data"
    elif years is None:
        p = chi2_sf(d2, len(S))
        years = (1 / p) / 52 if p > 0 else None
        label = "rare" if years and years > 5 else "common"
    elif years < 0.5:
        label = "common"
    elif years < 3:
        label = "happens every year or two"
    else:
        label = "rare"
    return {"d2": d2, "years": years, "seen": seen, "weeks": weeks, "label": label, "z": z}


def typical_move(r: pd.Series, q: float = 0.9) -> float | None:
    """A strong but ordinary week: the q-th percentile of absolute weekly moves."""
    r = r.dropna()
    if len(r) < 26:
        return None
    return float(np.quantile(np.abs(r.to_numpy()), q))
