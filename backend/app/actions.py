"""From a scenario result to decisions: what each person could do, and where new money should go.

Options are long-only (trim, rebalance toward a cap, shift into TLT/IEF/GLD, raise cash, do nothing),
each capped at half the portfolio. For every option:
- benefit if the scenario happens: how much of the expected (probability-weighted middle) loss it avoids;
- cost if it goes the other way: what it gives up in the scenario's best branch, or, for a one-branch
  scenario, in the mirror image of it;
- its effect on concentration (largest holding, effective number of holdings).
Nothing here is sent anywhere; the message draft is for you to copy.
"""
from __future__ import annotations

import datetime as dt
import json
import re

import numpy as np
from sqlmodel import Session, select

from . import factors, impact, services
from .models import ResearchedSpec, Scenario, Watch
from .scenarios import books

CAP_SHARE = 0.5
REBALANCE_TO = 0.15
CASH_SHARE = 0.25
LONG_HEDGES = ["TLT", "IEF", "GLD"]
FACTOR_HE = {"market": "שוק המניות", "size": "מניות קטנות", "value": "ערך מול צמיחה", "momentum": "מומנטום", "quality": "איכות",
             "rates": "אג״ח ארוכות", "oil": "נפט", "gold": "זהב"}
FACTOR_EN = {"market": "the stock market", "size": "small caps", "value": "value vs growth", "momentum": "momentum", "quality": "quality",
             "rates": "long bonds", "oil": "oil", "gold": "gold"}


def _fname(f: str, he: bool = False) -> str:
    if f.startswith("sector_"):
        return f"סקטור {f[7:]}" if he else f"the {f[7:]} sector"
    return (FACTOR_HE if he else FACTOR_EN).get(f, f)


def concentration(values: dict[str, float], total: float | None = None) -> dict:
    """Largest holding and effective number of holdings, as shares of the whole portfolio (cash included)."""
    tot = total if total else sum(v for v in values.values() if v > 0)
    if tot <= 0:
        return {"top_symbol": None, "top_weight": 0.0, "effective": 0.0}
    w = {k: v / tot for k, v in values.items() if v > 0}
    top = max(w, key=w.get)
    return {"top_symbol": top, "top_weight": w[top], "effective": 1.0 / sum(x * x for x in w.values())}


# ------------------------------------------------------------------ per-person actions

