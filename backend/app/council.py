"""AI Stock Council: several models rate every family holding (and the watchlist) Buy / Hold / Sell.

Definitions sent to every model: Buy = expected to beat SPY over 12 months from today's price; Hold = roughly
in line; Sell = expected to lag. "Insufficient data" is allowed and preferred over guessing.

Per batch of 5 stocks (each batch is its own pipeline, so ratings arrive as they finish and a run can span days):
1. Round 1, blind: each analyst rates the stocks from their data packs (order randomized, names hidden).
2. Round 2: each sees the round-1 tally and the others' theses (anonymized). The devil's advocate (rotates every
   run) argues against the majority. A model may revise once and must give a new reason to change.
3. Judge: merged thesis, the strongest dissent, and where a thesis contradicts the data pack.
The tally, consensus strength, agreement quality, crowded flag, flip-flops and per-person actions are computed
here, never by a model, and ratings are never averaged into one score.
"""
from __future__ import annotations

import datetime as dt
import json
import random
import threading
from collections import Counter

from sqlmodel import Session, select

from . import ai_jobs, openrouter, stock_pack
from .models import AiCall, AiJob, AiJobStep, CouncilRating, CouncilRun, CouncilVerdict, Portfolio, Watch

TAGS = ["valuation", "growth", "margins", "momentum", "analyst revisions", "balance sheet", "macro", "sector trend", "catalyst", "other"]
RATINGS = ["buy", "hold", "sell", "insufficient"]
BATCH = 5
LETTERS = "ABCDEFGH"
MIN_WEIGHT = 0.02
DEFAULT_CAP = 0.15  # max single-stock weight until each person has a policy (step 2)
DEFAULT_TAX = 0.25  # estimated tax rate on gains until each person has a policy
BIG_GAIN = 0.20  # a gain of at least this share of the position's value counts as "large"
FRAGILE_BETA = 1.3
MATERIAL_MOVE = 0.10
MATERIAL_REVISION = 0.02
HOLD_BAND = 0.05  # Hold is right when the stock ends within ±5 points of SPY
WEIGHT_AFTER = 20  # scored ratings before a model's track record weights the tally
SHRINK = 20
HORIZON_DAYS = {"3m": 91, "6m": 182, "12m": 365}
# funds that track the S&P 500 itself: "beat SPY" means nothing for them, so the council skips them
SP500_TRACKERS = {"SPY", "VOO", "IVV", "SPLG", "VFIAX", "FXAIX", "SWPPX", "CSPX.L", "^GSPC"}
LABEL_NOTE = "AI opinion based on the data shown. Not advice."

DEFINITIONS = (
    "You are one of several independent equity analysts on a council. Rate each stock against the S&P 500 (SPY) "
    "over the next 12 months from today's price: buy = expected to beat SPY; hold = roughly in line with SPY; "
    "sell = expected to lag SPY. Use \"insufficient\" when the data pack doesn't support a view: it is preferred over "
    "guessing. Rely only on the data packs given (prices, returns, valuation, growth, margins, analysts, revisions, "
    "earnings dates, factor exposures); do not use memory for numbers. Fields marked missing are unknown. "
    "Reason tags must come from this list: " + ", ".join(TAGS) + ".")


def _schema_r1() -> dict:
    return {"type": "object", "required": ["ratings"], "properties": {"ratings": {"type": "array", "items": {
        "type": "object", "required": ["ticker", "rating"], "properties": {
            "ticker": {"type": "string"}, "rating": {"enum": RATINGS}, "conviction": {"type": "integer", "minimum": 1, "maximum": 5},
            "thesis": {"type": "string"}, "main_risk": {"type": "string"}, "change_my_mind": {"type": "string"},
            "tags": {"type": "array", "items": {"enum": TAGS}}}}}}}


def _schema_r2() -> dict:
    return {"type": "object", "required": ["ratings"], "properties": {"ratings": {"type": "array", "items": {
        "type": "object", "required": ["ticker", "rating"], "properties": {
            "ticker": {"type": "string"}, "rating": {"enum": RATINGS}, "conviction": {"type": "integer", "minimum": 1, "maximum": 5},
            "changed": {"type": "boolean"}, "new_reason": {"type": "string"}, "tags": {"type": "array", "items": {"enum": TAGS}},
            "devils_argument": {"type": "string"}}}}}}


def _schema_judge() -> dict:
    return {"type": "object", "required": ["stocks"], "properties": {"stocks": {"type": "array", "items": {
        "type": "object", "required": ["ticker", "merged_thesis"], "properties": {
            "ticker": {"type": "string"}, "merged_thesis": {"type": "string"}, "strongest_dissent": {"type": "string"},
            "contradictions": {"type": "array", "items": {"type": "string"}}}}}}}


