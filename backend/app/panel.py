"""The analyst panel: three paid models (ChatGPT, Gemini, Claude) each research a question on their own
and give a move for every holding; the app puts their numbers next to the engine's and adds them up.

- Each model designs its own research method, may search the web, and answers alone (no debate).
- A model only sees tickers with combined family weights. Per-portfolio results are computed here.
- The summary and every total are computed here from the models' numbers, never written by a model.
- The engine column is the local factor model on the same question, when the local rules can read it.
- Runs are kept; once the horizon has passed, each model's calls are compared with what prices did.
"""
from __future__ import annotations

import datetime as dt
import json
import statistics
import threading

from sqlmodel import Session, select

from . import context_pack, openrouter, privacy
from .models import AiCall, PanelRun

HORIZON_DAYS = {"1d": 1, "1w": 5, "2w": 10, "1m": 21, "3m": 63, "6m": 126, "12m": 252}
HORIZON_HE = {"1d": "ביום המסחר הקרוב", "1w": "בשבוע הקרוב", "2w": "בשבועיים הקרובים", "1m": "בחודש הקרוב",
              "3m": "בשלושת החודשים הקרובים", "6m": "בחצי השנה הקרובה", "12m": "בשנה הקרובה"}
HORIZON_EN = {"1d": "the next trading day", "1w": "the next week", "2w": "the next two weeks", "1m": "the next month",
              "3m": "the next three months", "6m": "the next six months", "12m": "the next year"}
SPLIT_PTS = 5.0  # models this many points apart on one stock (or on opposite sides) are "split"
ENGINE_GAP_PTS = 5.0  # the models' average this far from the engine is flagged
MAX_TOKENS = 14000  # the answer plus the model's own reasoning; also the ceiling on what one call can cost
MAX_FOCUS = 3
TIMEOUT = 300.0  # a model that searches the web and reasons can take minutes

GUIDANCE = (
    "You are a senior equity analyst of the calibre of the best research desks and hedge funds, working alone. "
    "First design your own research method for this question (for example: past analogs, earnings sensitivity, "
    "macro transmission, positioning and what is already priced in) and say in two to four sentences what it is. "
    "Then apply it to every holding listed. Use the data pack, your own knowledge and, when you have web search, "
    "current facts (cite what you used in `sources`). "
    "`move_pct` is your central estimate of the stock's total price move in its own currency over the horizon, "
    "given the question (if it describes something that may happen, assume it happens; if it already happened, "
    "estimate the reaction from here). `low_pct` and `high_pct` bound a range you would be surprised to see broken. "
    "When the question is about a surprise against expectations, say in `assumptions` what you take as already priced in. "
    "Give a number for every symbol in the list and only for those symbols. Be honest about low confidence. "
    "Write `method`, `briefing`, `market_view`, `assumptions` and every `reason` in Hebrew; keep tickers in English. "
    "`briefing` is the summary the portfolio manager needs first: what happened or would happen, the key facts and "
    "numbers, in five to eight short sentences. "
    "When the message lists `focus` symbols, the question is mainly about those companies. For each one give "
    "`report_summary` (what was reported or happened against expectations: the key numbers, the guidance, the "
    "market's first reaction; say plainly if you could not find it), your estimate of the stock's move over the "
    "next month (`move_1m_pct`) and over the next three months (`move_3m_pct`) from today's price, and `view`, "
    "one sentence with your call and what would change it. Write these in Hebrew too.")

_lock = threading.Lock()


def schema(symbols: list[str], focus: list[str] | None = None) -> dict:
    out = _schema(symbols)
    if focus:
        out["required"] = [*out["required"], "focus"]
        out["properties"]["focus"] = {"type": "array", "minItems": 1, "items": {
            "type": "object", "additionalProperties": True, "required": ["symbol", "report_summary", "move_1m_pct", "move_3m_pct", "view"],
            "properties": {"symbol": {"type": "string", "enum": focus}, "report_summary": {"type": "string"},
                           "move_1m_pct": {"type": "number"}, "move_3m_pct": {"type": "number"}, "view": {"type": "string"}}}}
    return out


