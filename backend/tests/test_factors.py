"""Factor model: betas from weekly returns, ridge-shrunk toward the sector; shekel as a factor."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import factors, services
from app.main import app
from app.market import provider
from app.market.synthetic import SyntheticProvider

AS_OF = dt.date.today() - dt.timedelta(days=3)


@pytest.fixture
def syn(session):
    provider.set_provider(SyntheticProvider())
    services.bump_revision()
    factors._factor_cache.clear()
    return session


def betas(session, sym, as_of=AS_OF):
    return factors.estimate(session, sym, as_of)["local"]


def test_qqq_market_beta_above_spy(syn):
    spy = betas(syn, "SPY")["betas"]["market"]
    qqq = betas(syn, "QQQ")["betas"]["market"]
    assert spy == pytest.approx(1.0, abs=0.05)
    assert qqq > spy + 0.02  # tech-heavy: clearly above the market (synthetic data puts the gap at 0.035-0.05)


def test_oil_betas_energy_positive_airline_negative(syn):
    services.ensure_meta_info(syn, ["XOM", "DAL"])
    assert betas(syn, "XOM")["betas"]["oil"] > 0.2
    assert betas(syn, "XLE")["betas"]["oil"] > 0.2
    assert betas(syn, "DAL")["betas"]["oil"] < -0.2


def test_utilities_positive_rates_beta(syn):
    services.ensure_meta_info(syn, ["NEE"])
    assert betas(syn, "XLU")["betas"]["rates"] > 0.2
    assert betas(syn, "NEE")["betas"]["rates"] > 0.2


def test_no_currency_factor_and_local_returns_only(syn):
    """Stock performance only: no USD/ILS or dollar factor, and a holding's returns are its own-currency returns."""
    assert "shekel" not in factors.BASE_IDS and "dollar" not in factors.BASE_IDS
    services.ensure_meta_info(syn, ["AAPL"])
    b = betas(syn, "AAPL")["betas"]
    assert "shekel" not in b and "dollar" not in b
    R = factors.holding_returns(syn, "AAPL", AS_OF)["local"]
    from app.market.cache import load_history
    import pandas as pd
    px = load_history(syn, "AAPL")["adj_close"]
    wk = pd.Series(px.to_numpy(), index=pd.to_datetime(px.index)).resample("W-FRI").last().pct_change()
    assert R.iloc[-5] == pytest.approx(wk.loc[R.index[-5]])  # the dollar price's own weekly return


def test_short_history_shrinks_to_sector_and_is_marked():
    rng = np.random.default_rng(3)
    X = pd.DataFrame({"market": rng.normal(0, 0.02, 30), "oil": rng.normal(0, 0.05, 30)})
    y = pd.Series(3.0 * X["market"] + rng.normal(0, 0.03, 30))
    b0 = np.array([1.0, 0.0])
    short = factors.fit(y, X, b0)
    long_X = pd.DataFrame({"market": rng.normal(0, 0.02, 156), "oil": rng.normal(0, 0.05, 156)})
    long = factors.fit(pd.Series(3.0 * long_X["market"] + rng.normal(0, 0.03, 156)), long_X, b0)
    assert short["short_history"] and not long["short_history"]
    assert long["betas"]["market"] == pytest.approx(3.0, abs=0.35)
    assert 1.0 < short["betas"]["market"] < long["betas"]["market"] - 0.4  # pulled toward the sector's 1.0


def test_fit_matches_least_squares_without_shrinkage(monkeypatch):
    monkeypatch.setattr(factors, "LAMBDA", 0.0)
    rng = np.random.default_rng(4)
    X = pd.DataFrame({"a": rng.normal(0, 1, 200), "b": rng.normal(0, 1, 200)})
    y = pd.Series(0.7 * X["a"] - 0.3 * X["b"] + rng.normal(0, 0.1, 200))
    out = factors.fit(y, X, np.zeros(2))
    ols = np.linalg.lstsq(np.c_[np.ones(200), X.to_numpy()], y.to_numpy(), rcond=None)[0][1:]
    assert out["betas"]["a"] == pytest.approx(ols[0], abs=1e-9) and out["betas"]["b"] == pytest.approx(ols[1], abs=1e-9)
    assert 0.8 < out["r2"] < 1 and out["weeks"] == 200


def test_no_look_ahead(syn):
    services.ensure_meta_info(syn, ["NVDA"])
    early = AS_OF - dt.timedelta(days=180)
    a = betas(syn, "NVDA", as_of=early)
    F = factors.factor_returns(syn, early)
    assert F.index.max() <= pd.Timestamp(early) + pd.Timedelta(days=6)  # the week containing as_of at most
    R = factors.holding_returns(syn, "NVDA", early)
    assert R["local"].index.max() <= pd.Timestamp(early) + pd.Timedelta(days=6)
    # later prices don't change an earlier estimate
    factors._factor_cache.clear()
    b = betas(syn, "NVDA", as_of=early)
    assert a["betas"] == pytest.approx(b["betas"])


@pytest.fixture
def family(syn):
    c = TestClient(app)
    c.put("/api/settings", json={"base_currency": "USD"})
    assert c.post("/api/demo?portfolio=1").status_code == 200
    pid2 = c.post("/api/portfolios", json={"name": "Keren"}).json()["id"]
    for t in ("XOM", "NEE"):
        px = c.get(f"/api/price/{t}?date=2024-01-03").json()["close"]
        c.post(f"/api/transactions?portfolio={pid2}", json={"ticker": t, "type": "BUY", "date": "2024-01-03", "quantity": 50, "price": px, "currency": "USD"})
    return c


def test_payload_family_first_and_value_weighted(family, syn):
    d = family.get("/api/exposures").json()
    assert d["basis"] == "local" and [b["name"] for b in d["books"]][0] == "Family"
    fam, *ports = d["books"]
    # family betas = value-weighted portfolio betas
    for k in ("market", "oil", "rates"):
        mix = sum(p["betas"].get(k, 0) * p["value"] for p in ports) / sum(p["value"] for p in ports)
        assert fam["betas"][k] == pytest.approx(mix, abs=1e-9)
    keren = next(b for b in ports if b["name"] == "Keren")
    assert keren["betas"]["oil"] > 0  # Exxon
    assert all("r2" in h and "resid_vol" in h and "weeks" in h for h in fam["holdings"])
    assert {f["id"] for f in d["factors"]} >= {"market", "rates", "oil", "gold"}
    assert not {"dollar", "shekel"} & {f["id"] for f in d["factors"]}
    # cached: a second call doesn't re-estimate
    rows = syn.exec(factors.select(factors.FactorExposure)).all()
    assert len(rows) == len(fam["holdings"])  # one own-currency view per holding


def test_family_market_beta_stable_when_window_shifts_4_weeks(family, syn):
    from app.scenarios import books

    _, fam = books(syn, None)
    total = sum(h.value for h in fam.holdings)

    def market(as_of):
        e = factors.exposures(syn, [h.symbol for h in fam.holdings], as_of, refresh=True)
        return sum(h.value / total * e[h.symbol]["local"]["betas"]["market"] for h in fam.holdings)

    assert abs(market(AS_OF) - market(AS_OF - dt.timedelta(weeks=4))) < 0.1
