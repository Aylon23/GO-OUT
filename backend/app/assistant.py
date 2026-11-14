"""The assistant: a conversation about a panel result and the portfolios.

- It answers from facts the app computed (the panel's numbers, the engine, each portfolio's totals and
  biggest drivers) and, with web search, from current sources. It is told not to invent portfolio numbers.
- When a follow-up is itself a scenario the local rules can read ("and if rates fall instead?"), the
  engine runs on it first and its numbers go into the facts.
- Privacy: portfolio names in the question become "Portfolio A", "Portfolio B" before anything is sent,
  and are put back in the answer here. Only percentages are sent; amounts are filled in here.
"""
from __future__ import annotations

import json
import re

from sqlmodel import Session, select

from . import openrouter, panel, privacy
from .models import ChatMessage, PanelRun, Portfolio

LETTERS = "ABCDEFGHIJKLMNOP"
HISTORY = 8  # earlier turns sent along
HE_PREFIX = "[בלשומכה]{0,2}"  # "לקרן", "שבתיק של קרן": Hebrew glues short words onto a name

SCHEMA = {"type": "object", "additionalProperties": True, "required": ["answer"],
          "properties": {"answer": {"type": "string"},
                         "caveat": {"type": "string"},
                         "followups": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                         "ask_panel": {"type": "boolean"}}}

GUIDANCE = (
    "You are the portfolio manager's own senior investment analyst: loyal, precise and candid. Answer in Hebrew "
    "(tickers and 'Portfolio A' labels stay in English), in short paragraphs, the conclusion first. "
    "FACTS holds what the app computed: use those numbers for anything about the portfolios, and say where a "
    "number comes from (the models' average, a named model, or the engine). Never invent a number about a "
    "portfolio or a holding: if FACTS does not have it, say so. With web search, cite current facts by source. "
    "Portfolios are named 'Portfolio A', 'Portfolio B' and so on: refer to them exactly like that. "
    "Money: you never see amounts, the app adds them. Whenever you state how much a whole portfolio would gain or lose, "
    "write it as [[A:-1.83]] (the portfolio's label, then the percent of that portfolio's value with the decimals FACTS gives, from FACTS or worked out "
    "from FACTS, e.g. a stock's contribution in points): the app shows it as the percent with the amount in the "
    "portfolio's currency. Never say an amount is unavailable, never use a currency symbol yourself, and never invent "
    "an example amount. "
    "If the question is about a surprise against expectations, separate what is already priced in from the surprise. "
    "Put what you are unsure about in `caveat`. `followups` are up to three useful next questions, in Hebrew. "
    "Set `ask_panel` to true when the question deserves a full run of all three analysts with a move for every "
    "holding (a new scenario or event), rather than a quick answer. You inform decisions; you do not make them.")


# ------------------------------------------------------------------ names <-> labels

def labels(session: Session) -> list[dict]:
    """Every portfolio that holds something, with its letter and the names it goes by."""
    from .scenarios import books

    ports, family = books(session, None)
    by_id = {p.id: p for p in session.exec(select(Portfolio)).all()}
    out = []
    for b in [x for x in ports if x.holdings]:
        p = by_id.get(b.pid)
        names = [b.name, *((p.aliases or "").split(",") if p else [])]
        out.append({"letter": LETTERS[len(out) % len(LETTERS)], "id": b.pid, "name": b.name, "value": b.value,
                    "names": sorted({n.strip() for n in names if len(n.strip()) >= 2}, key=len, reverse=True)})
    return out


def hide_names(text: str, labs: list[dict]) -> str:
    pairs = sorted(((n, lab["letter"]) for lab in labs for n in lab["names"]), key=lambda x: -len(x[0]))
    for name, letter in pairs:
        text = re.sub(rf"(?<!\w)({HE_PREFIX}){re.escape(name)}(?!\w)", lambda m: f"{m.group(1)}Portfolio {letter}", text, flags=re.I)
    return text


def show_names(text: str, labs: list[dict], currency: str) -> str:
    by = {lab["letter"]: lab for lab in labs}
    sym = "₪" if currency == "ILS" else "$"

    def amount(m: re.Match) -> str:
        lab = by.get(m.group(1).upper())
        try:
            v = float(m.group(2))
        except ValueError:
            return m.group(0)
        shown = f"{'+' if v > 0 else '−' if v < 0 else ''}{abs(v):.1f}%"
        if lab is None:
            return shown
        a = lab["value"] * v / 100
        # one left-to-right unit that does not break across lines inside Hebrew text
        return f"\u2066{shown}\u00a0({'+' if a > 0 else '−' if a < 0 else ''}{sym}{abs(a):,.0f})\u2069"

    text = re.sub(r"\[\[\s*([A-Pa-p])\s*:\s*([+\-−]?\d+(?:\.\d+)?)\s*%?\s*\]\]", lambda m: amount(re.match(r"(.)\|(.*)", f"{m.group(1)}|{m.group(2).replace('−', '-')}")), text)
    text = re.sub(r"\[\[\s*\w+\s*:\s*([+\-−]?\d+(?:\.\d+)?)\s*%?\s*\]\]", r"\1%", text)  # a label the app does not know
    return re.sub(r"\b(?:Portfolio|תיק)\s+([A-P])\b", lambda m: by[m.group(1)]["name"] if m.group(1) in by else m.group(0), text)