# ------------------------------------------------------------------ the rules (code, not models)

def tally(ratings: list[str]) -> dict[str, int]:
    c = Counter(r for r in ratings if r in RATINGS)
    return {r: c.get(r, 0) for r in RATINGS}


def consensus(t: dict[str, int]) -> tuple[str, str]:
    """(strength, majority rating). Strength: unanimous / strong (≥ 75%) / split / insufficient."""
    votes = {k: v for k, v in t.items() if k != "insufficient" and v}
    n = sum(votes.values())
    if n == 0:
        return "insufficient", ""
    top, cnt = max(votes.items(), key=lambda kv: (kv[1], kv[0] == "hold"))
    share = cnt / n
    tied = sum(1 for v in votes.values() if v == cnt) > 1
    majority = top if share > 0.5 and not tied else ""
    if share == 1 and n >= 2:
        return "unanimous", top
    if share >= 0.75 and n >= 3:
        return "strong", top
    return "split", majority


def agreement_quality(majority: str, finals: list[dict]) -> str:
    """'single reason' when one tag carries the majority (≥ 60% of their tag mentions), else 'diverse'."""
    tags = [t for f in finals if f["rating"] == majority for t in f.get("tags") or []]
    if not majority or not tags:
        return ""
    top = Counter(tags).most_common(1)[0][1]
    return "single reason" if top / len(tags) >= 0.6 else "diverse"


def crowded(strength: str, majority: str, pack: dict) -> bool:
    """Unanimous/strong Buy within 5% of the 52-week high with analysts already at Buy (mirrored for Sell near lows)."""
    if strength not in ("unanimous", "strong"):
        return False
    rng = pack.get("range_52w") or {}
    price = pack.get("price")
    an = ((pack.get("analysts") or {}).get("consensus") or "").lower()
    if majority == "buy":
        return pack.get("from_high") is not None and pack["from_high"] >= -0.05 and an in ("buy", "strong_buy")
    if majority == "sell":
        return bool(price and rng.get("low")) and price <= rng["low"] * 1.05 and an in ("sell", "strong_sell", "underperform")
    return False


def is_flipflop(prev: dict | None, now: dict, material: bool) -> bool:
    """A rating change with no new reason tag and no material data change is a flip-flop."""
    if not prev or prev["rating"] == now["rating"] or "insufficient" in (prev["rating"], now["rating"]):
        return False
    new_tags = set(now.get("tags") or []) - set(prev.get("tags") or [])
    return not new_tags and not material


def material_change(session: Session, ticker: str, prev_date: dt.date, prev_price: float | None, pack: dict, today: dt.date) -> bool:
    price = pack.get("price")
    if prev_price and price and abs(price / prev_price - 1) >= MATERIAL_MOVE:
        return True
    rev = ((pack.get("revisions") or {}).get("0y") or {}).get("estimate_change_30d")
    if rev is not None and abs(rev) >= MATERIAL_REVISION:
        return True
    try:
        from .earnings import report_dates

        return any(prev_date < d <= today for d, _t in report_dates(session, ticker))
    except Exception:
        return False


def is_fund(session: Session, ticker: str, kind: str = "") -> bool:
    """ETFs, mutual funds and indices (from the symbol's type, else Yahoo's quote type)."""
    if kind:
        return kind.lower() in ("etf", "fund", "index")
    qt = ((stock_pack.raw_fundamentals(session, ticker).get("info") or {}).get("quoteType") or "").upper()
    return qt in ("ETF", "MUTUALFUND", "INDEX")