def actions(session: Session, spec: dict, regime: str | None = None, edits: list[dict] | None = None, seed: int = impact.SEED,
            n: int = impact.N_DRAWS) -> dict:
    spec = impact.apply_edits(spec, edits or [])
    res = impact.run(session, spec, regime=regime, seed=seed, n=n, analogs=False)
    if res.get("empty"):
        return {"empty": True}
    base = res["base_currency"]
    to_amt = 1.0  # amounts are already in the display currency
    ports, family = books(session, None)
    syms = [h.symbol for h in family.holdings]
    probs = np.array([b["probability"] for b in res["branches"]])
    # expected (probability-weighted middle) moves of holdings, hedge assets and dollar cash
    sim = impact.simulate(session, spec, syms + LONG_HEDGES, base, regime=regime, n=200, seed=seed, noise=False)
    mids = np.array([b["mid_moves"] for b in sim["branches"]])  # branches x symbols
    cash_mids = np.array([b["cash_mid"] for b in sim["branches"]])
    expected = probs @ mids
    cash_exp = float(probs @ cash_mids)
    # the other way: the branch best for the family, or the mirror image of a one-branch scenario
    fam_mid = [b["books"][0]["mid"] for b in res["branches"]]
    best = int(np.argmax(fam_mid))
    if len(res["branches"]) > 1 and fam_mid[best] > 0:
        other, other_cash, other_label = mids[best], float(cash_mids[best]), f"if “{res['branches'][best]['name']}” happens instead"
    else:
        other, other_cash, other_label = -expected, -cash_exp, "if things go the other way"
    idx = {s: i for i, s in enumerate(syms + LONG_HEDGES)}

    people = []
    for b in ports:
        stats = next(x for x in res["blended"] if x["id"] == b.pid)
        key = str(b.pid)
        value_amt = b.value * to_amt
        held = {h.symbol: h.value * to_amt for h in b.holdings}
        invested = sum(held.values())
        cash_amt = value_amt - invested
        cap = min(CAP_SHARE * value_amt, family.value * to_amt)

        def inv_move(moves: np.ndarray) -> float:
            return sum(v * moves[idx[s]] for s, v in held.items()) / invested if invested > 0 else 0.0

        options = []

        def add(kind: str, title: str, amount: float, after: dict[str, float], effect_exp: float, effect_other: float, **extra):
            amount = min(amount, cap)
            conc0, conc1 = concentration(held, value_amt), concentration(after, value_amt)
            options.append({"kind": kind, "title": title, "amount": amount, "share_of_portfolio": amount / value_amt,
                            "benefit_amt": effect_exp, "cost_amt": max(0.0, -effect_other), "other_amt": effect_other,
                            "top_weight_before": conc0["top_weight"], "top_weight_after": conc1["top_weight"],
                            "effective_before": conc0["effective"], "effective_after": conc1["effective"], **extra})

        losers = sorted(held, key=lambda s: held[s] * expected[idx[s]])
        if losers and expected[idx[losers[0]]] < -0.001:
            s = losers[0]
            amt = min(held[s] / 2, cap)
            after = {**held, s: held[s] - amt}
            add("trim", f"Trim {s} by half", amt, after, amt * -expected[idx[s]] + amt * cash_exp, amt * -other[idx[s]] + amt * other_cash, symbol=s)
        over = {s: v - REBALANCE_TO * value_amt for s, v in held.items() if v > REBALANCE_TO * value_amt and expected[idx[s]] < -0.001}
        if over:
            amt = min(sum(over.values()), cap)
            sc = amt / sum(over.values())
            after = {s: (v - over[s] * sc if s in over else v) for s, v in held.items()}
            eff_e = sum(x * sc * (cash_exp - expected[idx[s]]) for s, x in over.items())
            eff_o = sum(x * sc * (other_cash - other[idx[s]]) for s, x in over.items())
            add("rebalance", f"Bring {', '.join(over)} down to {REBALANCE_TO:.0%} of the portfolio", amt, after, eff_e, eff_o, symbols=list(over), target=REBALANCE_TO)
        best_add = None
        for a in LONG_HEDGES:
            ga = expected[idx[a]] - inv_move(expected)
            if ga > 0.001 and (best_add is None or ga > best_add[1]):
                best_add = (a, ga)
        if best_add and invested > 0:
            a, gain = best_add
            loss_exp = -sum(v * expected[idx[s]] for s, v in held.items())
            amt = min(max(loss_exp, 0) / gain if gain > 0 else 0, cap) or min(0.1 * value_amt, cap)
            amt = min(amt, cap)
            frac = amt / invested
            after = {**{s: v * (1 - frac) for s, v in held.items()}, a: held.get(a, 0) + amt}
            add("add", f"Shift into {a}", amt, after, amt * gain, amt * (other[idx[a]] - inv_move(other)), symbol=a, move=float(expected[idx[a]]))
        if invested > 0:
            amt = min(CASH_SHARE * invested, cap)
            after = {s: v * (1 - amt / invested) for s, v in held.items()}
            add("cash", "Raise cash: sell a quarter of every holding", amt, after, amt * (cash_exp - inv_move(expected)), amt * (other_cash - inv_move(other)))
        options = [o for o in options if o["benefit_amt"] > 0]
        options.sort(key=lambda o: -(o["benefit_amt"] - 0.5 * o["cost_amt"]))
        options = options[:3]
        c0 = concentration(held, value_amt)
        options.append({"kind": "nothing", "title": "Do nothing", "amount": 0.0, "share_of_portfolio": 0.0, "benefit_amt": 0.0, "cost_amt": 0.0,
                        "other_amt": 0.0, "top_weight_before": c0["top_weight"], "top_weight_after": c0["top_weight"],
                        "effective_before": c0["effective"], "effective_after": c0["effective"]})
        hrows = res["holdings"].get(key, [])
        drivers_h = [h for h in hrows if h["change_amt"] < 0][:2] if stats["median"] < 0 else sorted(hrows, key=lambda h: -h["change_amt"])[:2]
        drivers_f = res["drivers"].get(key, [])
        drivers_f = drivers_f[:2] if stats["median"] < 0 else sorted(drivers_f, key=lambda d: -d["effect"])[:2]
        if stats["median"] < 0:
            line = (f"{b.name}'s biggest risks here are {' and '.join(h['symbol'] for h in drivers_h) or 'the holdings overall'}"
                    f"{', mostly through ' + ' and '.join(_fname(d['factor']) for d in drivers_f) if drivers_f else ''}.")
        else:
            line = f"{b.name} comes out ahead here, mainly from {' and '.join(h['symbol'] for h in drivers_h) or 'the holdings overall'}."
        person = {"id": b.pid, "name": b.name, "currency": base, "value_amt": value_amt, "expected_amt": stats["mean"] * value_amt, "expected": stats["mean"],
                  "median_amt": stats["median_amt"], "median": stats["median"], "bad_amt": stats["p5_amt"], "bad": stats["p5"],
                  "drivers_holdings": [{"symbol": h["symbol"], "change_amt": h["change_amt"], "move": h["move"]} for h in drivers_h],
                  "drivers_factors": drivers_f, "line": line, "options": options, "other_label": other_label}
        person["message"] = draft_message(person, spec)
        people.append(person)
    by_expected = [p["id"] for p in sorted(people, key=lambda p: p["expected_amt"])]
    by_bad = [p["id"] for p in sorted(people, key=lambda p: p["bad_amt"])]
    return {"empty": False, "title": spec.get("title", ""), "title_he": spec.get("title_he", ""), "horizon": spec.get("horizon"),
            "people": people, "rank_expected": by_expected, "rank_bad": by_bad, "other_label": other_label}