# ------------------------------------------------------------------ the facts

def _top(rows: list[dict], key, n=5) -> list[dict]:
    return sorted(rows, key=key)[:n]


def run_facts(run: PanelRun, labs: list[dict]) -> dict:
    """The panel result as percentages: totals for all portfolios and each one, every stock, the biggest drivers."""
    res = json.loads(run.result or "{}")
    tables = res.get("tables") or []
    if not tables:
        return {}
    letter = {lab["id"]: lab["letter"] for lab in labs}
    r1 = lambda v: None if v is None else round(v, 1)
    seats = {s["key"]: s["label"] for s in json.loads(run.seats or "[]")}

    def totals(t: dict) -> dict:
        return {"models_average_pct": r1((t.get("avg") or {}).get("pct")),
                "by_model_pct": {seats.get(k, k): r1(v["pct"]) for k, v in (t.get("models") or {}).items()},
                "engine_pct": r1((t.get("engine") or {}).get("pct"))}

    fam = tables[0]
    stocks = [{"symbol": r["symbol"], "weight_pct": r1(r["weight"] * 100), "models_average_pct": r1(r["avg"]),
               "by_model_pct": {seats.get(k, k): (r1(v["move"]) if v else None) for k, v in r["models"].items()},
               "engine_pct": r1(r["engine"]), "models_split": r["split"],
               "reasons": {seats.get(k, k): v["reason"][:160] for k, v in r["models"].items() if v}}
              for r in fam["rows"]]
    ports = {}
    for t in tables[1:]:
        lt = letter.get(t["id"])
        if not lt:
            continue
        rows = [r for r in t["rows"] if r["avg"] is not None]
        ports[f"Portfolio {lt}"] = {**totals(t), "biggest_drags": [{"symbol": r["symbol"], "weight_pct": r1(r["weight"] * 100), "points": r1(r["weight"] * r["avg"])}
                                                                 for r in _top(rows, lambda r: r["weight"] * r["avg"]) if r["avg"] < 0],
                                    "biggest_gains": [{"symbol": r["symbol"], "weight_pct": r1(r["weight"] * 100), "points": r1(r["weight"] * r["avg"])}
                                                      for r in _top(rows, lambda r: -r["weight"] * r["avg"]) if r["avg"] > 0]}
    answers = json.loads(run.answers or "{}")
    return {"question": run.text, "horizon": panel.HORIZON_EN.get(run.horizon, run.horizon), "asked_on": run.created_at.date().isoformat(),
            "all_portfolios": totals(fam), "portfolios": ports, "stocks": stocks,
            "engine_note": res.get("engine_why") or "",
            "stocks_in_focus": [{"symbol": f["symbol"], "models_average_1m_pct": r1(f["avg_1m"]), "models_average_3m_pct": r1(f["avg_3m"]),
                                 "held_in": [{"portfolio": f"Portfolio {letter[h['id']]}", "weight_pct": r1(h["weight"] * 100),
                                              # two decimals: the app turns this percent into an amount, so rounding shows
                                              "effect_on_portfolio_1m_pct": round(h["weight"] * f["avg_1m"], 2), "effect_on_portfolio_3m_pct": round(h["weight"] * f["avg_3m"], 2)}
                                             for h in f.get("held_in") or [] if h["id"] in letter],
                                 "by_model": {seats.get(k, k): {"next_month_pct": r1(v["m1"]), "next_3_months_pct": r1(v["m3"]), "view": v["view"][:200], "report_summary": v["summary"][:500]}
                                              for k, v in f["models"].items() if v}} for f in res.get("focus") or []],
            "analysts": {seats.get(k, k): {"briefing": a.get("briefing", "")[:700], "method": a.get("method", "")[:300]} for k, a in answers.items()}}


def book_facts(session: Session, labs: list[dict]) -> dict:
    """Without a panel run: what each portfolio holds, as weights."""
    from .scenarios import books

    ports, family = books(session, None)
    letter = {lab["id"]: lab["letter"] for lab in labs}
    w = lambda b: [{"symbol": h.symbol, "weight_pct": round(h.value / b.value * 100, 1)} for h in sorted(b.holdings, key=lambda x: -x.value)[:12]] if b.value else []
    return {"all_portfolios_holdings": w(family), "portfolios": {f"Portfolio {letter[b.pid]}": {"top_holdings": w(b)} for b in ports if b.pid in letter}}