def translate(session: Session, ticker: str, strength: str, majority: str, pack: dict, tally_text: str) -> list[dict]:
    """Council verdict -> a suggested action per portfolio that holds the stock (engine rules, not a model)."""
    from . import services
    from .scenarios import books

    if strength not in ("unanimous", "strong") or majority not in ("buy", "sell", "hold"):
        return []
    beta = ((pack.get("factor_exposures") or {}).get("betas") or {}).get("market")
    fragile = f" Fragile in a sell-off (market beta {beta:.2f})." if beta is not None and beta >= FRAGILE_BETA else ""
    ports, _fam = books(session, None)
    out = []
    for b in ports:
        h = next((x for x in b.holdings if x.symbol == ticker), None)
        if h is None:
            continue
        w = h.value / b.value if b.value else 0.0
        single_stock = not is_fund(session, ticker, h.kind)  # the stock cap doesn't apply to funds
        cap, rate = (DEFAULT_CAP if single_stock else 1.0), DEFAULT_TAX
        try:
            gain = services.position_gain(services.Context(session, b.pid), ticker)["gain_base"]
        except Exception:
            gain = None
        if majority == "buy":
            if w >= cap:
                action, why = "Hold (at limit)", f"Council says Buy ({tally_text}), but {ticker} is already {w:.0%} of the portfolio (limit {cap:.0%})."
            elif single_stock:
                action, why = "Consider adding", f"Council says Buy ({tally_text}). Room up to the {cap:.0%} limit: about ${(cap - w) * b.value:,.0f}."
            else:
                action, why = "Consider adding", f"Council says Buy ({tally_text})."
        elif majority == "sell":
            if gain is not None and gain > 0 and gain >= BIG_GAIN * h.value:
                tax = gain * rate
                action = f"Review sell: est. tax ${tax:,.0f}"
                why = (f"Council says Sell ({tally_text}). The position has a gain of about ${gain:,.0f}; selling all of it would cost an "
                       f"estimated ${tax:,.0f} in tax ({rate:.0%} of the gain, an estimate).")
            else:
                action, why = "Consider selling", f"Council says Sell ({tally_text})."
        else:
            if w <= cap:
                continue
            action, why = "Above limit: consider trimming", f"Council says Hold ({tally_text}) and {ticker} is {w:.0%} of the portfolio (limit {cap:.0%})."
        out.append({"portfolio_id": b.pid, "name": b.name, "symbol": ticker, "action": action, "why": why + fragile,
                    "weight": w, "value": h.value, "gain": gain})
    return out


# ------------------------------------------------------------------ universe and runs

def universe(session: Session, only_min_weight: bool | None = None) -> list[str]:
    from .scenarios import books
    from .services import get_settings

    if only_min_weight is None:
        only_min_weight = get_settings(session).council_min_weight
    ports, fam = books(session, None)
    best: dict[str, float] = {}
    for b in ports:
        for h in b.holdings:
            best[h.symbol] = max(best.get(h.symbol, 0.0), h.value / b.value if b.value else 0.0)
    fam_w = {h.symbol: h.value for h in fam.holdings}
    syms = [s for s in sorted(best, key=lambda s: -fam_w.get(s, 0))
            if s not in SP500_TRACKERS and (not only_min_weight or best[s] >= MIN_WEIGHT)]
    for w in session.exec(select(Watch)).all():
        if w.symbol not in syms and w.symbol not in SP500_TRACKERS:
            syms.append(w.symbol)
    return syms


def _labels(session: Session, seed: int) -> dict[str, dict]:
    roster = [r for r in openrouter.roster(session) if r["role"] != "judge"]
    order = list(range(len(roster)))
    random.Random(seed).shuffle(order)
    return {LETTERS[i]: {"role": roster[j]["role"], "models": roster[j]["models"]} for i, j in enumerate(order)}


def _judge_models(session: Session) -> list[str]:
    j = next((r for r in openrouter.roster(session) if r["role"] == "judge"), None)
    return (j or openrouter.roster(session)[0])["models"]


def start(session: Session, tickers: list[str] | None = None, kind: str = "full", background: bool = True) -> CouncilRun:
    n_before = len(session.exec(select(CouncilRun.id)).all())
    syms = [t.upper() for t in tickers] if tickers else universe(session)
    run = CouncilRun(kind=kind, universe=json.dumps(syms))
    session.add(run)
    session.commit()
    session.refresh(run)
    labels = _labels(session, run.id)
    run.labels = json.dumps(labels)
    run.devil = sorted(labels, key=lambda lbl: labels[lbl]["role"])[n_before % len(labels)]  # rotates every run
    run.batches = json.dumps([{"tickers": syms[i:i + BATCH], "phase": "pending", "jobs": {}} for i in range(0, len(syms), BATCH)])
    session.add(run)
    session.commit()
    if background:
        rid = run.id

        def prep():
            from .db import get_engine

            with Session(get_engine()) as s:
                _prepare(s, s.get(CouncilRun, rid))

        threading.Thread(target=prep, name=f"council-{rid}", daemon=True).start()
    else:
        _prepare(session, run)
    return run


def _prepare(session: Session, run: CouncilRun) -> None:
    """Build the data packs (cached for the day), then start the first batch."""
    for t in json.loads(run.universe):
        try:
            stock_pack.build(session, t)
        except Exception as exc:
            run.note = (run.note + f" {t}: no data pack ({exc}).")[:1000]
    run.status = "running"
    session.add(run)
    session.commit()
    _next_batch(session, run)


def _pack_for_prompt(p: dict) -> dict:
    keep = ("ticker", "name", "sector", "industry", "currency", "as_of", "price", "range_52w", "from_high", "returns", "valuation",
            "growth", "margins", "roe", "debt_to_equity", "analysts", "revisions", "next_earnings", "dividend_yield", "beta",
            "factor_exposures", "missing")
    out = {k: p.get(k) for k in keep}
    if isinstance(out.get("valuation"), dict):
        out["valuation"] = {k: v for k, v in out["valuation"].items() if k != "peers"}
    return out