# ------------------------------------------------------------------ the message draft

def amt_he(v: float, currency: str = "USD") -> str:
    return f"{abs(v):,.0f} {'$' if currency == 'USD' else '₪'}"


def pct_he(v: float) -> str:
    return f"{abs(v) * 100:.1f}%"


def draft_message(p: dict, spec: dict) -> str:
    """A short Hebrew note in your voice. Only numbers from the result appear in it."""
    title = spec.get("title_he") or spec.get("title") or "התרחיש"
    lines = [f"היי {p['name']},", f"בדקתי מה יקרה לתיק שלך אם יתממש התרחיש \"{title}\"."]
    if p["median"] < 0:
        lines.append(f"בתרחיש הסביר התיק יורד בכ-{amt_he(p['median_amt'], p.get('currency', 'USD'))} ({pct_he(p['median'])}), ובמקרה רע (1 מתוך 20) הירידה יכולה להגיע ל-{amt_he(p['bad_amt'], p.get('currency', 'USD'))}.")
    else:
        lines.append(f"בתרחיש הסביר התיק עולה בכ-{amt_he(p['median_amt'], p.get('currency', 'USD'))} ({pct_he(p['median'])}). במקרה רע (1 מתוך 20) השינוי הוא {'-' if p['bad_amt'] < 0 else '+'}{amt_he(p['bad_amt'], p.get('currency', 'USD'))}.")
    if p["drivers_holdings"]:
        names = " ו-".join(h["symbol"] for h in p["drivers_holdings"])
        via = " ו".join(_fname(d["factor"], he=True) for d in p["drivers_factors"])
        lines.append(f"מה שהכי משפיע: {names}" + (f", בעיקר דרך {via}." if via else "."))
    opts = [o for o in p["options"] if o["kind"] != "nothing"]
    if opts:
        lines.append("אפשרויות שכדאי לשקול:")
        he = {"trim": lambda o: f"לממש חצי מ-{o['symbol']}", "rebalance": lambda o: f"להקטין את {', '.join(o['symbols'])} ל-{o['target'] * 100:.0f}% מהתיק",
              "add": lambda o: f"להעביר חלק ל-{o['symbol']}", "cash": lambda o: "להגדיל מזומן (רבע מכל החזקה)"}
        for i, o in enumerate(opts, 1):
            lines.append(f"{i}. {he[o['kind']](o)}: בערך {amt_he(o['amount'], p.get('currency', 'USD'))} ({pct_he(o['share_of_portfolio'])} מהתיק). חוסך כ-{amt_he(o['benefit_amt'], p.get('currency', 'USD'))} אם זה קורה"
                         + (f", ועולה כ-{amt_he(o['cost_amt'], p.get('currency', 'USD'))} אם זה הולך לכיוון השני." if o["cost_amt"] > 0.5 else "."))
        lines.append(f"{len(opts) + 1}. לא לעשות כלום.")
    else:
        lines.append("לא נראה שצריך לעשות משהו כרגע.")
    lines.append("זה לא ייעוץ, רק תמונת מצב. מה דעתך?")
    return "\n".join(lines)


