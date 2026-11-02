"""Scenario stress tests for every portfolio and the whole family. Method: docs/SCENARIOS.md.

Flow: text -> parse() -> editable shocks -> run() -> impact per portfolio and holding, the
market picture, past events like it, a reverse stress test and protection ideas.
model_check() measures how well the method would have predicted past events.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sqlmodel import Session, select

from . import services
from .engine import stress
from .engine.series import align, fx_factor
from .market.cache import NoPriceData, currency_for, load_history
from .market.names import clean_name
from .models import Portfolio, Scenario, SymbolMeta
from .scenario_library import EVENT_BY_ID, EVENTS, FACTOR_SYMBOLS, FACTORS, LABEL, SECTOR_PROXY, THEME_BY_ID
from .scenario_text import parse_rules

log = logging.getLogger(__name__)

YEARS = 10
EXTRA_IDIO_WEEKLY = 0.035  # extra weekly uncertainty for a holding measured through a stand-in
MAX_SHOCKS = 6
INDEX_HEDGES = ["SPY", "QQQ", "IWM", "SOXX", "XLK", "EIS"]
INVERSE = {"SPY": "SH", "QQQ": "PSQ", "IWM": "RWM"}
SCENARIO_HEDGES = ["GLD", "TLT", "IEF", "USO", "XLE", "ITA", "UUP", "XLP", "XLU", "XLV"]
ENGLISH_NAMES = {
    "apple": "AAPL", "microsoft": "MSFT", "nvidia": "NVDA", "google": "GOOGL", "alphabet": "GOOGL", "amazon": "AMZN",
    "tesla": "TSLA", "meta": "META", "facebook": "META", "netflix": "NFLX", "intel": "INTC", "amd": "AMD", "broadcom": "AVGO",
    "teva": "TEVA", "palantir": "PLTR", "berkshire": "BRK-B", "jpmorgan": "JPM", "exxon": "XOM", "chevron": "CVX",
    "oracle": "ORCL", "salesforce": "CRM", "adobe": "ADBE", "qualcomm": "QCOM", "tsmc": "TSM", "coca-cola": "KO",
    "costco": "COST", "walmart": "WMT", "disney": "DIS", "boeing": "BA", "lockheed": "LMT", "elbit": "ESLT", "nice": "NICE",
    "check point": "CHKP", "wix": "WIX", "monday": "MNDY", "cyberark": "CYBR", "mobileye": "MBLY",
}


# ------------------------------------------------------------------ data

@dataclass
class Universe:
    symbols: list[str]
    R: pd.DataFrame  # weekly returns in the base currency
    C: dict  # covariances: calm, stress, count, stress_weeks
    daily: dict[str, pd.Series]  # daily prices in the base currency, for event replays
    typical: dict[str, float]
    missing: list[str] = field(default_factory=list)

    def idx(self, s: str) -> int | None:
        try:
            return self.symbols.index(s)
        except ValueError:
            return None

    def has(self, s: str) -> bool:
        i = self.idx(s)
        return i is not None and self.C["count"][i, i] >= stress.MIN_WEEKS


_universe_cache: dict[tuple, Universe] = {}


def _daily_base(session: Session, sym: str, fx: pd.Series, base: str) -> pd.Series | None:
    try:
        df = load_history(session, sym)
    except NoPriceData:
        return None
    if df is None or df.empty:
        return None
    start = dt.date.today() - dt.timedelta(days=int(365.25 * 20))
    df = df[df.index >= start]
    p = df["adj_close"].astype(float)
    if sym == "ILS=X" or sym.startswith("^"):
        return p  # a rate or an index level, not a holding: no currency conversion
    ccy = currency_for(session, sym)
    if ccy == base:
        return p
    cal = list(p.index)
    return p * fx_factor(ccy, base, align(fx, cal)).to_numpy()


def universe(session: Session, extra: list[str]) -> Universe:
    settings = services.get_settings(session)
    base = settings.base_currency
    symbols = list(dict.fromkeys(FACTOR_SYMBOLS + [s.upper() for s in extra if s]))
    key = (services._rev, dt.date.today(), base, tuple(symbols))
    hit = _universe_cache.get(key)
    if hit:
        return hit
    fx = services.fx_series(session)
    daily, missing = {}, []
    for s in symbols:
        p = _daily_base(session, s, fx, base)
        if p is None or len(p) < 30:
            missing.append(s)
            continue
        daily[s] = p
    cut = pd.Timestamp(dt.date.today() - dt.timedelta(days=int(365.25 * YEARS)))
    R = stress.weekly_returns(daily)
    R = R[R.index >= cut]
    have = [s for s in symbols if s in R.columns]
    R = R[have]
    C = stress.covariances(R)
    typical = {s: v for s in have if (v := stress.typical_move(R[s])) is not None}
    u = Universe(have, R, C, daily, typical, missing)
    if len(_universe_cache) > 8:
        _universe_cache.clear()
    _universe_cache[key] = u
    return u


@dataclass
class Holding:
    symbol: str
    name: str
    value: float  # base currency
    sector: str
    kind: str


@dataclass
class Book:
    pid: int | None  # None = the whole family
    name: str
    value: float  # total value including cash
    holdings: list[Holding]


def books(session: Session, pids: list[int] | None) -> tuple[list[Book], Book]:
    ports = session.exec(select(Portfolio).order_by(Portfolio.id)).all()
    if pids:
        ports = [p for p in ports if p.id in pids]
    out = []
    metas = {m.ticker: m for m in session.exec(select(SymbolMeta)).all()}
    for p in ports:
        ctx = services.Context(session, p.id)
        if ctx.series is None:
            continue
        hv = ctx.series.holding_value.iloc[-1]
        hs = []
        for t, q in services.open_positions(ctx).items():
            v = float(hv.get(t, 0.0))
            if v <= 0:
                continue
            m = metas.get(t)
            hs.append(Holding(t, clean_name(m.name if m else t, t), v, (m.sector if m else "") or "", (m.type if m else "") or ""))
        total = float(ctx.series.value.iloc[-1])
        if total > 0 and hs:
            out.append(Book(p.id, p.name, total, sorted(hs, key=lambda h: -h.value)))
    merged: dict[str, Holding] = {}
    for b in out:
        for h in b.holdings:
            if h.symbol in merged:
                merged[h.symbol].value += h.value
            else:
                merged[h.symbol] = Holding(h.symbol, h.name, h.value, h.sector, h.kind)
    family = Book(None, "Everyone", sum(b.value for b in out), sorted(merged.values(), key=lambda h: -h.value))
    return out, family


def _column(u: Universe, h: Holding) -> tuple[int, bool, str | None]:
    """Which column measures this holding: its own, or a stand-in (sector fund, else the S&P)."""
    if u.has(h.symbol):
        return u.idx(h.symbol), False, None
    proxy = SECTOR_PROXY.get(h.sector) if h.kind not in ("ETF", "Fund") else None
    proxy = proxy if proxy and u.has(proxy) else "SPY"
    return u.idx(proxy), True, proxy


# ------------------------------------------------------------------ events

def _asof(p: pd.Series, d: dt.date) -> tuple[float, dt.date] | None:
    sub = p[p.index <= d]
    if sub.empty:
        return None
    return float(sub.iloc[-1]), sub.index[-1]


def event_move(u: Universe, sym: str, ev: dict) -> float | None:
    """Actual move over an event window, or None when the asset has no prices covering it."""
    p = u.daily.get(sym)
    if p is None:
        return None
    s, e = dt.date.fromisoformat(ev["start"]), dt.date.fromisoformat(ev["end"])
    a, b = _asof(p, s), _asof(p, e)
    if a is None or b is None or (s - a[1]).days > 6 or a[0] <= 0 or b[1] <= a[1]:
        return None
    return b[0] / a[0] - 1


def event_weeks(ev: dict) -> float:
    s, e = dt.date.fromisoformat(ev["start"]), dt.date.fromisoformat(ev["end"])
    days = int(np.busday_count(s, e))
    return max(1.0, days / 5.0)


def _analogs(u: Universe, shocks: dict[str, float], exclude: set[str], n: int = 3) -> list[str]:
    """Past events whose moves in the shocked assets point the same way, scaled by volatility."""
    syms = [s for s in shocks if u.has(s)]
    if not syms:
        return []
    sd = {s: math.sqrt(u.C["calm"][u.idx(s), u.idx(s)]) for s in syms}
    target = np.array([shocks[s] / sd[s] for s in syms])
    scored = []
    for ev in EVENTS:
        if ev["id"] in exclude:
            continue
        mv = [event_move(u, s, ev) for s in syms]
        if any(m is None for m in mv):
            continue
        h = math.sqrt(event_weeks(ev))
        vec = np.array([m / (sd[s] * h) for m, s in zip(mv, syms)])
        nv, nt = np.linalg.norm(vec), np.linalg.norm(target)
        if nv < 1.0 or nt == 0:  # the event barely moved these assets
            continue
        cos = float(vec @ target / (nv * nt))
        if cos > 0.75:
            scored.append((cos * min(nv / max(nt, 1e-9), 1.5), ev["id"]))
    return [i for _, i in sorted(scored, reverse=True)[:n]]


# ------------------------------------------------------------------ parsing

def _companies(session: Session, held: list[Holding]) -> dict[str, str]:
    from .omnibox import HEBREW_NAMES

    out = {k: v for k, v in HEBREW_NAMES.items() if not v.endswith(("QQQ", "SPY")) and v not in ("QQQ", "SPY")}
    out.update(ENGLISH_NAMES)
    for h in held:
        if len(h.symbol) >= 3 and h.symbol.isalpha():
            out[h.symbol.lower()] = h.symbol
        first = (h.name or "").split(" ")[0].strip(",.").lower()
        if len(first) >= 4 and first not in ("the", "invesco", "ishares", "vanguard", "spdr", "select", "global", "united", "first"):
            out.setdefault(first, h.symbol)
    for s in FACTOR_SYMBOLS:
        out.pop(s.lower(), None)
    return out


def _ai_parse(session: Session, text: str, key: str, held: list[str]) -> dict:
    """Ask Claude to read the scenario. Returns the same shape as parse_rules."""
    import anthropic

    factors = "\n".join(f"{s}: {en}" for s, en, _he, _g in FACTORS)
    events = "\n".join(f"{e['id']}: {e['name']} ({e['start']} to {e['end']})" for e in EVENTS)
    system = (
        "You turn a short market scenario, written in Hebrew or English by a private investor, into a few market shocks "
        "for a stress test. Use factor symbols from the list when the story is about the market, sectors, rates, "
        "commodities or currencies. Use a stock's Yahoo ticker only for news about one company. Pick 1 to 5 shocks: "
        "the ones the scenario is really about, not everything that might follow (the model works out the rest). "
        "Bond prices fall when yields or rates rise. ILS=X is shekels per dollar, so a stronger shekel means ILS=X down. "
        "Set move_percent only when the text gives a size; otherwise 0. Pick past events from the list that resemble the "
        "scenario, at most 4, or none.\n\nFactors:\n" + factors + "\n\nPast events:\n" + events
    )
    schema = {
        "type": "object",
        "properties": {
            "label": {"type": "string"},
            "shocks": {"type": "array", "items": {"type": "object", "properties": {
                "symbol": {"type": "string"},
                "kind": {"type": "string", "enum": ["factor", "stock"]},
                "direction": {"type": "string", "enum": ["up", "down"]},
                "move_percent": {"type": "number"},
                "why": {"type": "string"},
            }, "required": ["symbol", "kind", "direction", "move_percent", "why"], "additionalProperties": False}},
            "events": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "string"},
        },
        "required": ["label", "shocks", "events", "notes"],
        "additionalProperties": False,
    }
    client = anthropic.Anthropic(api_key=key, timeout=45.0, max_retries=1)
    resp = client.beta.messages.create(
        model="claude-opus-5-5",
        max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        system=system,
        messages=[{"role": "user", "content": f"Holdings in the portfolios: {', '.join(held) or 'none'}\n\nScenario: {text}"}],
    )
    if resp.stop_reason == "refusal":
        raise ValueError("Claude declined to read this scenario")
    raw = next((b.text for b in resp.content if b.type == "text"), "")
    data = json.loads(raw)
    shocks = []
    for s in data.get("shocks", [])[:MAX_SHOCKS]:
        sym = str(s.get("symbol", "")).upper().strip()
        if not sym:
            continue
        d = 1 if s.get("direction") == "up" else -1
        mp = float(s.get("move_percent") or 0)
        shocks.append({"symbol": sym, "kind": "factor" if sym in FACTOR_SYMBOLS else "stock", "direction": d,
                       "move": abs(mp) / 100 * d if mp else None, "word": s.get("why", "")})
    evs = [e for e in data.get("events", []) if e in EVENT_BY_ID]
    return {"themes": [], "shocks": shocks, "notes": [data["notes"]] if data.get("notes") else [], "events": evs, "label": data.get("label", "")}


def parse(session: Session, text: str, use_ai: bool = True) -> dict:
    _, family = books(session, None)
    settings = services.get_settings(session)
    key = (settings.anthropic_key or "").strip()
    parser, notes, raw = "rules", [], None
    if use_ai and key:
        try:
            raw = _ai_parse(session, text, key, [h.symbol for h in family.holdings])
            parser = "ai"
        except Exception as exc:  # any failure falls back to the free rules
            log.warning("AI scenario parse failed: %s", exc)
            notes.append("Claude couldn't read it right now, so the local rules did.")
    if raw is None:
        raw = parse_rules(text, _companies(session, family.holdings))
    notes += raw.get("notes", [])
    stock_syms = [s["symbol"] for s in raw["shocks"]]
    u = universe(session, [h.symbol for h in family.holdings] + stock_syms)

    themes = [THEME_BY_ID[t] for t in raw.get("themes", []) if t in THEME_BY_ID]
    events: list[str] = list(raw.get("events", []))
    for th in themes:
        events += [e for e in th["events"] if e not in events]
    shocks: list[dict] = []
    seen = set()
    for s in raw["shocks"]:
        sym = s["symbol"]
        if sym in seen:
            continue
        if not u.has(sym):
            notes.append(f"No price history for {sym}, so it was left out.")
            continue
        seen.add(sym)
        if s.get("move") is not None:
            shocks.append(_shock(u, sym, s["kind"], s["move"], "text", "As you wrote it", s.get("event")))
        else:
            mv = u.typical[sym] * s["direction"]
            what = "a strong week for it (90th percentile of weekly moves, 10 years)"
            shocks.append(_shock(u, sym, s["kind"], mv, "typical", what, s.get("event")))
    # past events actually used: a theme's events that have prices in the window
    used: list[str] = []
    for th in themes:
        used += [e for e in th["events"] if e not in used and event_move(u, "SPY", EVENT_BY_ID[e]) is not None]
    for th in themes:
        evs = [EVENT_BY_ID[e] for e in th["events"] if e in used]
        for sym in th["factors"]:
            if sym in seen or not u.has(sym) or len(shocks) >= MAX_SHOCKS:
                continue
            moves = [m for ev in evs if (m := event_move(u, sym, ev)) is not None]
            if moves:
                mv = float(np.median(moves))
                if abs(mv) < 0.002:
                    continue  # this factor didn't move in these events; let the model decide
                text = f"Median of {len(moves)} past event{'s' if len(moves) != 1 else ''} used"
                if len(moves) < len(evs):
                    text += f" ({len(evs) - len(moves)} had no prices for it)"
                sh = _shock(u, sym, "factor", mv, "events", text)
                sh["split"] = any(m > 0.002 for m in moves) and any(m < -0.002 for m in moves)
                sh["event_moves"] = [{"id": ev["id"], "move": m} for ev in evs if (m := event_move(u, sym, ev)) is not None]
                shocks.append(sh)
            else:
                mv = u.typical[sym] * th["fallback"].get(sym, -1)
                shocks.append(_shock(u, sym, "factor", mv, "typical", "No past event data; a strong week for it"))
            seen.add(sym)
    if any(sh["symbol"] == "USO" and sh["move"] < 0 for sh in shocks) and not {"recession", "crash", "pandemic"} & {t["id"] for t in themes}:
        notes.append("Assumes supply-driven (OPEC, ceasefire). A recession-driven drop looks very different.")
    horizon = 1.0
    if themes:
        lens = [event_weeks(EVENT_BY_ID[e]) for e in (used or [e for th in themes for e in th["events"]])]
        horizon = float(np.median(lens)) if lens else 1.0
    shocks = shocks[:MAX_SHOCKS]
    events = used + [e for e in events if e not in used]
    events += [e for e in _analogs(u, {s["symbol"]: s["move"] for s in shocks}, set(events)) if e not in events]
    label = raw.get("label") or (themes[0]["label"] if themes else "")
    return {"text": text, "parser": parser, "label": label, "themes": [{"id": t["id"], "label": t["label"]} for t in themes],
            "shocks": shocks, "events": events[:max(6, len(used))], "used_events": used, "horizon_weeks": round(horizon, 1), "notes": notes,
            "understood": bool(shocks)}


def _shock(u: Universe, sym: str, kind: str, move: float, basis: str, basis_text: str, event: str | None = None) -> dict:
    return {"symbol": sym, "label": LABEL.get(sym, sym), "kind": kind, "move": float(move), "basis": basis,
            "basis_text": basis_text, "typical": u.typical.get(sym), "event": event}


# ------------------------------------------------------------------ the run

def _propagate(u: Universe, shocks: list[dict], horizon: float, regime: str = "normal") -> dict:
    """Conditional moves of every asset, with normal and sell-off correlations. The regime is the
    user's choice (normal by default); the size of the move never switches it."""
    S = [u.idx(s["symbol"]) for s in shocks]
    s = np.array([float(x["move"]) for x in shocks])
    pinned = None
    if shocks and all(x["kind"] == "stock" for x in shocks) and "SPY" not in [x["symbol"] for x in shocks]:
        # company news: hold the overall market still, so only what is tied to the company moves
        S.append(u.idx("SPY"))
        s = np.append(s, 0.0)
        pinned = "SPY"
    calm = stress.conditional(u.C["calm"], S, s, horizon)
    sell = stress.conditional(u.C["stress"], S, s, horizon)
    mode = "stress" if regime == "selloff" else "calm"
    plaus = stress.plausibility(u.C["calm"], S, s, horizon, u.R)
    return {"S": S, "s": s, "calm": calm, "stress": sell, "mode": mode, "pinned": pinned, "plaus": plaus}