def _batches(run: CouncilRun) -> list[dict]:
    return json.loads(run.batches or "[]")


def _save_batches(session: Session, run: CouncilRun, b: list[dict]) -> None:
    run.batches = json.dumps(b)
    session.add(run)
    session.commit()


def _next_batch(session: Session, run: CouncilRun) -> None:
    bs = _batches(run)
    k = next((i for i, b in enumerate(bs) if b["phase"] == "pending"), None)
    if k is None:
        run.status = "done"
        session.add(run)
        session.commit()
        return
    labels = json.loads(run.labels)
    tickers = bs[k]["tickers"]
    steps = []
    for lbl in sorted(labels):
        order = tickers[:]
        random.Random(f"{run.id}:{k}:{lbl}").shuffle(order)  # each model sees its own random order
        packs = [_pack_for_prompt(stock_pack.build(session, t)) for t in order]
        steps.append({"key": lbl, "role": labels[lbl]["role"], "models": labels[lbl]["models"], "schema": _schema_r1(), "schema_name": "council_ratings",
                      "messages": [{"role": "system", "content": DEFINITIONS},
                                   {"role": "user", "content": "DATA PACKS:\n" + json.dumps(packs, ensure_ascii=False, separators=(",", ":"))},
                                   {"role": "user", "content": "Rate every stock above, independently. For each: rating, conviction 1-5, a 2-sentence "
                                    "12-month thesis, the main risk, what would change your mind, and reason tags."}]})
    job = ai_jobs.create(session, f"council:{run.id}:{k}:round1", f"Council: {', '.join(tickers)} (round 1)", steps)
    bs[k]["phase"], bs[k]["jobs"]["round1"] = "round1", job.id
    _save_batches(session, run, bs)


def _answers(session: Session, job_id: int, tickers: list[str]) -> dict[str, dict[str, dict]]:
    """label -> ticker -> rating dict, keeping only tickers of the batch."""
    out: dict[str, dict[str, dict]] = {}
    for lbl, ans in ai_jobs.results(session, job_id).items():
        if not ans:
            continue
        for r in ans.get("ratings") or []:
            t = str(r.get("ticker", "")).upper().strip()
            if t in tickers and r.get("rating") in RATINGS:
                r["tags"] = [x for x in r.get("tags") or [] if x in TAGS]
                out.setdefault(lbl, {})[t] = r
    return out


def _anchors(session: Session, t: str) -> tuple[float | None, float | None]:
    from .market.cache import NoPriceData, load_history

    try:
        p = float(load_history(session, t)["close"].iloc[-1])
    except (NoPriceData, IndexError):
        p = None
    try:
        s = float(load_history(session, "SPY")["close"].iloc[-1])
    except (NoPriceData, IndexError):
        s = None
    return p, s


def _on_done(session: Session, job: AiJob) -> None:
    _, rid, k, phase = job.kind.split(":")
    run, k = session.get(CouncilRun, int(rid)), int(k)
    if run is None:
        return
    bs = _batches(run)
    if bs[k]["phase"] != phase:
        return
    tickers, labels = bs[k]["tickers"], json.loads(run.labels)
    if phase == "round1":
        r1 = _answers(session, job.id, tickers)
        for lbl, rs in r1.items():
            for t, r in rs.items():
                p, s = _anchors(session, t)
                session.add(CouncilRating(run_id=run.id, ticker=t, round=1, label=lbl, model=labels[lbl]["models"][0], rating=r["rating"],
                                          conviction=r.get("conviction"), thesis=str(r.get("thesis") or "")[:1200], risk=str(r.get("main_risk") or "")[:600],
                                          change_mind=str(r.get("change_my_mind") or "")[:600], tags=json.dumps(r["tags"]), anchor_price=p, anchor_spy=s))
        session.commit()
        if not r1:
            bs[k]["phase"] = "failed"
            _save_batches(session, run, bs)
            _next_batch(session, run)
            return
        _round2(session, run, k, bs, r1)
    elif phase == "round2":
        r1 = _ratings(session, run.id, tickers, 1)
        r2 = _answers(session, job.id, tickers)
        for lbl, rs in r2.items():
            for t, r in rs.items():
                first = r1.get(lbl, {}).get(t)
                rating, reason = r["rating"], str(r.get("new_reason") or "").strip()
                if first and rating != first["rating"] and not reason:
                    rating = first["rating"]  # a change needs a new reason: the round-1 rating stands
                p, s = _anchors(session, t)
                session.add(CouncilRating(run_id=run.id, ticker=t, round=2, label=lbl, model=labels[lbl]["models"][0], rating=rating,
                                          conviction=r.get("conviction") or (first or {}).get("conviction"),
                                          thesis=(first or {}).get("thesis", ""), tags=json.dumps(r["tags"] or (first or {}).get("tags") or []),
                                          new_reason=reason[:600], devils_argument=str(r.get("devils_argument") or "")[:1200] if lbl == run.devil else "",
                                          anchor_price=p, anchor_spy=s))
        session.commit()
        _judge(session, run, k, bs)
    elif phase == "judge":
        j = {str(x.get("ticker", "")).upper(): x for x in ((ai_jobs.results(session, job.id).get("judge") or {}).get("stocks") or [])}
        for t in tickers:
            verdict_for(session, run, t, j.get(t) or {})
        bs[k]["phase"] = "done"
        _save_batches(session, run, bs)
        _next_batch(session, run)