def _schema(symbols: list[str]) -> dict:
    # an extra field a model adds on its own is ignored, not a reason to throw the whole answer away
    return {"type": "object", "additionalProperties": True,
            "required": ["method", "briefing", "market_view", "assumptions", "stocks"],
            "properties": {
                "method": {"type": "string"}, "briefing": {"type": "string"}, "market_view": {"type": "string"},
                "assumptions": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
                "sources": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
                "stocks": {"type": "array", "minItems": 1, "items": {
                    "type": "object", "additionalProperties": True, "required": ["symbol", "move_pct", "low_pct", "high_pct", "confidence", "reason"],
                    "properties": {"symbol": {"type": "string", "enum": symbols}, "move_pct": {"type": "number"},
                                   "low_pct": {"type": "number"}, "high_pct": {"type": "number"},
                                   "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                                   "reason": {"type": "string"}}}}}}


def clean_answer(ans: dict, symbols: list[str], focus: list[str] | None = None) -> dict:
    """Keep what is usable: known symbols once each, a sane ordered range, moves within -95%..+300%."""
    clip = lambda v: max(-95.0, min(300.0, float(v)))
    stocks, seen = {}, set()
    for s in ans.get("stocks") or []:
        sym = str(s.get("symbol", "")).upper()
        if sym not in symbols or sym in seen:
            continue
        try:
            lo, mid, hi = sorted(clip(s[k]) for k in ("low_pct", "move_pct", "high_pct"))
            mid = clip(s["move_pct"])
        except (KeyError, TypeError, ValueError):
            continue
        seen.add(sym)
        stocks[sym] = {"move": mid, "low": min(lo, mid), "high": max(hi, mid),
                       "confidence": s.get("confidence") if s.get("confidence") in ("low", "medium", "high") else "low",
                       "reason": str(s.get("reason") or "")[:400]}
    spot = {}
    for f in ans.get("focus") or []:
        sym = str(f.get("symbol", "")).upper()
        if sym not in (focus or []) or sym in spot:
            continue
        try:
            spot[sym] = {"m1": clip(f["move_1m_pct"]), "m3": clip(f["move_3m_pct"]),
                         "summary": str(f.get("report_summary") or "")[:1500], "view": str(f.get("view") or "")[:400]}
        except (KeyError, TypeError, ValueError):
            continue
    return {"method": str(ans.get("method") or "")[:1200], "briefing": str(ans.get("briefing") or "")[:2500],
            "market_view": str(ans.get("market_view") or "")[:1200],
            "assumptions": [str(x)[:300] for x in (ans.get("assumptions") or [])][:8],
            "sources": [str(x)[:300] for x in (ans.get("sources") or [])][:12], "stocks": stocks, "focus": spot}


# ------------------------------------------------------------------ what the models get

def focus_symbols(session: Session, text: str) -> list[str]:
    """Holdings the question names, by ticker or company name in English or Hebrew ("the CRWV report",
    "הדוח של אפל"): the answer then opens with those stocks."""
    import re

    from .scenarios import _companies, books

    _ports, family = books(session, None)
    held = {h.symbol for h in family.holdings}
    low = " " + text.lower() + " "
    hits: list[tuple[int, str]] = []
    for name, sym in _companies(session, family.holdings).items():
        if sym not in held or len(name) < 2:
            continue
        m = re.search(rf"(?<![a-z0-9א-ת])(?:[בלשומכה]{{0,2}})?{re.escape(name)}(?![a-z0-9א-ת])", low)
        if m and sym not in [s for _, s in hits]:
            hits.append((m.start(), sym))
    return [s for _, s in sorted(hits)][:MAX_FOCUS]


def question_message(session: Session, text: str, horizon: str, focus: list[str] | None = None) -> tuple[list[dict], list[str]]:
    """The one message every model gets: the question, the horizon, and the data pack (tickers, combined
    weights, sector, betas, the market snapshot). No names, no amounts, no split by portfolio."""
    from .models import SymbolMeta

    pack = context_pack.build(session, [], include_analogs=False)
    fam = pack.get("family") or {}
    metas = {m.ticker: m for m in session.exec(select(SymbolMeta)).all()}
    rows = []
    for h in fam.get("holdings") or []:
        sym = h["ticker"]
        m = metas.get(sym)
        rows.append({"symbol": sym, "company": (m.name if m and m.name else sym), "sector": (m.sector if m else "") or "",
                     "weight_pct": round(float(h.get("weight") or 0) * 100, 1),
                     "betas": {k: round(float(v), 2) for k, v in (h.get("betas") or {}).items() if isinstance(v, (int, float))}})
    symbols = [r["symbol"] for r in rows]
    body = {"question": text, "horizon": HORIZON_EN.get(horizon, horizon), "today": pack["today"],
            "holdings": rows, "market": pack.get("market"), **({"focus": focus} if focus else {}),
            "note": "weight_pct is the share of all the portfolios combined. Betas are weekly sensitivities to the named factors."}
    return [{"role": "user", "content": json.dumps(body, ensure_ascii=False)}], symbols


# ------------------------------------------------------------------ the engine, on the same question

def engine_moves(session: Session, text: str, horizon: str) -> dict:
    """The local factor model on the same question: a move per holding, or why there is none."""
    from . import impact, scenarios

    try:
        p = scenarios.parse(session, text, use_ai=False)
        if not p.get("understood") or not p.get("shocks"):
            return {"moves": {}, "why": "The local rules could not turn this question into market moves, so there is no engine figure."}
        weeks = HORIZON_DAYS.get(horizon, 21) / 5
        shocks = [{"symbol": s["symbol"], "kind": s["kind"], "move": s["move"]} for s in p["shocks"]]
        spec = impact.spec_from_shocks(session, shocks, weeks, text[:80], text, p.get("used_events"), None)
        out = impact.run(session, spec, analogs=False)
        if out.get("empty"):
            return {"moves": {}, "why": "No holdings to run."}
        return {"moves": {r["symbol"]: r["move"] * 100 for r in out["holdings"]["family"]}, "why": "",
                "shocks": [{"symbol": s["symbol"], "move": round(s["move"] * 100, 1)} for s in p["shocks"]],
                "family": {k: out["blended"][0][k] for k in ("median", "p5", "p95")}}
    except Exception as exc:  # the panel still answers without the engine
        return {"moves": {}, "why": f"The engine could not run this question ({str(exc)[:120]})."}


# ------------------------------------------------------------------ adding it up (here, not by a model)

def build_result(session: Session, answers: dict[str, dict], seats: list[dict], engine: dict, horizon: str, focus: list[str] | None = None) -> dict:
    from .scenarios import books

    ports, family = books(session, None)
    keys = [s["key"] for s in seats if s["key"] in answers]
    eng = engine.get("moves") or {}

    def table(book) -> dict:
        rows = []
        tot = {k: 0.0 for k in keys}
        cover = {k: 0.0 for k in keys}
        tot_avg = tot_eng = cov_avg = cov_eng = 0.0
        for h in sorted(book.holdings, key=lambda x: -x.value):
            w = h.value / book.value if book.value else 0.0
            by = {k: answers[k]["stocks"].get(h.symbol) for k in keys}
            mids = [v["move"] for v in by.values() if v]
            avg = statistics.fmean(mids) if mids else None
            e = eng.get(h.symbol)
            split = len(mids) >= 2 and (max(mids) - min(mids) >= SPLIT_PTS or (min(mids) < -0.5 and max(mids) > 0.5))
            row = {"symbol": h.symbol, "name": h.name, "weight": w, "value_amt": h.value,
                   "models": {k: ({"move": v["move"], "low": v["low"], "high": v["high"], "confidence": v["confidence"], "reason": v["reason"]} if v else None)
                              for k, v in by.items()},
                   "avg": avg, "low": min(mids) if mids else None, "high": max(mids) if mids else None,
                   "avg_amt": h.value * avg / 100 if avg is not None else None,
                   "engine": e, "split": bool(split),
                   "engine_gap": avg is not None and e is not None and abs(avg - e) >= ENGINE_GAP_PTS}
            rows.append(row)
            for k, v in by.items():
                if v:
                    tot[k] += w * v["move"]
                    cover[k] += w
            if avg is not None:
                tot_avg += w * avg
                cov_avg += w
            if e is not None:
                tot_eng += w * e
                cov_eng += w
        per_model = {k: {"pct": tot[k], "amt": book.value * tot[k] / 100, "coverage": cover[k]} for k in keys}
        vals = [v["pct"] for v in per_model.values()]
        return {"id": book.pid, "name": "All portfolios" if book.pid is None else book.name, "value_amt": book.value, "rows": rows,
                "models": per_model,
                "avg": {"pct": tot_avg, "amt": book.value * tot_avg / 100, "coverage": cov_avg} if cov_avg else None,
                "range": {"low": min(vals), "high": max(vals)} if vals else None,
                "engine": {"pct": tot_eng, "amt": book.value * tot_eng / 100, "coverage": cov_eng} if cov_eng else None}

    tables = [table(family)] + [table(b) for b in ports if b.holdings]
    spot = []
    for sym in focus or []:
        by = {k: answers[k].get("focus", {}).get(sym) for k in keys}
        got = {k: v for k, v in by.items() if v}
        if not got:
            continue
        h = next((x for x in family.holdings if x.symbol == sym), None)
        a1, a3 = statistics.fmean(v["m1"] for v in got.values()), statistics.fmean(v["m3"] for v in got.values())
        held = [{"id": b.pid, "name": b.name, "weight": x.value / b.value if b.value else 0.0, "amt_1m": x.value * a1 / 100, "amt_3m": x.value * a3 / 100}
                for b in ports for x in b.holdings if x.symbol == sym]
        spot.append({"symbol": sym, "name": h.name if h else sym, "models": by, "avg_1m": a1, "avg_3m": a3,
                     "low_1m": min(v["m1"] for v in got.values()), "high_1m": max(v["m1"] for v in got.values()),
                     "low_3m": min(v["m3"] for v in got.values()), "high_3m": max(v["m3"] for v in got.values()),
                     "weight": h.value / family.value if h and family.value else 0.0,
                     "amt_1m": h.value * a1 / 100 if h else None, "amt_3m": h.value * a3 / 100 if h else None,
                     "held_in": sorted(held, key=lambda r: -r["weight"])})
    lines = summary_he(tables, keys, seats, engine, horizon)
    for sp in spot[::-1]:  # the stock the question is about comes first
        n = sum(1 for v in sp["models"].values() if v)
        lines.insert(0, _ltr(sp["symbol"]) + f": ממוצע {n} המודלים הוא {_pct(sp['avg_1m'])} בחודש הקרוב ו-{_pct(sp['avg_3m'])} בשלושת החודשים הקרובים"
                     + (f" (טווח לחודש: {_pct(sp['low_1m'])} עד {_pct(sp['high_1m'])})." if n > 1 else "."))
    return {"tables": tables, "summary_he": lines, "focus": spot,
            "engine_why": engine.get("why") or "", "engine_shocks": engine.get("shocks") or []}


def _num(v: float) -> str:
    v = 0.0 if abs(v) < 0.05 else v  # never "-0.0%"
    return f"{'+' if v > 0 else ''}{v:.1f}%"


def _ltr(text: str) -> str:
    """Keeps an English name with its number in reading order inside a Hebrew sentence."""
    return "\u2066" + text + "\u2069"


def _pct(v: float) -> str:
    return _ltr(_num(v))


def summary_he(tables: list[dict], keys: list[str], seats: list[dict], engine: dict, horizon: str) -> list[str]:
    """A few Hebrew sentences from the numbers above. Nothing here comes from a model's prose."""
    fam = tables[0]
    if not fam.get("avg"):
        return ["אף מודל לא נתן תשובה שמישה."]
    label = {s["key"]: s["label"] for s in seats}
    hz = HORIZON_HE.get(horizon, horizon)
    n = len(keys)
    lines = []
    a = fam["avg"]["pct"]
    word = "ירידה" if a < -0.05 else "עלייה" if a > 0.05 else "כמעט בלי שינוי"
    each = ", ".join(_ltr(f"{label[k]} {_num(fam['models'][k]['pct'])}") for k in keys)
    lines.append(f"{hz}: ממוצע {n} המודלים לכלל התיקים הוא {word} של {_pct(a)} ({each}).")
    if fam.get("engine"):
        e = fam["engine"]["pct"]
        gap = abs(a - e)
        lines.append(f"מנוע החישוב (היסטוריית מחירים) נותן {_pct(e)}. " + ("המודלים והמנוע קרובים." if gap < 2 else
                     f"יש פער של {gap:.1f} נקודות בין המודלים למנוע, אז כדאי להתייחס למספרים בזהירות."))
    elif engine.get("why"):
        lines.append("למנוע החישוב אין מספר לשאלה הזאת, אז הטבלה מציגה רק את הערכות המודלים.")
    ports = [t for t in tables[1:] if t.get("avg")]
    if len(ports) >= 2:
        worst, best = min(ports, key=lambda t: t["avg"]["pct"]), max(ports, key=lambda t: t["avg"]["pct"])
        lines.append("הכי חשוף: " + _ltr(f"{worst['name']} ({_num(worst['avg']['pct'])})") + ". הכי פחות: " + _ltr(f"{best['name']} ({_num(best['avg']['pct'])})") + ".")
    rows = [r for r in fam["rows"] if r["avg"] is not None]
    if rows:
        hurt = sorted(rows, key=lambda r: r["avg"] * r["weight"])[:3]
        helped = sorted(rows, key=lambda r: -r["avg"] * r["weight"])[:3]
        h = [_ltr(f"{r['symbol']} ({_num(r['avg'])})") for r in hurt if r["avg"] < -0.05]
        g = [_ltr(f"{r['symbol']} ({_num(r['avg'])})") for r in helped if r["avg"] > 0.05]
        if h:
            lines.append("מה שמוריד הכי הרבה, לפי משקל בתיקים: " + ", ".join(h) + ".")
        if g:
            lines.append("מה שתורם הכי הרבה: " + ", ".join(g) + ".")
        split = [r["symbol"] for r in rows if r["split"]]
        if split:
            lines.append(f"המודלים חלוקים על {len(split)} מניות: " + _ltr(", ".join(split[:8]) + (" …" if len(split) > 8 else "")) + ". שם ההערכה הכי פחות בטוחה.")
        else:
            lines.append("המודלים מסכימים על הכיוון בכל המניות.")
    if n < len(seats):
        missing = [s["label"] for s in seats if s["key"] not in keys]
        lines.append("לא ענו: " + _ltr(", ".join(missing)) + ".")
    lines.append("אלה הערכות של מודלים, לא תחזית מחושבת ולא המלצה.")
    return lines


# ------------------------------------------------------------------ running

def _set_progress(run_id: int, **kw) -> None:
    from .db import get_engine

    with _lock, Session(get_engine()) as s:
        r = s.get(PanelRun, run_id)
        if r is None:
            return
        p = json.loads(r.progress or "{}")
        models = {**p.get("models", {}), **kw.pop("models", {})}
        p.update(kw)
        p["models"] = models
        r.progress = json.dumps(p)
        s.add(r)
        s.commit()


def estimate_cost(seats: list[dict], prompt_chars: int, web: bool) -> float:
    """A rough cost before running: the prompt in, about 9,000 tokens out (the answer and the model's own
    reasoning; never more than MAX_TOKENS), plus web search. The real cost is shown after each run."""
    total = 0.0
    for s in seats:
        if s.get("price_in") is None:
            continue
        total += (prompt_chars / 3.5) / 1e6 * s["price_in"] + 9000 / 1e6 * (s.get("price_out") or 0) + (0.03 if web else 0)
    return round(total, 2)


def start(session: Session, text: str, horizon: str, web: bool = True, background: bool = True) -> PanelRun:
    text = text.strip()[:600]
    problems = privacy.scan(session, text)
    if problems:
        raise openrouter.AiError("The question would send " + "; ".join(problems) + ". Write it without names or amounts: the app applies the answer to each portfolio itself.")
    if not openrouter.api_key():
        raise openrouter.NoKey("Add OPENROUTER_API_KEY to the .env file next to the app, then restart it.")
    b = openrouter.budget_status(session)
    if b["left"] <= 0:
        raise openrouter.BudgetReached(f"The monthly AI budget (${b['limit']:.2f}) is used up. Raise it in Settings, or wait for the 1st.")
    seats = [s for s in openrouter.panel_seats(session)]
    if not any(s["model"] for s in seats):
        raise openrouter.AiError("Couldn't find the models on OpenRouter (" + (seats[0].get("error") or "no model list") + "). Check the connection, or set the model ids in Settings.")
    run = PanelRun(text=text, horizon=horizon if horizon in HORIZON_DAYS else "1m", web=web,
                   seats=json.dumps([{"key": s["key"], "label": s["label"], "model": s["model"], "effort": s.get("effort")} for s in seats]),
                   progress=json.dumps({"pct": 3, "step": "Preparing the data", "models": {s["key"]: "waiting" for s in seats}}))
    session.add(run)
    session.commit()
    session.refresh(run)
    if background:
        threading.Thread(target=_run, args=(run.id,), name=f"panel-{run.id}", daemon=True).start()
    else:
        _run(run.id, parallel=False)
        session.refresh(run)
    return run


def _run(run_id: int, parallel: bool = True) -> None:
    from .db import get_engine

    try:
        with Session(get_engine()) as s:
            run = s.get(PanelRun, run_id)
            seats = json.loads(run.seats)
            text, horizon, web = run.text, run.horizon, run.web
            focus = focus_symbols(s, text)
            messages, symbols = question_message(s, text, horizon, focus)
            if not symbols:
                raise openrouter.AiError("There are no holdings to analyse yet.")
            prices = _last_prices(s, symbols)
        sch = schema(symbols, focus)
        live = [x for x in seats if x["model"]]
        share = 70 / max(1, len(live))
        answers: dict[str, dict] = {}
        errors: dict[str, str] = {}
        done = [0]
        _set_progress(run_id, pct=10, step="The models are researching", models={x["key"]: ("working" if x["model"] else "failed") for x in seats})
        for x in seats:
            if not x["model"]:
                errors[x["key"]] = "No model found for this seat on OpenRouter."

        def ask(seat: dict) -> None:
            with Session(get_engine()) as s2:
                try:
                    ans, _call = openrouter.chat_json(s2, f"panel_{seat['key']}", [seat["model"]], messages, sch, "analyst_answer",
                                                      max_tokens=MAX_TOKENS, job_id=None, step_key=f"panel:{run_id}", paid=True, web=web,
                                                      guidance=GUIDANCE, timeout=TIMEOUT, effort=seat.get("effort"))
                    cleaned = clean_answer(ans, symbols, focus)
                    if not cleaned["stocks"]:
                        raise openrouter.InvalidAnswer("The answer had no usable stock estimates.")
                    with _lock:
                        answers[seat["key"]] = cleaned
                    state = "done"
                except Exception as exc:
                    with _lock:
                        errors[seat["key"]] = str(exc)[:300]
                    state = "failed"
            with _lock:
                done[0] += 1
                pct = 10 + round(share * done[0])
            _set_progress(run_id, pct=pct, models={seat["key"]: state})

        if parallel:
            threads = [threading.Thread(target=ask, args=(x,), daemon=True) for x in live]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        else:
            for x in live:
                ask(x)
        with Session(get_engine()) as s:
            run = s.get(PanelRun, run_id)
            run.cost = float(sum(c.cost or 0.0 for c in s.exec(select(AiCall).where(AiCall.step_key == f"panel:{run_id}")).all()))
            s.add(run)
            s.commit()

            def step(pct: int, text_: str) -> None:
                # through this session, and committed: a second connection writing while this one holds
                # unsaved changes is what SQLite reports as "database is locked"
                r = s.get(PanelRun, run_id)
                r.progress = json.dumps({**json.loads(r.progress or "{}"), "pct": pct, "step": text_})
                s.add(r)
                s.commit()

            if not answers:
                label = {x["key"]: x["label"] for x in seats}
                why = " ".join(f"{label.get(k, k)} ({next((x['model'] for x in seats if x['key'] == k), '')}): {e}" for k, e in errors.items())
                run.status, run.note = "failed", f"No model answered. {why}"[:900]
                run.progress = json.dumps({**json.loads(run.progress), "pct": 100, "step": "Stopped"})
                s.add(run)
                s.commit()
                return
            step(85, "Running the engine on the same question")
            engine = engine_moves(s, text, horizon)
            step(95, "Adding it up for each portfolio")
            run = s.get(PanelRun, run_id)
            result = build_result(s, answers, seats, engine, horizon, focus)
            result["errors"] = errors
            from .services import _clean, get_settings

            result["currency"] = get_settings(s).base_currency
            run.answers, run.result, run.prices = json.dumps(answers, ensure_ascii=False), json.dumps(_clean(result), ensure_ascii=False), json.dumps(prices)
            run.status = "done"
            p = json.loads(run.progress or "{}")
            run.progress = json.dumps({**p, "pct": 100, "step": "Done"})
            s.add(run)
            s.commit()
    except Exception as exc:
        with Session(get_engine()) as s:
            run = s.get(PanelRun, run_id)
            if run is not None:
                run.status, run.note = "failed", str(exc)[:500]
                run.progress = json.dumps({**json.loads(run.progress or "{}"), "pct": 100, "step": "Stopped"})
                s.add(run)
                s.commit()


def _last_prices(session: Session, symbols: list[str]) -> dict:
    from .market.cache import load_history

    out = {}
    for t in symbols:
        try:
            f = load_history(session, t, fetch=False)
            if len(f):
                out[t] = {"date": f.index[-1].isoformat(), "close": float(f["close"].iloc[-1])}
        except Exception:
            continue
    return out


# ------------------------------------------------------------------ reading back, scoring

def payload(session: Session, run: PanelRun, full: bool = True) -> dict:
    out = {"id": run.id, "text": run.text, "horizon": run.horizon, "web": run.web, "status": run.status, "note": run.note,
           "created_at": run.created_at.isoformat(), "cost": round(run.cost or 0.0, 4),
           "progress": json.loads(run.progress or "{}"), "seats": json.loads(run.seats or "[]")}
    if full:
        answers = json.loads(run.answers or "{}")
        out["analysts"] = {k: {x: v[x] for x in ("method", "briefing", "market_view", "assumptions", "sources")} for k, v in answers.items()}
        out["result"] = json.loads(run.result or "{}")
        out["score"] = json.loads(run.scored) if run.scored else None
    return out


def listing(session: Session) -> list[dict]:
    rows = session.exec(select(PanelRun).order_by(PanelRun.id.desc())).all()
    return [payload(session, r, full=False) for r in rows[:40]]


def score_due(session: Session, today: dt.date | None = None) -> int:
    """Once a run's horizon has passed, compare each model's moves with what prices did since the run."""
    from .market.cache import load_history

    today = today or dt.date.today()
    n = 0
    for run in session.exec(select(PanelRun).where(PanelRun.status == "done", PanelRun.scored == "")).all():
        prices = json.loads(run.prices or "{}")
        if not prices:
            continue
        days = HORIZON_DAYS.get(run.horizon, 21)
        actual = {}
        ready = True
        for t, p in prices.items():
            try:
                f = load_history(session, t)
            except Exception:
                continue
            after = f[f.index > dt.date.fromisoformat(p["date"])]
            if len(after) < days:
                ready = False
                break
            actual[t] = (float(after["close"].iloc[days - 1]) / p["close"] - 1) * 100
        if not ready or not actual:
            continue
        answers = json.loads(run.answers or "{}")
        score = {}
        for k, a in answers.items():
            pairs = [(v["move"], actual[t], v["low"], v["high"]) for t, v in a["stocks"].items() if t in actual]
            sided = [(m, x) for m, x, _, _ in pairs if abs(m) >= 0.5]
            if not pairs:
                continue
            score[k] = {"n": len(pairs), "direction_hit_rate": (sum(1 for m, x in sided if (m > 0) == (x > 0)) / len(sided)) if sided else None,
                        "avg_error": statistics.fmean(abs(m - x) for m, x, _, _ in pairs),
                        "in_range_rate": sum(1 for _, x, lo, hi in pairs if lo <= x <= hi) / len(pairs)}
        run.scored = json.dumps({"models": score, "actual": actual, "scored_on": today.isoformat()})
        session.add(run)
        n += 1
    if n:
        session.commit()
    return n


def leaderboard(session: Session) -> list[dict]:
    """Each model over every scored run. Prices also moved for other reasons, so this is a rough guide."""
    agg: dict[str, dict] = {}
    for run in session.exec(select(PanelRun).where(PanelRun.scored != "")).all():
        for k, v in (json.loads(run.scored).get("models") or {}).items():
            a = agg.setdefault(k, {"runs": 0, "n": 0, "err": 0.0, "hits": 0.0, "hit_n": 0, "in_range": 0.0})
            a["runs"] += 1
            a["n"] += v["n"]
            a["err"] += v["avg_error"] * v["n"]
            a["in_range"] += v["in_range_rate"] * v["n"]
            if v.get("direction_hit_rate") is not None:
                a["hits"] += v["direction_hit_rate"] * v["n"]
                a["hit_n"] += v["n"]
    label = {s["key"]: s["label"] for s in openrouter.PANEL_SEATS}
    return [{"key": k, "label": label.get(k, k), "runs": a["runs"], "stocks": a["n"], "avg_error": a["err"] / a["n"],
             "direction_hit_rate": a["hits"] / a["hit_n"] if a["hit_n"] else None, "in_range_rate": a["in_range"] / a["n"]}
            for k, a in sorted(agg.items(), key=lambda kv: kv[1]["err"] / kv[1]["n"])]