def _book_impact(u: Universe, book: Book, prop: dict, detail: bool = True) -> dict:
    head = prop[prop["mode"]]
    shocked = {u.symbols[i]: prop["s"][k] for k, i in enumerate(prop["S"])}
    cols, extra, rows = [], [], []
    for h in book.holdings:
        col, proxied, proxy = _column(u, h)
        w = h.value / book.value
        own_shock = h.symbol in shocked
        mu = float(shocked[h.symbol]) if own_shock else float(head["mu"][col])
        mu_n = float(shocked[h.symbol]) if own_shock else float(prop["calm"]["mu"][col])
        mu_s = float(shocked[h.symbol]) if own_shock else float(prop["stress"]["mu"][col])
        sd = 0.0 if own_shock else math.sqrt(float(head["sd"][col]) ** 2 + (EXTRA_IDIO_WEEKLY ** 2 * prop["h"] if proxied else 0.0))
        cols.append(col)
        extra.append(0.0 if own_shock or not proxied else EXTRA_IDIO_WEEKLY ** 2 * prop["h"])
        row = {"symbol": h.symbol, "name": h.name, "value": h.value, "weight": w, "move": mu, "move_normal": mu_n, "move_selloff": mu_s,
               "low": mu - sd, "high": mu + sd, "change": mu * h.value, "proxy": proxy if proxied else None, "shocked": own_shock}
        if detail and not own_shock:
            contrib = head["B"][col] * prop["s"]
            order = np.argsort(-np.abs(contrib))[:2]
            row["drivers"] = [{"symbol": u.symbols[prop["S"][j]], "effect": float(contrib[j])} for j in order if abs(contrib[j]) > 0.001]
        rows.append(row)
    w = np.array([r["weight"] for r in rows])
    mu = np.array([r["move"] for r in rows])
    cc = head["cond_cov"][np.ix_(cols, cols)].copy()
    shocked_mask = np.array([r["shocked"] for r in rows])
    cc[shocked_mask, :] = 0
    cc[:, shocked_mask] = 0
    var = float(w @ cc @ w + np.sum(w ** 2 * np.array(extra)))
    m = float(w @ mu)
    sd = math.sqrt(max(var, 0.0))
    normal = float(w @ np.array([r["move_normal"] for r in rows]))
    selloff = float(w @ np.array([r["move_selloff"] for r in rows]))
    rows.sort(key=lambda r: r["change"])
    return {"id": book.pid, "name": book.name, "value": book.value, "invested": float(sum(h.value for h in book.holdings)),
            "impact": m, "change": m * book.value, "low": m - sd, "high": m + sd,
            "impact_normal": normal, "impact_selloff": selloff, "change_normal": normal * book.value, "change_selloff": selloff * book.value,
            "holdings": rows, "_w": w, "_cols": cols, "_mu": mu}