def _ratings(session: Session, run_id: int, tickers: list[str], rnd: int) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for r in session.exec(select(CouncilRating).where(CouncilRating.run_id == run_id, CouncilRating.round == rnd,
                                                      CouncilRating.ticker.in_(tickers))).all():
        out.setdefault(r.label, {})[r.ticker] = {"rating": r.rating, "conviction": r.conviction, "thesis": r.thesis, "tags": json.loads(r.tags),
                                                 "risk": r.risk, "devils_argument": r.devils_argument, "new_reason": r.new_reason}
    return out


def _round2(session: Session, run: CouncilRun, k: int, bs: list[dict], r1: dict[str, dict[str, dict]]) -> None:
    labels = json.loads(run.labels)
    tickers = bs[k]["tickers"]
    steps = []
    for lbl in sorted(r1):
        per_stock = []
        for t in tickers:
            votes = [r1[o][t]["rating"] for o in r1 if t in r1[o]]
            per_stock.append({"ticker": t, "round1_tally": tally(votes), "your_rating": r1[lbl].get(t),
                              "others": {f"Analyst {o}": {kk: r1[o][t].get(kk) for kk in ("rating", "conviction", "thesis", "tags")}
                                         for o in r1 if o != lbl and t in r1[o]}})
        ask = ("Round 2. For each stock you see the round-1 tally, your own rating and the other analysts' theses. You may revise "
               "your rating once; if you change it you must give a new reason in new_reason (a change without one is ignored). ")
        if lbl == run.devil:
            ask += ("You are the devil's advocate this run: for each stock, argue the strongest case against the majority in "
                    "devils_argument, then give your honest rating.")
        packs = [_pack_for_prompt(stock_pack.build(session, t)) for t in tickers]
        steps.append({"key": lbl, "role": labels[lbl]["role"], "models": labels[lbl]["models"], "schema": _schema_r2(), "schema_name": "council_revisions",
                      "messages": [{"role": "system", "content": DEFINITIONS},
                                   {"role": "user", "content": "DATA PACKS:\n" + json.dumps(packs, ensure_ascii=False, separators=(",", ":"))},
                                   {"role": "user", "content": ask + "\n" + json.dumps(per_stock, ensure_ascii=False)}]})
    job = ai_jobs.create(session, f"council:{run.id}:{k}:round2", f"Council: {', '.join(tickers)} (round 2)", steps)
    bs[k]["phase"], bs[k]["jobs"]["round2"] = "round2", job.id
    _save_batches(session, run, bs)


def _finals(session: Session, run_id: int, tickers: list[str]) -> dict[str, dict[str, dict]]:
    r1, r2 = _ratings(session, run_id, tickers, 1), _ratings(session, run_id, tickers, 2)
    out = {}
    for lbl, rs in r1.items():
        for t, r in rs.items():
            out.setdefault(lbl, {})[t] = r2.get(lbl, {}).get(t) or r
    return out


def _judge(session: Session, run: CouncilRun, k: int, bs: list[dict]) -> None:
    tickers = bs[k]["tickers"]
    finals = _finals(session, run.id, tickers)
    views = []
    for t in tickers:
        views.append({"ticker": t, "ratings": {f"Analyst {lbl}": rs[t] for lbl, rs in finals.items() if t in rs}})
    packs = [_pack_for_prompt(stock_pack.build(session, t)) for t in tickers]
    job = ai_jobs.create(session, f"council:{run.id}:{k}:judge", f"Council: {', '.join(tickers)} (judge)", [{
        "key": "judge", "role": "judge", "models": _judge_models(session), "schema": _schema_judge(), "schema_name": "council_judge",
        "messages": [{"role": "system", "content": DEFINITIONS.replace("one of several independent equity analysts on a council", "the judge of an equity council")},
                     {"role": "user", "content": "DATA PACKS:\n" + json.dumps(packs, ensure_ascii=False, separators=(",", ":"))},
                     {"role": "user", "content": "The analysts' final ratings and theses:\n" + json.dumps(views, ensure_ascii=False) +
                      "\n\nFor each stock: a merged 12-month thesis (2-3 sentences), the strongest dissent, and every place where a thesis "
                      "contradicts the data pack (quote the field). Do not give your own rating."}]}])
    bs[k]["phase"], bs[k]["jobs"]["judge"] = "judge", job.id
    _save_batches(session, run, bs)


