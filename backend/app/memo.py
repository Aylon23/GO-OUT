"""Analyst memo for a scenario, written from the engine's output only.

Every number in the memo comes from the engine result, the market data, or the spec's own research text.
Sections: 1 Bottom line · 2 Branches · 3 Transmission · 4 Evidence · 5 What markets price now ·
6 Signposts · 7 Implications per person · 8 Options with trade-offs · 9 Confidence.
"""
from __future__ import annotations

import datetime as dt
import re

import numpy as np
from sqlmodel import Session

from . import actions, factors, impact
from .market.cache import NoPriceData, load_history

MARKETS = [("^VIX", "VIX", "מדד הפחד (VIX)"), ("CL=F", "Crude oil", "נפט גולמי"), ("^TNX", "US 10-year yield", "תשואת אג״ח 10 שנים בארה״ב")]
FACTOR_HE = actions.FACTOR_HE
FACTOR_EN = {f["id"]: f["label"] for f in factors.FACTORS}
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


class Facts:
    """Formats numbers and remembers every string it produced, so the memo can be checked."""

    def __init__(self, currency: str = "USD") -> None:
        self.used: set[str] = set()
        self.sym = "$" if currency == "USD" else "₪"

    def _keep(self, s: str) -> str:
        self.used.update(NUM.findall(s))
        return s

    def pct(self, v: float | None, d: int = 1, signed: bool = True) -> str:
        if v is None:
            return "–"
        s = f"{abs(v) * 100:.{d}f}%"
        sign = ("+" if v > 0 else "−" if v < 0 else "") if signed else ""
        return self._keep(sign + s)

    def amt(self, v: float | None, signed: bool = True) -> str:
        """An amount in the display currency."""
        if v is None:
            return "–"
        sign = ("+" if v > 0 else "−" if v < 0 else "") if signed else ""
        return self._keep(f"{sign}{self.sym}{abs(v):,.0f}")

    def num(self, v: float, d: int = 2) -> str:
        return self._keep(f"{v:,.{d}f}")

    def text(self, s: str) -> str:
        """Research text from the spec: its numbers count as sources, not inventions."""
        return self._keep(s or "")


def market_now(session: Session, f: Facts, he: bool) -> list[str]:
    out = []
    for sym, en, hel in MARKETS:
        try:
            df = load_history(session, sym)
        except NoPriceData:
            continue
        if df is None or df.empty:
            continue
        p = df["close"].astype(float)
        last_day = p.index[-1]
        yr = p[p.index >= last_day - dt.timedelta(days=365)]
        lo, hi, now = float(yr.min()), float(yr.max()), float(p.iloc[-1])
        pctile = float((yr <= now).mean())
        if he:
            out.append(f"{hel}: {f.num(now)} (טווח שנה {f.num(lo)}–{f.num(hi)}, אחוזון {f.pct(pctile, 0, False)})")
        else:
            out.append(f"{en}: {f.num(now)} (1-year range {f.num(lo)}–{f.num(hi)}, {f.pct(pctile, 0, False)} of the way up)")
    return out