NEEDS_PROTECTION_MODEL = -0.005  # model hit beyond -0.5%
NEEDS_PROTECTION_EVENT = -0.02  # or any used past event beyond -2% for this portfolio
CAP_SHARE = 0.5  # no single idea moves more than half a portfolio
REBALANCE_MAX = 0.15
CASH_SHARE = 0.25
LONG_HEDGES = ["TLT", "IEF", "GLD"]


def _protection(u: Universe, imp: dict, prop: dict, book_events: list[dict], family_value: float, allow_shorts: bool) -> dict:
    """Protection ideas for one portfolio, long-only unless shorts are allowed.

    Sized for the worst case among the model estimate and the past events actually used:
    each idea's effect is computed with that case's own moves (a past event's real moves, or the
    model's). Every idea is capped at half the portfolio and at the family's total value."""
    value = imp["value"]
    invested = imp["invested"]
    worst_ev = min(book_events, key=lambda e: e["change"]) if book_events else None
    event_hit = worst_ev is not None and worst_ev["impact"] < NEEDS_PROTECTION_EVENT
    if imp["impact"] > NEEDS_PROTECTION_MODEL and not event_hit:
        return {"status": "not_needed", "ideas": [], "basis": None, "weak": False}
    head = prop[prop["mode"]]
    if worst_ev is not None and worst_ev["change"] < imp["change"]:
        basis = {"kind": "event", "id": worst_ev["id"], "name": worst_ev["name"], "impact": worst_ev["impact"], "change": worst_ev["change"]}
        hmove = dict(worst_ev["_moves"])
        amove = dict(worst_ev["_assets"])
    else:
        basis = {"kind": "model", "impact": imp["impact"], "change": imp["change"]}
        hmove = {r["symbol"]: r["move"] for r in imp["holdings"]}
        amove = {sym: float(head["mu"][u.idx(sym)]) for sym in LONG_HEDGES + INDEX_HEDGES if u.has(sym)}
    rows = [r for r in imp["holdings"]]
    hit = -sum(r["value"] * hmove.get(r["symbol"], r["move"]) for r in rows)
    if hit <= 0:
        return {"status": "not_needed", "ideas": [], "basis": basis, "weak": False}
    cap = min(CAP_SHARE * value, family_value)
    ideas = []

    def idea(kind: str, amount: float, offset: float, **extra):
        amount = min(amount, cap)
        ideas.append({"kind": kind, "amount": amount, "share_of_portfolio": amount / value, "offset": offset,
                      "covers": max(offset, 0.0) / hit, **extra})

    losers = sorted(rows, key=lambda r: r["value"] * hmove.get(r["symbol"], r["move"]))
    for r in losers[:2]:
        m = hmove.get(r["symbol"], r["move"])
        if m >= -0.002:
            continue
        amt = min(r["value"] / 2, cap)
        idea("trim", amt, amt * -m, symbol=r["symbol"])
    over = [(r, r["value"] - REBALANCE_MAX * value) for r in rows if r["weight"] > REBALANCE_MAX and hmove.get(r["symbol"], r["move"]) < -0.002]
    if over:
        amt = min(sum(x for _, x in over), cap)
        scale = amt / sum(x for _, x in over)
        idea("rebalance", amt, sum(x * scale * -hmove.get(r["symbol"], r["move"]) for r, x in over),
             symbols=[r["symbol"] for r, _ in over], target=REBALANCE_MAX)
    if invested > 0:
        amt = min(CASH_SHARE * invested, cap)
        idea("cash", amt, hit * amt / invested)
    for sym in LONG_HEDGES:
        m = amove.get(sym)
        if m is None or m <= 0.002 or invested <= 0:
            continue
        # shift money from everything (pro rata) into this asset: lose that slice of the hit, gain the asset's move
        per_dollar = m + hit / invested
        amt = min(hit / per_dollar, cap)
        idea("add", amt, amt * per_dollar, symbol=sym, move=m)
    if allow_shorts:
        C = u.C["calm"]
        w, cols = imp["_w"], imp["_cols"]
        var_p = float(w @ C[np.ix_(cols, cols)] @ w)
        best = None
        for hsym in INDEX_HEDGES:
            j = u.idx(hsym)
            m = amove.get(hsym)
            if j is None or not u.has(hsym) or var_p <= 0 or m is None or m >= -0.002:
                continue
            cov_ph = float(w @ C[cols, j])
            ratio = cov_ph / float(C[j, j])
            corr2 = cov_ph ** 2 / (var_p * float(C[j, j]))
            if ratio > 0 and (best is None or corr2 > best[1]):
                best = (hsym, corr2, ratio, m)
        if best:
            hsym, corr2, ratio, m = best
            amt = min(ratio * value, cap)
            idea("short", amt, amt * -m, symbol=hsym, inverse=INVERSE.get(hsym), risk_cut=corr2)
    ideas.sort(key=lambda x: -x["covers"])
    weak = not any(i["covers"] >= 0.2 for i in ideas)
    return {"status": "ideas", "ideas": ideas[:5], "basis": basis, "hit": hit, "weak": weak}