def verdict_for(session: Session, run: CouncilRun, t: str, judge: dict) -> CouncilVerdict | None:
    from . import action_queue

    finals = {lbl: rs[t] for lbl, rs in _finals(session, run.id, [t]).items() if t in rs}
    if not finals:
        return None
    labels = json.loads(run.labels)
    pack = stock_pack.build(session, t)
    today = dt.date.today()
    tl = tally([f["rating"] for f in finals.values()])
    strength, majority = consensus(tl)
    votes = sum(v for kk, v in tl.items() if kk != "insufficient")
    # flip-flops against each model's last rating of this stock in an earlier run
    prev_v = session.exec(select(CouncilVerdict).where(CouncilVerdict.ticker == t, CouncilVerdict.run_id != run.id)
                          .order_by(CouncilVerdict.id.desc())).first()
    flips, counted = [], []
    for lbl, f in finals.items():
        model = labels[lbl]["models"][0]
        prev = session.exec(select(CouncilRating).where(CouncilRating.ticker == t, CouncilRating.model == model,
                                                        CouncilRating.run_id != run.id).order_by(CouncilRating.id.desc())).first()
        prev_d = {"rating": prev.rating, "tags": json.loads(prev.tags)} if prev else None
        mat = material_change(session, t, prev.rated_at, prev.anchor_price, pack, today) if prev else True
        if is_flipflop(prev_d, f, mat):
            flips.append(lbl)
            counted.append(prev.rating)  # not counted as a change
        else:
            counted.append(f["rating"])
    change = ""
    if prev_v:
        _s, maj_counted = consensus(tally(counted))
        if prev_v.majority and maj_counted and maj_counted != prev_v.majority:
            change = f"{prev_v.majority.title()} → {maj_counted.title()}, {tally(counted)[maj_counted]}/{votes}"
    da = next((f.get("devils_argument") for lbl, f in finals.items() if lbl == run.devil and f.get("devils_argument")), "")
    convs = [f["conviction"] for f in finals.values() if f.get("conviction")]
    p, s = _anchors(session, t)
    v = CouncilVerdict(run_id=run.id, ticker=t, tally=json.dumps(tl), consensus=strength, majority=majority,
                       quality=agreement_quality(majority, list(finals.values())), crowded=crowded(strength, majority, pack),
                       thesis=str(judge.get("merged_thesis") or "")[:2000], dissent=str(judge.get("strongest_dissent") or "")[:1200],
                       contradictions=json.dumps([str(x)[:400] for x in judge.get("contradictions") or []]), flipflops=json.dumps(flips),
                       change=change, conviction=round(sum(convs) / len(convs), 2) if convs else None,
                       upside=(pack.get("analysts") or {}).get("upside"), devils_argument=da or "", anchor_price=p, anchor_spy=s)
    w = weighted_tally(session, finals, labels)
    if w:
        v.weighted = json.dumps(w)
    session.add(v)
    session.commit()
    session.refresh(v)
    text = f"{tl.get(majority, 0)}/{votes}" if majority else "split"
    for a in translate(session, t, strength, majority, pack, text):
        action_queue.add(session, "council", run.id, f"{a['action']}: {t}", a["why"], a["action"], portfolio_id=a["portfolio_id"],
                         symbol=t, impact_amt=a["value"])
    return v


# ------------------------------------------------------------------ track record

def _excess(session: Session, t: str, start: dt.date, days: int, today: dt.date) -> float | None:
    """Stock minus SPY total return from the rating day to `days` later; None until that day has passed."""
    from .market.cache import NoPriceData, load_history

    end = start + dt.timedelta(days=days)
    if end > today:
        return None  # forward only
    try:
        a, b = load_history(session, t)["adj_close"], load_history(session, "SPY")["adj_close"]
    except NoPriceData:
        return None

    def ret(p):
        p0, p1 = p[p.index <= start], p[p.index <= end]
        if p0.empty or p1.empty or p1.index[-1] <= p0.index[-1]:
            return None
        return float(p1.iloc[-1] / p0.iloc[-1] - 1)

    r, s = ret(a), ret(b)
    return None if r is None or s is None else r - s


def hit(rating: str, excess: float) -> bool | None:
    if rating == "buy":
        return excess > 0
    if rating == "sell":
        return excess < 0
    if rating == "hold":
        return abs(excess) <= HOLD_BAND
    return None