def build(session: Session, spec: dict, report: dict | None = None, lang: str = "he", regime: str | None = None,
          n: int = impact.N_DRAWS) -> dict:
    he = lang == "he"
    from . import services

    currency = services.get_settings(session).base_currency
    f = Facts(currency)
    res = impact.run(session, spec, regime=regime, n=n)
    if res.get("empty"):
        return {"empty": True}
    act = actions.actions(session, spec, regime=regime, n=n)  # the same run as the headline and the page
    fam = res["blended"][0]
    horizon = {"1w": ("שבוע", "1 week"), "2w": ("שבועיים", "2 weeks"), "1m": ("חודש", "1 month"), "3m": ("3 חודשים", "3 months"),
               "6m": ("חצי שנה", "6 months"), "12m": ("שנה", "12 months")}.get(spec.get("horizon", "1m"), ("", spec.get("horizon", "")))
    hz = horizon[0] if he else horizon[1]
    title = (spec.get("title_he") if he else None) or spec.get("title", "")
    S = []

    # 1 bottom line
    if he:
        lines = [f"בטווח של {hz}: התרחיש הסביר למשפחה {f.amt(fam['median_amt'])} ({f.pct(fam['median'])}). במקרה רע (1 מתוך 20) {f.amt(fam['p5_amt'])} ({f.pct(fam['p5'])}). "
                 f"הסיכוי להפסד {f.pct(fam['prob_loss'], 0, False)}."]
    else:
        lines = [f"Over {hz}: most likely the family {f.amt(fam['median_amt'])} ({f.pct(fam['median'])}). Bad case (1 in 20): {f.amt(fam['p5_amt'])} "
                 f"({f.pct(fam['p5'])}). Chance of a loss: {f.pct(fam['prob_loss'], 0, False)}."]
    if spec.get("summary"):
        lines.append(f.text(spec["summary"]))
    S.append(("שורה תחתונה" if he else "Bottom line", lines))

    # 2 branches
    lines = []
    for b, sb in zip(res["branches"], spec["branches"]):
        bf = b["books"][0]
        if he:
            lines.append(f"{f.text(sb['name'])} — {f.pct(b['probability'], 0, False)}: משפחה {f.pct(bf['median'])} ({f.pct(bf['p5'])} עד {f.pct(bf['p95'])}). {f.text(sb.get('narrative', ''))}")
        else:
            lines.append(f"{f.text(sb['name'])} — {f.pct(b['probability'], 0, False)}: family {f.pct(bf['median'])} ({f.pct(bf['p5'])} to {f.pct(bf['p95'])}). {f.text(sb.get('narrative', ''))}")
    S.append(("ענפי התרחיש" if he else "Branches", lines))

    # 3 transmission
    lines = []
    names = FACTOR_HE if he else FACTOR_EN
    for sb in spec["branches"]:
        moves = sb.get("factor_moves") or {}
        links = sb.get("transmission") or []
        if links:
            chain = "; ".join(f"{'טריגר' if (l['from'] == 'trigger' and he) else ('trigger' if l['from'] == 'trigger' else names.get(l['from'], l['from']))} → "
                              f"{names.get(l['to'], l['to'])} {f.pct(moves[l['to']]['mid'] / 100) if l['to'] in moves else ''}"
                              f"{(': ' + f.text(l['explanation'])) if l.get('explanation') else ''}" for l in links)
        else:
            chain = ", ".join(f"{names.get(k, k)} {f.pct(v['mid'] / 100)}" for k, v in moves.items())
        lines.append(f"{f.text(sb['name'])}: {chain}")
    S.append(("איך זה מתגלגל" if he else "Transmission", lines))

    # 4 evidence
    lines = []
    for a in res.get("analogs") or []:
        if a.get("no_data"):
            lines.append(f"{f.text(a['name'])}: {'אין נתוני מחיר' if he else 'no price data'}")
            continue
        lines.append(f"{f.text(a['name'])} ({f.text(a['start'])} – {f.text(a['end'])}): {'התיקים של היום היו זזים' if he else 'today’s holdings would have moved'} {f.pct(a['family']['impact'])}")
    for x in (report or {}).get("analogs", []):
        f.text(x["name"])
        bad = [r for r in x["factors"] if r["contradicts"]]
        for r in bad:
            lines.append((f"אזהרה: ב-{x['name']} {names.get(r['factor'], r['factor'])} זז בפועל {f.pct(r['actual'] / 100)}, בניגוד ל-{f.pct(r['claimed_mid'] / 100)} בענף."
                          if he else f"Warning: in {x['name']}, {names.get(r['factor'], r['factor'])} actually moved {f.pct(r['actual'] / 100)}, against the branch's {f.pct(r['claimed_mid'] / 100)}."))
    lines += [f.text(w) for w in res.get("warnings", [])]
    S.append(("ראיות" if he else "Evidence", lines or [("אין אנלוגים היסטוריים." if he else "No historical analogs.")]))

    # 5 what markets price now
    S.append(("מה השווקים מתמחרים עכשיו" if he else "What markets price now", market_now(session, f, he) or [("אין נתונים." if he else "No data.")]))

    # 6 signposts
    lines = []
    for sb in spec["branches"]:
        for sp in sb.get("signposts") or []:
            lines.append(f"{f.text(sp['indicator'])} {sp['condition']} {f.text(str(sp['value']))}: {f.text(sp['meaning'])} ({f.text(sb['name'])})")
    S.append(("סימנים לעקוב אחריהם" if he else "Signposts", lines or [("אין." if he else "None given.")]))

    # 7 implications per person
    lines = []
    for p in sorted(act["people"], key=lambda x: x["median_amt"]):
        if he:
            lines.append(f"{f.text(p['name'])}: {f.amt(p['median_amt'])} ({f.pct(p['median'])}), במקרה רע {f.amt(p['bad_amt'])}. "
                         + (f"מה שמשפיע: {f.text(', '.join(h['symbol'] for h in p['drivers_holdings']))}." if p["drivers_holdings"] else ""))
        else:
            lines.append(f"{f.text(p['name'])}: {f.amt(p['median_amt'])} ({f.pct(p['median'])}), bad case {f.amt(p['bad_amt'])}. "
                         + (f"Driven by {f.text(', '.join(h['symbol'] for h in p['drivers_holdings']))}." if p["drivers_holdings"] else ""))
    S.append(("השלכות לכל אחד" if he else "Implications per person", lines))

    # 8 options
    lines = []
    for p in act["people"]:
        opts = [o for o in p["options"] if o["kind"] != "nothing"][:2]
        if not opts:
            continue
        parts = []
        for o in opts:
            if he:
                parts.append(f"{f.text(o['title'])} ({f.pct(o['share_of_portfolio'], 0, False)} מהתיק): חוסך {f.amt(o['benefit_amt'], False)} אם קורה, עולה {f.amt(o['cost_amt'], False)} אם לא")
            else:
                parts.append(f"{f.text(o['title'])} ({f.pct(o['share_of_portfolio'], 0, False)} of the portfolio): saves {f.amt(o['benefit_amt'], False)} if it happens, costs {f.amt(o['cost_amt'], False)} if not")
        lines.append(f"{f.text(p['name'])}: " + "; ".join(parts))
    S.append(("אפשרויות ומחירן" if he else "Options with trade-offs", lines or [("לא נדרשת פעולה." if he else "No action needed.")]))

    # 9 confidence
    conf = spec.get("confidence", "")
    conf_he = {"low": "נמוכה", "medium": "בינונית", "high": "גבוהה"}.get(conf, conf)
    lines = [f"{'רמת ביטחון' if he else 'Confidence'}: {conf_he if he else conf}."]
    if spec.get("what_would_change_my_view"):
        lines.append(("מה ישנה את התמונה: " if he else "What would change the view: ") + f.text(spec["what_would_change_my_view"]))
    lines.append("זה לא ייעוץ השקעות." if he else "Not investment advice.")
    S.append(("ביטחון" if he else "Confidence", lines))

    sections = [{"n": i + 1, "title": t, "lines": [x for x in ls if x.strip()]} for i, (t, ls) in enumerate(S)]
    return {"empty": False, "lang": lang, "currency": currency, "title": title, "horizon": spec.get("horizon"), "date": dt.date.today().isoformat(),
            "answer": res["answer"], "branches": [{"name": b["name"], "probability": b["probability"], "median": b["books"][0]["median"]} for b in res["branches"]],
            "people": [{"name": p["name"], "median_amt": p["median_amt"], "bad_amt": p["bad_amt"]} for p in act["people"]],
            "sections": sections, "numbers": sorted(f.used)}


def text_numbers(memo: dict) -> list[str]:
    return [n for s in memo["sections"] for line in [s["title"], *s["lines"]] for n in NUM.findall(line)]


def summary_numbers(memo: dict) -> set[str]:
    return set(np.array(memo["numbers"]).tolist())