def _clean_book(imp: dict, top: int | None = None) -> dict:
    out = {k: v for k, v in imp.items() if not k.startswith("_")}
    if top is not None:
        rows = imp["holdings"]
        out["losers"] = [r for r in rows if r["change"] < 0][:top]
        out["gainers"] = [r for r in reversed(rows) if r["change"] > 0][:top]
        del out["holdings"]
    return out


def run(session: Session, shocks: list[dict], horizon: float = 1.0, event_ids: list[str] | None = None,
        pids: list[int] | None = None, regime: str = "normal", used_events: list[str] | None = None) -> dict:
    ports, family = books(session, pids)
    settings = services.get_settings(session)
    shocks = [s for s in shocks if s.get("symbol")][:MAX_SHOCKS]
    u = universe(session, [h.symbol for h in family.holdings] + [s["symbol"].upper() for s in shocks])
    warnings = []
    clean = []
    for s in shocks:
        sym = s["symbol"].upper()
        if not u.has(sym):
            warnings.append(f"No price history for {sym}; that shock was skipped.")
            continue
        if any(c["symbol"] == sym for c in clean):
            continue
        kind = s.get("kind") or ("factor" if sym in FACTOR_SYMBOLS else "stock")
        clean.append({"symbol": sym, "kind": kind, "move": float(s.get("move") or 0.0)})
    if not clean:
        return {"empty": True, "warnings": warnings or ["Add at least one shock."]}
    horizon = float(min(max(horizon or 1.0, 1.0), 52.0))
    regime = "selloff" if regime == "selloff" else "normal"
    prop = _propagate(u, clean, horizon, regime)
    prop["h"] = horizon
    head = prop[prop["mode"]]

    used = [e for e in (used_events or []) if e in EVENT_BY_ID]
    shown = list(dict.fromkeys(used + [e for e in (event_ids or []) if e in EVENT_BY_ID]))
    replays = [r for e in shown if (r := replay(u, EVENT_BY_ID[e], ports, family))]
    for r in replays:
        r["used"] = r["id"] in used
    used_replays = [r for r in replays if r["used"]]

    fam = _book_impact(u, family, prop)
    per = [_book_impact(u, b, prop, detail=False) for b in ports]
    allow = bool(settings.allow_short_hedges)
    protection = {}
    for b in per:
        evs = [{"id": r["id"], "name": r["name"], "impact": pr["impact"], "change": pr["change"], "_moves": r["_moves"], "_assets": r["_assets"]}
               for r in used_replays for pr in r["portfolios"] if pr["id"] == b["id"]]
        protection[str(b["id"])] = _protection(u, b, prop, evs, family.value, allow)
    fam_evs = [{"id": r["id"], "name": r["name"], "impact": r["family"]["impact"], "change": r["family"]["change"],
                "_moves": r["_moves"], "_assets": r["_assets"]} for r in used_replays]
    protection["family"] = _protection(u, fam, prop, fam_evs, family.value, allow)

    shocked = {u.symbols[i] for i in prop["S"]}
    market = []
    for sym, en, he, group in FACTORS:
        i = u.idx(sym)
        if i is None or not u.has(sym):
            continue
        market.append({"symbol": sym, "label": en, "label_he": he, "group": group, "move": float(head["mu"][i]),
                       "low": float(head["mu"][i] - head["sd"][i]), "high": float(head["mu"][i] + head["sd"][i]),
                       "shocked": sym in shocked, "pinned": sym == prop["pinned"]})

    pl = prop["plaus"]
    reverse = None
    if fam["impact"] < -0.001 and fam["value"] > 0:
        k = -0.10 / fam["impact"]
        scaled = prop["s"] * k
        rp = stress.plausibility(u.C["calm"], prop["S"], scaled, horizon, u.R)
        if rp["seen"] == 0 or rp["years"] is None:
            reverse = {"beyond": True, "scale": k}
        else:
            reverse = {"beyond": False, "target": -0.10, "scale": k, "years": rp["years"], "label": rp["label"],
                       "shocks": [{"symbol": u.symbols[i], "move": float(scaled[j])} for j, i in enumerate(prop["S"]) if u.symbols[i] != prop["pinned"]]}

    history = None
    if used_replays:
        worst = min(used_replays, key=lambda r: r["family"]["impact"])
        best = max(used_replays, key=lambda r: r["family"]["impact"])
        years = sorted({r["start"][:4] for r in used_replays})
        history = {"n": len(used_replays), "from": years[0], "to": years[-1],
                   "worst": {"id": worst["id"], "name": worst["name"], "impact": worst["family"]["impact"], "change": worst["family"]["change"]},
                   "best": {"id": best["id"], "name": best["name"], "impact": best["family"]["impact"], "change": best["family"]["change"]}}
    events_out = [{k: v for k, v in r.items() if not k.startswith("_")} for r in replays]
    return {
        "empty": False,
        "regime": regime,
        "horizon_weeks": horizon,
        "shocks": [{**c, "label": LABEL.get(c["symbol"], c["symbol"]), "z": z}
                   for c, z in zip(clean, pl["z"][: len(clean)])],
        "pinned": prop["pinned"],
        "plausibility": {k: pl[k] for k in ("years", "label", "d2", "seen", "weeks")},
        "family": _clean_book(fam),
        "portfolios": [_clean_book(b, top=3) for b in per],
        "portfolio_holdings": {str(b["id"]): _clean_book(b)["holdings"] for b in per},
        "market": market,
        "reverse": reverse,
        "protection": protection,
        "allow_shorts": allow,
        "history": history,
        "events": events_out,
        "stress_weeks": u.C.get("stress_weeks", 0),
        "weeks": int(len(u.R)),
        "warnings": warnings,
        "base_currency": settings.base_currency,
    }