def model_record(session: Session, today: dt.date | None = None) -> dict[str, dict]:
    """12-month record of each model's round-1 ratings."""
    today = today or dt.date.today()
    out: dict[str, dict] = {}
    for r in session.exec(select(CouncilRating).where(CouncilRating.round == 1)).all():
        if r.rating == "insufficient":
            continue
        ex = _excess(session, r.ticker, r.rated_at, HORIZON_DAYS["12m"], today)
        if ex is None:
            continue
        m = out.setdefault(r.model, {"n": 0, "hits": 0})
        m["n"] += 1
        m["hits"] += bool(hit(r.rating, ex))
    return out


def weighted_tally(session: Session, finals: dict[str, dict], labels: dict) -> dict | None:
    rec = model_record(session)
    if not any(v["n"] >= WEIGHT_AFTER for v in rec.values()):
        return None
    out = {r: 0.0 for r in RATINGS}
    for lbl, f in finals.items():
        m = rec.get(labels[lbl]["models"][0], {"n": 0, "hits": 0})
        w = (m["hits"] + SHRINK * 0.5) / (m["n"] + SHRINK) if m["n"] >= WEIGHT_AFTER else 0.5
        out[f["rating"]] += w / 0.5  # 1.0 = an average model
    return {k: round(v, 2) for k, v in out.items()}


def leaderboard(session: Session, today: dt.date | None = None) -> dict:
    """Hit rate and average excess return vs SPY per horizon and rating type: each model, the council, my calls, SPY."""
    from .models import Recommendation

    today = today or dt.date.today()
    rows: dict[str, dict] = {}

    def add(name: str, rating: str, ticker: str, start: dt.date):
        for h, days in HORIZON_DAYS.items():
            ex = _excess(session, ticker, start, days, today)
            if ex is None:
                continue
            ok = hit(rating, ex)
            if ok is None:
                continue
            cell = rows.setdefault(name, {}).setdefault(h, {}).setdefault(rating, {"n": 0, "hits": 0, "excess": 0.0})
            cell["n"] += 1
            cell["hits"] += ok
            cell["excess"] += ex if rating != "sell" else -ex  # a sell earns what the stock lagged by

    for r in session.exec(select(CouncilRating).where(CouncilRating.round == 1)).all():
        add(r.model, r.rating, r.ticker, r.rated_at)
    for v in session.exec(select(CouncilVerdict)).all():
        if v.majority:
            add("Council", v.majority, v.ticker, v.rated_at)
    for rec in session.exec(select(Recommendation).where(Recommendation.status == "approved")).all():
        add("My calls", "buy" if rec.action == "BUY" else "sell", rec.ticker, rec.date)
    out = []
    for name, hs in rows.items():
        for h, rs in hs.items():
            for rating, c in rs.items():
                out.append({"who": name, "horizon": h, "rating": rating, "n": c["n"], "hit_rate": c["hits"] / c["n"],
                            "avg_excess": c["excess"] / c["n"]})
    da_n = da_right = 0
    for v in session.exec(select(CouncilVerdict).where(CouncilVerdict.devils_argument != "")).all():
        ex = _excess(session, v.ticker, v.rated_at, HORIZON_DAYS["12m"], today) if v.majority else None
        if ex is None:
            continue
        da_n += 1
        majority_hit = hit(v.majority, ex)
        da_right += majority_hit is False  # the devil's advocate argued against the majority
    return {"rows": out, "spy": {"note": "Just holding SPY: 0 excess return by definition."},
            "devils_advocate": {"scored": da_n, "right": da_right, "rate": da_right / da_n if da_n else None}}


# ------------------------------------------------------------------ reading

def latest_verdicts(session: Session) -> dict[str, CouncilVerdict]:
    out: dict[str, CouncilVerdict] = {}
    for v in session.exec(select(CouncilVerdict).order_by(CouncilVerdict.id)).all():
        out[v.ticker] = v
    return out


def verdict_dict(v: CouncilVerdict) -> dict:
    return {"ticker": v.ticker, "run_id": v.run_id, "rated_at": v.rated_at.isoformat(), "tally": json.loads(v.tally),
            "weighted": json.loads(v.weighted) if v.weighted else None, "consensus": v.consensus, "majority": v.majority,
            "quality": v.quality, "crowded": v.crowded, "thesis": v.thesis, "dissent": v.dissent,
            "contradictions": json.loads(v.contradictions), "flipflops": json.loads(v.flipflops), "change": v.change,
            "conviction": v.conviction, "upside": v.upside, "devils_argument": v.devils_argument,
            "anchor_price": v.anchor_price, "anchor_spy": v.anchor_spy}