def message_numbers(text: str) -> list[str]:
    return re.findall(r"\d[\d,]*(?:\.\d+)?", text)


# ------------------------------------------------------------------ new money

ALWAYS = ["SPY", "QQQ", "TLT", "GLD"]
PENALTY = 0.5
ROBUST_FLOOR = -0.0025


def candidates(session: Session) -> list[str]:
    _, family = books(session, None)
    watch = [w.symbol for w in session.exec(select(Watch).order_by(Watch.added_at)).all()]
    return list(dict.fromkeys(watch + [h.symbol for h in family.holdings] + factors.SECTOR_ETFS + ALWAYS))


def saved_specs(session: Session) -> list[dict]:
    """Every saved scenario as a spec: researched ones as they are, saved typed ones converted."""
    out = []
    for r in session.exec(select(ResearchedSpec)).all():
        out.append({"id": r.id, "title": r.title, "spec": json.loads(r.spec), "kind": "researched"})
    for s in session.exec(select(Scenario)).all():
        shocks = json.loads(s.shocks or "[]")
        if not shocks:
            continue
        spec = impact.spec_from_shocks(session, shocks, s.horizon_weeks, s.name, s.text, json.loads(s.used_events or "[]"),
                                       regime=None)  # by cause, like the scenario itself
        out.append({"id": f"saved-{s.id}", "title": s.name, "spec": spec, "kind": "typed"})
    return out


def normal_spec(session: Session, horizon: str = "1m") -> dict:
    """Normal conditions: no view on any factor (middle 0, no drift); each moves within ±2 standard
    deviations of its usual weekly moves, scaled to the horizon."""
    F = factors.factor_returns(session, dt.date.today())
    weeks = impact.HORIZON_DAYS.get(horizon, 21) / 5
    moves = {}
    for f in factors.BASE_IDS:
        if f in F.columns and F[f].notna().sum() > 20:
            sd = float(F[f].std()) * weeks ** 0.5 * 100
            moves[f] = {"low": -2 * sd, "mid": 0.0, "high": 2 * sd}
    return {"spec_version": 1, "kind": "scenario", "id": "normal", "title": "Normal conditions", "horizon": horizon,
            "branches": [{"name": "Normal", "probability": 1.0, "cause": "other", "narrative": "", "factor_moves": moves}]}