def engine_on(session: Session, text: str, horizon: str, labs: list[dict]) -> dict | None:
    """If the message is itself a scenario the local rules can read, the engine's answer to it."""
    from . import impact, scenarios

    try:
        p = scenarios.parse(session, text, use_ai=False)
        if not p.get("understood") or not p.get("shocks"):
            return None
        said = scenarios.horizon_from_text(text)
        weeks = said or panel.HORIZON_DAYS.get(horizon, 21) / 5
        shocks = [{"symbol": s["symbol"], "kind": s["kind"], "move": s["move"]} for s in p["shocks"]]
        out = impact.run(session, impact.spec_from_shocks(session, shocks, weeks, text[:80], text, p.get("used_events"), None), analogs=False)
        if out.get("empty"):
            return None
        letter = {lab["id"]: lab["letter"] for lab in labs}
        r1 = lambda v: round(v * 100, 1)
        rows = out["holdings"]["family"]
        return {"read_as": [{"symbol": s["symbol"], "move_pct": r1(s["move"])} for s in p["shocks"]], "over_weeks": round(weeks, 1),
                "all_portfolios": {"typical_pct": r1(out["blended"][0]["median"]), "worst_of_20_pct": r1(out["blended"][0]["p5"])},
                "portfolios": {f"Portfolio {letter[b['id']]}": {"typical_pct": r1(b["median"]), "worst_of_20_pct": r1(b["p5"])} for b in out["blended"][1:] if b["id"] in letter},
                "most_hurt": [{"symbol": r["symbol"], "move_pct": r1(r["move"])} for r in rows[:5] if r["move"] < 0],
                "most_helped": [{"symbol": r["symbol"], "move_pct": r1(r["move"])} for r in rows[::-1][:5] if r["move"] > 0],
                "source": "the app's engine (factor model on ten years of prices), not an AI opinion"}
    except Exception:
        return None


# ------------------------------------------------------------------ asking

def messages(session: Session, run_id: int) -> list[dict]:
    rows = session.exec(select(ChatMessage).where(ChatMessage.run_id == run_id).order_by(ChatMessage.id)).all()
    return [{"id": m.id, "role": m.role, "text": m.text, "at": m.created_at.isoformat(), **json.loads(m.extra or "{}")} for m in rows]


def ask(session: Session, run_id: int, text: str, web: bool = True) -> list[dict]:
    from .services import get_settings

    text = text.strip()[:1200]
    if not text:
        raise openrouter.AiError("Write a question first.")
    run = session.get(PanelRun, run_id) if run_id else None
    if run_id and (run is None or run.status != "done"):
        raise openrouter.AiError("That question has no finished result to talk about.")
    seat = next((s for s in openrouter.panel_seats(session) if s["model"]), None)
    if seat is None:
        raise openrouter.AiError("Couldn't find a model on OpenRouter. Check the connection, or set the model ids under Models and budget.")
    labs = labels(session)
    sent = hide_names(text, labs)
    facts = run_facts(run, labs) if run else book_facts(session, labs)
    eng = engine_on(session, sent, run.horizon if run else "1m", labs)
    if eng:
        facts["engine_on_this_message"] = eng
    earlier = session.exec(select(ChatMessage).where(ChatMessage.run_id == run_id).order_by(ChatMessage.id.desc())).all()[:HISTORY][::-1]
    msgs = [{"role": "user", "content": "FACTS (computed by the app; percentages only):\n" + json.dumps(facts, ensure_ascii=False)}]
    msgs += [{"role": m.role, "content": m.sent} for m in earlier]
    msgs.append({"role": "user", "content": sent})
    # the first seat is the best value of the three; a conversation does not need the others
    ans, call = openrouter.chat_json(session, "assistant", [seat["model"]], msgs, SCHEMA, "assistant_answer", max_tokens=6000,
                                     step_key=f"chat:{run_id}", paid=True, web=web, guidance=GUIDANCE, timeout=180.0, effort="medium")
    raw = str(ans.get("answer") or "").strip()
    ccy = get_settings(session).base_currency
    shown = show_names(raw, labs, ccy)
    extra = {"caveat": show_names(str(ans.get("caveat") or ""), labs, ccy)[:600],
             "followups": [show_names(str(x), labs, ccy)[:200] for x in (ans.get("followups") or [])][:3],
             "ask_panel": bool(ans.get("ask_panel")), "engine": eng, "model": call.model_used or seat["model"], "cost": round(call.cost or 0.0, 4), "web": web}
    if eng:
        letter = {lab["letter"]: lab["name"] for lab in labs}
        extra["engine"] = {**eng, "portfolios": {letter.get(k.split()[-1], k): v for k, v in eng["portfolios"].items()}}
    session.add(ChatMessage(run_id=run_id, role="user", text=text, sent=sent))
    session.add(ChatMessage(run_id=run_id, role="assistant", text=shown, sent=raw, extra=json.dumps(extra, ensure_ascii=False)))
    session.commit()
    return messages(session, run_id)