def run_status(session: Session, run: CouncilRun | None) -> dict | None:
    if run is None:
        return None
    bs = _batches(run)
    syms = json.loads(run.universe)
    rated = sum(len(b["tickers"]) for b in bs if b["phase"] in ("done",))
    job_ids = [j for b in bs for j in b["jobs"].values()]
    waiting = session.exec(select(AiJob).where(AiJob.id.in_(job_ids), AiJob.status == "waiting_quota")).first() if job_ids else None
    reqs = sum(c.requests for c in session.exec(select(AiCall).where(AiCall.job_id.in_(job_ids))).all()) if job_ids else 0
    return {"id": run.id, "status": run.status, "kind": run.kind, "created_at": run.created_at.isoformat(), "rated": rated,
            "total": len(syms), "requests": reqs, "devil": run.devil, "note": run.note,
            "waiting": waiting.note if waiting else None}


def overview(session: Session) -> dict:
    from .services import get_settings

    st = get_settings(session)
    last = session.exec(select(CouncilRun).order_by(CouncilRun.id.desc())).first()
    rows = []
    for t, v in sorted(latest_verdicts(session).items()):
        if t in SP500_TRACKERS:
            continue
        d = verdict_dict(v)
        try:
            p = stock_pack.build(session, t)
            d["from_high"], d["name"] = p.get("from_high"), p.get("name")
            d["move_since"] = (p["price"] / v.anchor_price - 1) if p.get("price") and v.anchor_price else None
        except Exception:
            d["from_high"] = d["name"] = d["move_since"] = None
        rows.append(d)
    return {"run": run_status(session, last), "rows": rows, "label": LABEL_NOTE,
            "settings": {"min_weight": st.council_min_weight, "weekday": st.council_weekday}}


def stock_detail(session: Session, t: str) -> dict:
    t = t.upper()
    v = latest_verdicts(session).get(t)
    if v is None:
        return {"ticker": t, "verdict": None, "models": [], "label": LABEL_NOTE}
    run = session.get(CouncilRun, v.run_id)
    labels = json.loads(run.labels) if run else {}
    models = []
    r1, r2 = _ratings(session, v.run_id, [t], 1), _ratings(session, v.run_id, [t], 2)
    for lbl in sorted(r1):
        a, b = r1[lbl].get(t), r2.get(lbl, {}).get(t)
        if not a:
            continue
        models.append({"label": lbl, "models": labels.get(lbl, {}).get("models", []), "devil": lbl == (run.devil if run else ""),
                       "round1": a, "round2": b, "final": (b or a)["rating"]})
    history = [verdict_dict(x) for x in session.exec(select(CouncilVerdict).where(CouncilVerdict.ticker == t).order_by(CouncilVerdict.id.desc())).all()]
    return {"ticker": t, "verdict": verdict_dict(v), "models": models, "pack": stock_pack.build(session, t), "history": history[:12],
            "label": LABEL_NOTE}


def actions_for(session: Session, pid: int) -> dict[str, dict]:
    """The council's suggested action for each holding of one portfolio (computed now, from the latest verdicts)."""
    out = {}
    for t, v in latest_verdicts(session).items():
        if t in SP500_TRACKERS:
            continue
        tl = json.loads(v.tally)
        votes = sum(n for k, n in tl.items() if k != "insufficient")
        text = f"{tl.get(v.majority, 0)}/{votes}" if v.majority else "split"
        try:
            pack = stock_pack.build(session, t)
        except Exception:
            pack = {}
        for a in translate(session, t, v.consensus, v.majority, pack, text):
            if a["portfolio_id"] == pid:
                out[t] = {"action": a["action"], "why": a["why"], "majority": v.majority, "consensus": v.consensus}
    return out


# ------------------------------------------------------------------ weekly schedule

def scheduled(session: Session, now: dt.datetime | None = None) -> CouncilRun | None:
    """Start the weekly run on the chosen day (once that day)."""
    from .services import get_settings

    now = now or dt.datetime.now()
    day = get_settings(session).council_weekday
    if day is None or day < 0 or now.weekday() != day:
        return None
    last = session.exec(select(CouncilRun).where(CouncilRun.kind == "full").order_by(CouncilRun.id.desc())).first()
    if last and (last.created_at.replace(tzinfo=dt.timezone.utc).astimezone().date() == now.date() or last.status in ("preparing", "running")):
        return None
    if not openrouter.api_key():
        return None
    return start(session)


_sched_started = False


def start_scheduler(interval: float = 3600.0) -> None:
    global _sched_started
    if _sched_started:
        return
    _sched_started = True
    from .db import get_engine

    def loop():
        ev = threading.Event()
        while True:
            try:
                with Session(get_engine()) as s:
                    scheduled(s)
            except Exception:
                pass
            ev.wait(interval)

    threading.Thread(target=loop, name="council-schedule", daemon=True).start()


ai_jobs.on_done("council:", _on_done)