def replay(u: Universe, ev: dict, ports: list[Book], family: Book) -> dict | None:
    """Today's holdings through a past event: actual moves where a holding traded then,
    otherwise estimated from that event's actual factor moves."""
    fmoves = {s: m for s in FACTOR_SYMBOLS if u.has(s) and (m := event_move(u, s, ev)) is not None}
    if "SPY" not in fmoves:
        return None
    h = event_weeks(ev)
    syms = list(fmoves)
    est = stress.conditional(u.C["calm"], [u.idx(s) for s in syms], np.array([fmoves[s] for s in syms]), h)

    def move_of(hd: Holding) -> tuple[float, bool]:
        m = event_move(u, hd.symbol, ev)
        if m is not None:
            return m, False
        col, _, _ = _column(u, hd)
        return float(est["mu"][col]), True

    cache: dict[str, tuple[float, bool]] = {}

    def book(b: Book) -> dict:
        total, estimated = 0.0, 0.0
        for hd in b.holdings:
            if hd.symbol not in cache:
                cache[hd.symbol] = move_of(hd)
            m, e = cache[hd.symbol]
            total += m * hd.value
            estimated += hd.value if e else 0.0
        return {"id": b.pid, "name": b.name, "impact": total / b.value, "change": total, "estimated_share": estimated / b.value}

    fam = book(family)
    rows = [{"symbol": hd.symbol, "move": cache[hd.symbol][0], "estimated": cache[hd.symbol][1], "change": cache[hd.symbol][0] * hd.value}
            for hd in family.holdings]
    rows.sort(key=lambda r: r["change"])
    key = ["SPY", "QQQ", "EIS", "TLT", "USO", "GLD", "ILS=X", "^VIX"]
    assets = {s: (fmoves[s] if s in fmoves else float(est["mu"][u.idx(s)])) for s in LONG_HEDGES + INDEX_HEDGES if u.has(s)}
    return {"id": ev["id"], "name": ev["name"], "he": ev["he"], "start": ev["start"], "end": ev["end"],
            "family": fam, "portfolios": [book(b) for b in ports],
            "factors": [{"symbol": s, "label": LABEL.get(s, s), "move": fmoves[s]} for s in key if s in fmoves],
            "worst": rows[:3], "best": [r for r in reversed(rows) if r["change"] > 0][:3],
            "_moves": {k: v[0] for k, v in cache.items()}, "_assets": assets}