def new_money(session: Session, amount: float, currency: str = "USD", person: int | None = None,
              scenarios: list[dict] | None = None, weights: dict[str, float] | None = None, n: int = 3000, seed: int = impact.SEED) -> dict:
    base = services.get_settings(session).base_currency
    fx = impact._fx_now(session)
    # the amount in the display currency, at today's rate (no currency effect on any return)
    amount_amt = amount if currency == base else (amount * fx if currency == "USD" else amount / fx)
    cands = candidates(session)
    exp = factors.exposures(session, cands)
    specs = saved_specs(session) if scenarios is None else scenarios
    cols = [{"id": "normal", "title": "Normal conditions", "spec": normal_spec(session), "weight": (weights or {}).get("normal")}] + [
        {**s, "weight": (weights or {}).get(s["id"])} for s in specs]
    per = {c: {"scenarios": {}, "branch_medians": [], "p5s": []} for c in cands}
    for col in cols:
        sim = impact.simulate(session, col["spec"], cands, base, n=n, seed=seed, exp=exp)
        H = sim["H"]
        for j, c in enumerate(cands):
            med_by_branch = []
            for b in sim["branches"]:
                rows = b["rows"]
                med_by_branch.append((b["probability"], float(np.median(H[rows, j])) if len(rows) else 0.0))
            pw = sum(p * m for p, m in med_by_branch)
            p5 = float(np.percentile(H[:, j], 5))
            per[c]["scenarios"][col["id"]] = {"median": pw, "p5": p5, "branches": [m for _, m in med_by_branch]}
            per[c]["branch_medians"] += [m for _, m in med_by_branch]
            per[c]["p5s"].append(p5)
    total_w = {col["id"]: (col["weight"] if col["weight"] is not None else 1.0) for col in cols}
    wsum = sum(total_w.values())
    rows = []
    for c in cands:
        sc = per[c]["scenarios"]
        score_parts = {k: v["median"] - PENALTY * max(0.0, -v["p5"]) for k, v in sc.items()}
        score = sum(total_w[k] * score_parts[k] for k in sc) / wsum
        meds = per[c]["branch_medians"]
        ok = sum(1 for m in meds if m >= ROBUST_FLOOR)
        rows.append({"symbol": c, "score": score, "robust_count": ok, "branches": len(meds), "robust": ok >= 2 * len(meds) / 3,
                     "expected": sum(total_w[k] * sc[k]["median"] for k in sc) / wsum,
                     "worst_p5": min(per[c]["p5s"]), "expected_amt": amount_amt * sum(total_w[k] * sc[k]["median"] for k in sc) / wsum,
                     "bad_amt": amount_amt * min(per[c]["p5s"]),
                     "by_scenario": {k: v["median"] for k, v in sc.items()},
                     "low_confidence": (exp[c]["local"]["r2"] or 0) < factors.LOW_R2, "short_history": exp[c]["local"]["short_history"]})
    rows.sort(key=lambda r: (-r["robust"], -r["robust_count"] / max(r["branches"], 1), -r["score"]))
    note = None
    if not specs:
        note = "No saved scenarios yet, so this uses normal conditions only. Save or import scenarios to rank against them."
    if person is not None:
        ports, _ = books(session, [person])
        if ports:
            b = ports[0]
            held = {h.symbol: h.value for h in b.holdings}
            add_base = amount_amt
            for r in rows:
                after = {**held, r["symbol"]: held.get(r["symbol"], 0) + add_base}
                r["weight_after"] = after[r["symbol"]] / (b.value + add_base)
    return {"amount": amount, "currency": currency, "amount_amt": amount_amt, "person": person,
            "scenarios": [{"id": c["id"], "title": c["title"], "weight": total_w[c["id"]] / wsum} for c in cols],
            "rows": rows, "top": rows[:10], "note": note, "seed": seed, "n": n,
            "method": f"Each candidate is simulated under every scenario ({n:,} draws, seed {seed}) and under normal conditions. "
                      f"Score = probability-weighted median − {PENALTY} × the 5th-percentile loss. Robust = median of at least "
                      f"{ROBUST_FLOOR:+.2%} in two thirds of all branches. Normal conditions assume no drift."}