# ------------------------------------------------------------------ model check

_check_cache: dict[tuple, dict] = {}
_DEFAULT_STORY = ["SPY", "TLT", "USO"]


def _story(ev: dict) -> list[str]:
    for th in THEME_BY_ID.values():
        if ev["id"] in th["events"]:
            return th["factors"][:4]
    return _DEFAULT_STORY


def model_check(session: Session, pids: list[int] | None = None) -> dict:
    """For each past event: estimate the model only from the 3 years before it, feed it the
    event's actual moves in the story's few factors, and compare its predictions for today's
    holdings with what they really did. A naive baseline (everything moves like the S&P) is
    scored alongside, so the model has to beat something."""
    ports, family = books(session, pids)
    u = universe(session, [h.symbol for h in family.holdings])
    key = (services._rev, dt.date.today(), tuple(h.symbol for h in family.holdings))
    if key in _check_cache:
        return _check_cache[key]
    rows, pairs = [], []
    for ev in EVENTS:
        start = pd.Timestamp(ev["start"])
        R = u.R[(u.R.index < start) & (u.R.index >= start - pd.Timedelta(weeks=156))]
        if len(R) < 104:
            continue
        story = [s for s in _story(ev) if s in R.columns and R[s].notna().sum() >= 100]
        actual_f = {s: event_move(u, s, ev) for s in story}
        if "SPY" not in actual_f or actual_f.get("SPY") is None or any(v is None for v in actual_f.values()):
            continue
        cov = stress.covariances(R)["calm"]
        cols = list(R.columns)
        S = [cols.index(s) for s in story]
        h = event_weeks(ev)
        cond = stress.conditional(cov, S, np.array([actual_f[s] for s in story]), h)
        cnt = stress.pairwise_moment(R)[1]
        preds = []
        for hd in family.holdings:
            if hd.symbol in story or hd.symbol not in cols:
                continue
            j = cols.index(hd.symbol)
            if cnt[j, j] < 52:
                continue
            act = event_move(u, hd.symbol, ev)
            if act is None:
                continue
            preds.append((hd.symbol, hd.value, float(cond["mu"][j]), act, float(cond["sd"][j])))
        if len(preds) < 2:
            continue
        spy = actual_f["SPY"]
        tot = sum(p[1] for p in preds)
        fam_pred = sum(p[1] * p[2] for p in preds) / tot
        fam_act = sum(p[1] * p[3] for p in preds) / tot
        meaningful = [p for p in preds if abs(p[3]) >= 0.01]
        hits = sum(1 for p in meaningful if np.sign(p[2]) == np.sign(p[3]))
        inside = sum(1 for p in preds if abs(p[3] - p[2]) <= p[4])
        rows.append({"id": ev["id"], "name": ev["name"], "start": ev["start"], "end": ev["end"], "story": story,
                     "holdings": len(preds), "family_predicted": fam_pred, "family_actual": fam_act, "sp500": spy,
                     "mae": float(np.mean([abs(p[2] - p[3]) for p in preds])),
                     "mae_naive": float(np.mean([abs(spy - p[3]) for p in preds])),
                     "hit_rate": hits / len(meaningful) if meaningful else None, "in_range": inside / len(preds)})
        pairs += [(p[2], p[3], spy, p[4]) for p in preds]
    if not rows:
        out = {"events": [], "summary": None}
    else:
        pred = np.array([p[0] for p in pairs])
        act = np.array([p[1] for p in pairs])
        spy = np.array([p[2] for p in pairs])
        sd = np.array([p[3] for p in pairs])
        mask = np.abs(act) >= 0.01
        out = {"events": rows, "summary": {
            "events": len(rows), "predictions": len(pairs),
            "hit_rate": float(np.mean(np.sign(pred[mask]) == np.sign(act[mask]))) if mask.any() else None,
            "mae": float(np.mean(np.abs(pred - act))), "mae_naive": float(np.mean(np.abs(spy - act))),
            "in_range": float(np.mean(np.abs(act - pred) <= sd)),
            "family_mae": float(np.mean([abs(r["family_predicted"] - r["family_actual"]) for r in rows])),
            "corr": float(np.corrcoef(pred, act)[0, 1]) if len(pairs) > 2 and np.std(pred) > 0 and np.std(act) > 0 else None,
        }}
    if len(_check_cache) > 8:
        _check_cache.clear()
    _check_cache[key] = out
    return out


# ------------------------------------------------------------------ library and saved scenarios

def library(session: Session) -> dict:
    st = services.get_settings(session)
    return {"factors": [{"symbol": s, "label": en, "label_he": he, "group": g} for s, en, he, g in FACTORS],
            "events": [{k: e[k] for k in ("id", "name", "he", "start", "end", "tags")} for e in EVENTS],
            "ai": bool((st.anthropic_key or "").strip())}


def saved_dict(s: Scenario) -> dict:
    return {"id": s.id, "name": s.name, "text": s.text, "shocks": json.loads(s.shocks or "[]"), "events": json.loads(s.events or "[]"),
            "used_events": json.loads(s.used_events or "[]"),
            "horizon_weeks": s.horizon_weeks, "created_at": s.created_at.isoformat()}
