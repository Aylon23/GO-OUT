"""Multi-model scenario debate: a question in, one validated Scenario Spec out, run through the engine.

1. Round 1 (blind): each analyst model gets the context pack and the spec schema and writes its own spec.
2. Round 2 (challenge): each sees the others' specs (anonymized as Model A, B, ...), critiques and revises.
   One model is the devil's advocate (rotates every debate) and argues the strongest opposite case.
3. Judge: one merged spec, the points of agreement, and a disputes list.
4. Engine (not an LLM): sanitize the spec; widen any range the models disagreed on (the spread of their
   expected moves); state any minority direction the judge left out; validate (schema, probabilities,
   analogs against our prices); if the judge's spec still fails, fall back to a merge that keeps each
   model's view as its own branch. Then save it as a researched spec and run it through the factor model.

Model specs and the judge's are stored and scored when the horizon ends (direction, error, range coverage).
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import random
import threading
import uuid

from sqlmodel import Session, select

from . import ai_jobs, context_pack, openrouter, specs
from .factors import BASE_IDS, FACTORS, SECTOR_ETFS
from .models import AiCall, AiJob, AiJobStep, Debate, DebateSpec

HORIZON_DAYS = {"1w": 5, "2w": 10, "1m": 21, "3m": 63, "6m": 126, "12m": 252}
HORIZON_WORDS = {"1w": "the next week", "2w": "the next two weeks", "1m": "the next month", "3m": "the next three months",
                 "6m": "the next six months", "12m": "the next year"}
FACTOR_LABEL = {f["id"]: f["label"] for f in FACTORS}
MIN_DIRECTION = 0.5  # percent: smaller expected moves have no direction
LETTERS = "ABCDEFGH"
WEIGHT_AFTER = 10  # scored debates before the judge sees each model's track record


# ------------------------------------------------------------------ schemas

def _relax(node):
    """The spec schema without the strict parts free models trip on (extra keys, factor-name lists);
    the engine cleans the answer afterwards and validates it against the full schema."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "additionalProperties" and v is False:
                continue
            if k == "propertyNames":
                continue
            out[k] = _relax(v)
        return out
    if isinstance(node, list):
        return [_relax(x) for x in node]
    return node


def model_spec_schema() -> dict:
    full = specs.schema()
    p = full["properties"]
    return _relax({"type": "object", "required": ["title", "summary", "branches", "confidence"],
                   "properties": {k: p[k] for k in ("title", "summary", "branches", "confidence", "what_would_change_my_view")},
                   "$defs": full["$defs"]})


def round2_schema() -> dict:
    s = model_spec_schema()
    return {"type": "object", "required": ["critique", "spec"], "$defs": s.pop("$defs"),
            "properties": {"critique": {"type": "string"}, "devils_case": {"type": "string"}, "spec": s}}


def judge_schema() -> dict:
    s = model_spec_schema()
    return {"type": "object", "required": ["spec", "agreements", "disputes"], "$defs": s.pop("$defs"),
            "properties": {
                "spec": s,
                "agreements": {"type": "array", "items": {"type": "string"}},
                "disputes": {"type": "array", "items": {"type": "object", "required": ["topic", "views"],
                                                        "properties": {"topic": {"type": "string"}, "views": {"type": "string"},
                                                                       "why": {"type": "string"}}}},
                "minority_views": {"type": "array", "items": {"type": "string"}},
                "devils_case_assessment": {"type": "string"}}}


# ------------------------------------------------------------------ cleaning and merging

def _num(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def _date(x) -> str | None:
    try:
        return dt.date.fromisoformat(str(x)[:10]).isoformat()
    except ValueError:
        return None


def _range(r) -> dict | None:
    if not isinstance(r, dict):
        v = _num(r)
        return {"low": v, "mid": v, "high": v} if v is not None else None
    vals = [_num(r.get(k)) for k in ("low", "mid", "high")]
    if vals[1] is None:
        return None
    lo, mid, hi = (v if v is not None else vals[1] for v in vals)
    lo, mid, hi = sorted([lo, mid, hi])
    clamp = lambda v: round(max(-95.0, min(500.0, v)), 2)
    out = {"low": clamp(lo), "mid": clamp(mid), "high": clamp(hi)}
    if isinstance(r.get("rationale"), str) and r["rationale"].strip():
        out["rationale"] = r["rationale"].strip()[:400]
    return out


def sanitize(raw: dict, question: str, horizon: str, cause: str) -> dict:
    """Any model's spec-like answer -> a complete spec in the app's format (may still fail validation)."""
    raw = raw if isinstance(raw, dict) else {}
    causes = {"supply", "demand", "policy", "geopolitical", "earnings", "other"}
    branches = []
    for i, b in enumerate(raw.get("branches") or []):
        if not isinstance(b, dict):
            continue
        fm = {}
        for k, v in (b.get("factor_moves") or {}).items():
            k = str(k).strip().lower()
            if k in BASE_IDS and (r := _range(v)):
                fm[k] = r
        if not fm:
            continue
        so = {str(k).upper(): r for k, v in (b.get("sector_overrides") or {}).items()
              if str(k).upper() in SECTOR_ETFS and (r := _range(v))}
        tr = [{"from": str(t.get("from") or "trigger")[:40], "to": t["to"], "direction": t["direction"],
               **({"explanation": str(t["explanation"])[:300]} if t.get("explanation") else {})}
              for t in (b.get("transmission") or []) if isinstance(t, dict) and t.get("to") in BASE_IDS and t.get("direction") in ("+", "-")]
        an = []
        for a in b.get("analogs") or []:
            if isinstance(a, dict) and (s := _date(a.get("start"))) and (e := _date(a.get("end"))) and s <= e:
                an.append({"name": str(a.get("name") or "Analog")[:120], "start": s, "end": e,
                           **({"why_similar": str(a["why_similar"])[:300]} if a.get("why_similar") else {})})
        sp = []
        for g in b.get("signposts") or []:
            if isinstance(g, dict) and g.get("condition") in (">", "<") and _num(g.get("value")) is not None:
                sp.append({"indicator": str(g.get("indicator") or g.get("symbol") or "Indicator")[:80], "condition": g["condition"],
                           "value": _num(g["value"]), "meaning": str(g.get("meaning") or "")[:300],
                           **({"symbol": str(g["symbol"])[:20]} if g.get("symbol") else {})})
        out = {"name": str(b.get("name") or f"Branch {i + 1}")[:120], "probability": max(0.0, min(1.0, _num(b.get("probability")) or 0.0)),
               "cause": b.get("cause") if b.get("cause") in causes else (cause if cause in causes else "other"),
               "narrative": str(b.get("narrative") or "")[:1500], "factor_moves": fm}
        if tr:
            out["transmission"] = tr
        if so:
            out["sector_overrides"] = so
        if an:
            out["analogs"] = an[:4]
        if sp:
            out["signposts"] = sp[:5]
        branches.append(out)
    branches = sorted(branches, key=lambda b: -b["probability"])[:6]
    names = set()
    for b in branches:  # branch names must be unique for edits and display
        while b["name"] in names:
            b["name"] += " (2)"
        names.add(b["name"])
    total = sum(b["probability"] for b in branches)
    for b in branches:
        b["probability"] = round(b["probability"] / total, 4) if total > 0 else round(1 / len(branches), 4)
    if branches:  # exact sum after rounding
        branches[0]["probability"] = round(branches[0]["probability"] + 1 - sum(b["probability"] for b in branches), 4)
    conf = raw.get("confidence") if raw.get("confidence") in ("low", "medium", "high") else "low"
    return {
        "spec_version": 1, "kind": "scenario", "id": str(uuid.uuid4()),
        "title": (str(raw.get("title") or question).strip() or "AI debate")[:140],
        "created_at": dt.date.today().isoformat(), "author": "ai-debate", "question": question,
        "horizon": horizon if horizon in HORIZON_DAYS else "1m",
        "summary": (str(raw.get("summary") or "").strip() or "Merged view of the AI debate.")[:2000],
        "branches": branches, "confidence": conf, "sources": [],
        **({"what_would_change_my_view": str(raw["what_would_change_my_view"])[:600]} if raw.get("what_would_change_my_view") else {}),
    }


def expected_moves(spec: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for b in spec.get("branches") or []:
        for f, r in (b.get("factor_moves") or {}).items():
            out[f] = out.get(f, 0.0) + b["probability"] * r["mid"]
    return out


def ranges(spec: dict) -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    for b in spec.get("branches") or []:
        for f, r in (b.get("factor_moves") or {}).items():
            lo, hi = out.get(f, (r["low"], r["high"]))
            out[f] = (min(lo, r["low"]), max(hi, r["high"]))
    return out


def enforce_spread(spec: dict, model_specs: dict[str, dict]) -> tuple[dict, list[dict]]:
    """Agreement keeps the judge's ranges; disagreement widens them: every branch range of a factor is
    stretched to at least the spread of the models' expected moves for it, around its middle."""
    spec = copy.deepcopy(spec)
    exp = [expected_moves(s) for s in model_specs.values()]
    widened = []
    if len(exp) < 2:
        return spec, widened
    for f in BASE_IDS:
        vals = [e.get(f, 0.0) for e in exp]
        if all(abs(v) < 1e-9 for v in vals):
            continue
        spread = max(vals) - min(vals)
        for b in spec["branches"]:
            r = b["factor_moves"].get(f)
            if r is None:
                continue
            lo, hi = min(r["low"], r["mid"] - spread / 2), max(r["high"], r["mid"] + spread / 2)
            if lo < r["low"] - 1e-9 or hi > r["high"] + 1e-9:
                widened.append({"branch": b["name"], "factor": f, "before": [r["low"], r["high"]],
                                "after": [round(lo, 2), round(hi, 2)], "spread": round(spread, 2)})
                r["low"], r["high"] = round(max(-95.0, lo), 2), round(min(500.0, hi), 2)
    return spec, widened


def minority_disputes(spec: dict, model_specs: dict[str, dict], disputes: list[dict]) -> list[dict]:
    """A model expecting the opposite direction on a factor is always stated, even if the judge didn't."""
    merged = expected_moves(spec)
    text = json.dumps(disputes, ensure_ascii=False).lower()
    added = []
    for f in BASE_IDS:
        m = merged.get(f, 0.0)
        if abs(m) < MIN_DIRECTION:
            continue
        against = [(lbl, e[f]) for lbl, s in model_specs.items() if abs((e := expected_moves(s)).get(f, 0.0)) >= MIN_DIRECTION
                   and (e[f] > 0) != (m > 0)]
        if not against:
            continue
        if f in text or FACTOR_LABEL[f].lower() in text:
            continue
        views = "; ".join(f"Model {lbl} expects {v:+.1f}%" for lbl, v in against)
        added.append({"topic": f"Direction of {FACTOR_LABEL[f].lower()}", "views": f"{views}; the merged view {m:+.1f}%.",
                      "why": "Not addressed by the judge. Added by the app so the minority view is stated.", "added_by": "app"})
    return added


def fallback_merge(model_specs: dict[str, dict], question: str, horizon: str, cause: str) -> dict:
    """Always-valid merge: each model's view as its own equally likely branch (no view averaged away)."""
    branches = []
    for lbl, s in model_specs.items():
        e, rg = expected_moves(s), ranges(s)
        fm = {f: {"low": round(min(rg[f][0], v), 2), "mid": round(v, 2), "high": round(max(rg[f][1], v), 2)} for f, v in e.items()}
        if not fm:
            continue
        an = [a for b in s["branches"] for a in b.get("analogs") or []][:3]
        br = {"name": f"Model {lbl} view", "probability": 1.0, "cause": cause if cause in ("supply", "demand", "policy", "geopolitical") else "other",
              "narrative": s.get("summary", "")[:1500], "factor_moves": fm}
        if an:
            br["analogs"] = an
        branches.append(br)
    out = sanitize({"title": question, "summary": "The judge's merge didn't validate, so each model's view is kept as its own branch.",
                    "branches": branches, "confidence": "low"}, question, horizon, cause)
    return out


# ------------------------------------------------------------------ prompts

RULES = (
    "You are one of several independent macro analysts forecasting how markets move in a scenario. "
    "Use only the data in the context pack: today's market snapshot, the analog library (real moves measured from price data) "
    "and the family's holdings (tickers, weights, factor betas). Do not rely on memory for prices or levels. "
    "Factor moves are percent changes over the horizon, each in the instrument's own currency (stock performance only; "
    "there are no currency factors). Factor ids: market (S&P 500 via SPY), size (IWM minus SPY), value (IWD minus IWF), "
    "momentum (MTUM minus SPY), quality (QUAL minus SPY), rates (long US government bonds, TLT price: negative when yields rise), "
    "oil (USO), gold (GLD). Sector overrides use sector ETF tickers (XLE, XLK, ...). Branch probabilities sum to 1. "
    "Analogs must be windows from the analog library, with its exact names and dates. Signposts use Yahoo symbols "
    "(e.g. BZ=F, ^VIX) and levels a reader can check. Give honest, wide ranges where you are unsure.")


def _pack_message(pack: dict) -> dict:
    return {"role": "user", "content": "CONTEXT PACK (identical for every participant):\n" + json.dumps(pack, ensure_ascii=False, separators=(",", ":"))}


def _question_message(d: Debate) -> str:
    inp = json.loads(d.inputs or "{}")
    lines = [f"QUESTION: {d.text}", f"HORIZON: {d.horizon} ({HORIZON_WORDS.get(d.horizon, d.horizon)})", f"CAUSE: {d.cause}"]
    for c in inp.get("dollar_moves") or []:
        if c.get("pct") is not None:
            lines.append(f"STATED MOVE: {c['text']}. Use {c['pct']:+.1f}% for {c['factor']} as the central case.")
    return "\n".join(lines)


def _brief(spec: dict) -> dict:
    """A spec without the noise, for showing to the other models."""
    return {"summary": spec.get("summary"), "confidence": spec.get("confidence"),
            "branches": [{k: b[k] for k in ("name", "probability", "cause", "narrative", "factor_moves", "sector_overrides",
                                             "transmission", "analogs") if k in b} for b in spec.get("branches", [])]}


# ------------------------------------------------------------------ running a debate

def _labels(session: Session, seed: int) -> dict[str, dict]:
    roster = [r for r in openrouter.roster(session) if r["role"] != "judge"]
    rnd = random.Random(seed)
    order = list(range(len(roster)))
    rnd.shuffle(order)
    return {LETTERS[i]: {"role": roster[j]["role"], "models": roster[j]["models"]} for i, j in enumerate(order)}


def _judge_models(session: Session) -> list[str]:
    j = next((r for r in openrouter.roster(session) if r["role"] == "judge"), None)
    return (j or openrouter.roster(session)[0])["models"]


def start(session: Session, text: str, horizon: str, cause: str, inputs: dict, background: bool = True) -> Debate:
    """Create the debate and return at once; the data is prepared (and round 1 queued) in the background."""
    n_before = len(session.exec(select(Debate.id)).all())
    d = Debate(text=text, horizon=horizon, cause=cause, inputs=json.dumps(inputs, ensure_ascii=False), phase="preparing")
    session.add(d)
    session.commit()
    session.refresh(d)
    labels = _labels(session, d.id)
    analysts = sorted(labels, key=lambda lbl: labels[lbl]["role"])
    d.labels = json.dumps(labels)
    d.devil = analysts[n_before % len(analysts)]  # rotates by role every debate
    session.add(d)
    session.commit()
    if background:
        did = d.id

        def prep():
            from .db import get_engine

            with Session(get_engine()) as s:
                _prepare(s, s.get(Debate, did))

        threading.Thread(target=prep, name=f"debate-{did}", daemon=True).start()
    else:
        _prepare(session, d)
    return d


def _prepare(session: Session, d: Debate) -> None:
    """Build the context pack (the first time this fetches a lot of price history) and queue round 1."""
    try:
        d.pack = json.dumps(context_pack.build(session, []), ensure_ascii=False)
    except Exception as exc:
        d.phase, d.note = "failed", f"Couldn't prepare the data: {exc}"[:500]
        session.add(d)
        session.commit()
        return
    session.add(d)
    session.commit()
    labels = json.loads(d.labels)
    msgs = [_pack_message(json.loads(d.pack)),
            {"role": "user", "content": _question_message(d) + "\n\nWrite your own Scenario Spec for this question. "
             "Work independently: you will not see other analysts until the next round."}]
    job = ai_jobs.create(session, f"debate:{d.id}:round1", f"Debate: {d.text[:60]} (round 1)", [
        {"key": lbl, "role": labels[lbl]["role"], "models": labels[lbl]["models"],
         "messages": [{"role": "system", "content": RULES}, *msgs], "schema": model_spec_schema(), "schema_name": "scenario_spec"}
        for lbl in sorted(labels)])
    _set_job(session, d, "round1", job.id)


def _set_job(session: Session, d: Debate, phase: str, job_id: int) -> None:
    jobs = json.loads(d.jobs or "{}")
    jobs[phase] = job_id
    d.jobs, d.phase = json.dumps(jobs), phase
    session.add(d)
    session.commit()


def _specs_of(session: Session, d: Debate, phase: str) -> dict[str, dict]:
    jid = json.loads(d.jobs or "{}").get(phase)
    if not jid:
        return {}
    out = {}
    for lbl, ans in ai_jobs.results(session, jid).items():
        if not ans:
            continue
        raw = ans.get("spec") if phase == "round2" else ans
        s = sanitize(raw, d.text, d.horizon, d.cause)
        if s["branches"]:
            out[lbl] = s
    return out


def _on_done(session: Session, job: AiJob) -> None:
    _, did, phase = job.kind.split(":")
    d = session.get(Debate, int(did))
    if d is None or d.phase != phase:
        return
    if phase == "round1":
        r1 = _specs_of(session, d, "round1")
        if len(r1) < 2:
            d.phase, d.note = "failed", f"Only {len(r1)} model(s) wrote a usable spec in round 1; a debate needs two."
            session.add(d)
            session.commit()
            return
        _round2(session, d, r1)
    elif phase == "round2":
        _judge(session, d)
    elif phase == "judge":
        finalize(session, d)


def _round2(session: Session, d: Debate, r1: dict[str, dict]) -> None:
    labels = json.loads(d.labels)
    steps = []
    for lbl, own in r1.items():
        others = {f"Model {o}": _brief(s) for o, s in r1.items() if o != lbl}
        ask = (f"{_question_message(d)}\n\nYou are Model {lbl}. Your round-1 spec:\n{json.dumps(_brief(own), ensure_ascii=False)}\n\n"
               f"The other analysts' round-1 specs:\n{json.dumps(others, ensure_ascii=False)}\n\n"
               "Critique the other specs (and your own) against the context pack: wrong directions, ranges too tight or too wide, "
               "analogs that don't fit, missing branches. Then return your revised spec.")
        if lbl == d.devil:
            ask += ("\n\nYou are the devil's advocate this round: in devils_case, argue the strongest case against the majority view "
                    "(direction, size or likelihood), and make your revised spec reflect that case.")
        steps.append({"key": lbl, "role": labels[lbl]["role"], "models": labels[lbl]["models"],
                      "messages": [{"role": "system", "content": RULES}, _pack_message(json.loads(d.pack)), {"role": "user", "content": ask}],
                      "schema": round2_schema(), "schema_name": "critique_and_revised_spec"})
    job = ai_jobs.create(session, f"debate:{d.id}:round2", f"Debate: {d.text[:60]} (round 2)", steps)
    _set_job(session, d, "round2", job.id)


def _round2_answers(session: Session, d: Debate) -> dict[str, dict]:
    jid = json.loads(d.jobs or "{}").get("round2")
    return {k: v for k, v in (ai_jobs.results(session, jid) if jid else {}).items() if v}


def _judge(session: Session, d: Debate) -> None:
    r1 = _specs_of(session, d, "round1")
    r2 = _specs_of(session, d, "round2")
    answers = _round2_answers(session, d)
    final = {lbl: r2.get(lbl) or r1[lbl] for lbl in r1}
    record = track_record_by_label(session, d)
    views = {f"Model {lbl}": {"revised_spec": _brief(s), "critique": (answers.get(lbl) or {}).get("critique", "(no round-2 answer)"),
                              **({"devils_case": answers[lbl].get("devils_case", "")} if lbl == d.devil and lbl in answers else {})}
             for lbl, s in final.items()}
    ask = (f"{_question_message(d)}\n\nThe analysts' revised specs and critiques (Model {d.devil} was the devil's advocate):\n"
           f"{json.dumps(views, ensure_ascii=False)}\n\n"
           + (f"Past accuracy of each analyst on scored scenarios (weigh them by it, but say where you did):\n{json.dumps(record)}\n\n" if record else "")
           + "Merge them into one spec. Where they agree, keep ranges tight. Where they disagree, widen the ranges or keep separate "
             "branches. Never average a minority view away without stating it in minority_views or disputes. List the points of "
             "agreement, and every dispute: the topic, each side's view, and why they disagree. Assess the devil's advocate case.")
    job = ai_jobs.create(session, f"debate:{d.id}:judge", f"Debate: {d.text[:60]} (judge)", [
        {"key": "judge", "role": "judge", "models": _judge_models(session),
         "messages": [{"role": "system", "content": RULES.replace("one of several independent macro analysts", "the judge of a panel of macro analysts")},
                      _pack_message(json.loads(d.pack)), {"role": "user", "content": ask}],
         "schema": judge_schema(), "schema_name": "merged_spec_and_disputes"}])
    _set_job(session, d, "judge", job.id)


def finalize(session: Session, d: Debate, run_engine: bool = True) -> Debate:
    """Engine part: merge checks, validation, save, impact, top hurt/helped, scoring rows."""
    r1 = _specs_of(session, d, "round1")
    r2 = _specs_of(session, d, "round2")
    final_views = {lbl: r2.get(lbl) or r1[lbl] for lbl in r1}
    jid = json.loads(d.jobs or "{}").get("judge")
    j = (ai_jobs.results(session, jid).get("judge") if jid else None) or {}
    notes = []
    spec = sanitize(j.get("spec") or {}, d.text, d.horizon, d.cause) if j else None
    disputes = [x for x in (j.get("disputes") or []) if isinstance(x, dict)]
    if not spec or not spec["branches"]:
        notes.append("The judge gave no usable spec; each model's view is kept as its own branch.")
        spec = fallback_merge(final_views, d.text, d.horizon, d.cause)
    spec, widened = enforce_spread(spec, final_views)
    disputes += minority_disputes(spec, final_views, disputes)
    report = specs.validate(session, spec)
    if report["errors"]:
        notes.append("The judge's spec failed validation (" + "; ".join(report["errors"][:3]) + "); each model's view is kept as its own branch.")
        spec = fallback_merge(final_views, d.text, d.horizon, d.cause)
        spec, widened = enforce_spread(spec, final_views)
        report = specs.validate(session, spec)
    if report["errors"]:
        d.phase, d.note = "failed", "No valid spec: " + "; ".join(report["errors"][:3])
        session.add(d)
        session.commit()
        return d
    specs.save(session, spec, report, "ai-debate")
    d.spec_id = spec["id"]
    labels = json.loads(d.labels)
    for lbl, s in r1.items():
        session.add(DebateSpec(debate_id=d.id, kind="round1", label=lbl, model=labels[lbl]["models"][0], spec=json.dumps(s), horizon=d.horizon))
    session.add(DebateSpec(debate_id=d.id, kind="judge", label="judge", model=_judge_models(session)[0], spec=json.dumps(spec), horizon=d.horizon))
    result = {"agreements": [str(x) for x in j.get("agreements") or []], "disputes": disputes,
              "minority_views": [str(x) for x in j.get("minority_views") or []],
              "devils_case": (_round2_answers(session, d).get(d.devil) or {}).get("devils_case", ""),
              "devils_case_assessment": j.get("devils_case_assessment", ""),
              "widened": widened, "validation": report, "notes": notes}
    if run_engine:
        result.update(engine_view(session, spec, {f"Model {k}": v for k, v in final_views.items()},
                                  {f"Model {k}": v for k, v in r1.items()}))
    d.result = json.dumps(result, ensure_ascii=False, default=str)
    d.phase = "done"
    session.add(d)
    session.commit()
    return d


def top_movers(impact_out: dict, k: int = 5) -> tuple[list[dict], list[dict]]:
    """Most hurt and most helped family holdings by amount, with the factor that drives each."""
    rows = impact_out["holdings"]["family"]

    def item(r):
        top = max(r["why"], key=lambda w: abs(w["effect"])) if r["why"] else None
        return {"symbol": r["symbol"], "name": r["name"], "move": r["move"], "change_amt": r["change_amt"], "weight": r["weight"],
                "driver": top["factor"] if top else None, "driver_effect": top["effect"] if top else None, "why": r["why"]}

    hurt = [item(r) for r in sorted(rows, key=lambda r: r["change_amt"]) if r["change_amt"] < 0][:k]
    helped = [item(r) for r in sorted(rows, key=lambda r: -r["change_amt"]) if r["change_amt"] > 0][:k]
    return hurt, helped


def engine_view(session: Session, spec: dict, final_views: dict[str, dict], r1: dict[str, dict]) -> dict:
    from . import impact

    out = impact.run(session, spec, analogs=False)
    if out.get("empty"):
        return {"empty": True}
    hurt, helped = top_movers(out)
    ports = [b for b in out["blended"] if b["id"] is not None]
    worst = min(ports, key=lambda b: b["median"]) if ports else None
    fam = out["blended"][0]
    headline = []
    for stage, group in (("round 1", r1), ("round 2", final_views)):
        for name, s in group.items():
            o = impact.run(session, s, n=1500, analogs=False)
            headline.append({"model": name, "stage": stage, "median": o["blended"][0]["median"], "p5": o["blended"][0]["p5"],
                             "median_amt": o["blended"][0]["median_amt"],
                             "moves": {f: round(v, 2) for f, v in expected_moves(s).items()}})
    return {"answer": answer_sentence(spec, fam, worst, out.get("base_currency", "USD")), "currency": out.get("base_currency", "USD"), "family": {k: fam[k] for k in ("median", "p5", "p95", "median_amt", "p5_amt", "p95_amt", "prob_loss")},
            "most_exposed": {"id": worst["id"], "name": worst["name"], "median": worst["median"], "median_amt": worst["median_amt"]} if worst else None,
            "hurt": hurt, "helped": helped, "headline": headline,
            "branches": [{"name": b["name"], "probability": b["probability"], "family": b["books"][0]} for b in out["branches"]]}


def answer_sentence(spec: dict, fam: dict, worst: dict | None, currency: str = "USD") -> str:
    sym = "₪" if currency == "ILS" else "$"

    def amt(v):
        return f"{sym}{abs(v):,.0f}"

    verb = "loses" if fam["median"] < 0 else "gains"
    s = (f"Most likely the family {verb} about {amt(fam['median_amt'])} ({fam['median'] * 100:+.1f}%) over "
         f"{HORIZON_WORDS.get(spec['horizon'], spec['horizon'])}; in a bad case (1 in 20) {amt(fam['p5_amt'])} "
         f"({fam['p5'] * 100:+.1f}%).")
    if worst:
        s += (f" {worst['name']} is the most exposed ({worst['median'] * 100:+.1f}%)." if worst["median"] < 0
              else f" Every portfolio gains; {worst['name']} the least ({worst['median'] * 100:+.1f}%).")
    return s


# ------------------------------------------------------------------ status, transcript

def requests_used(session: Session, d: Debate) -> int:
    ids = list(json.loads(d.jobs or "{}").values())
    return sum(c.requests for c in session.exec(select(AiCall).where(AiCall.job_id.in_(ids))).all()) if ids else 0


def transcript(session: Session, d: Debate) -> list[dict]:
    labels = json.loads(d.labels or "{}")
    out = []
    for phase, jid in json.loads(d.jobs or "{}").items():
        for st in session.exec(select(AiJobStep).where(AiJobStep.job_id == jid).order_by(AiJobStep.idx)).all():
            call = session.get(AiCall, st.call_id) if st.call_id else None
            out.append({"round": phase, "label": st.key, "role": st.role,
                        "models": (labels.get(st.key) or {}).get("models") or json.loads(st.request).get("models"),
                        "model_used": call.model_used if call else "", "requests": call.requests if call else 0,
                        "status": st.status, "error": st.error, "answer": json.loads(st.result) if st.result else None})
    return out


def payload(session: Session, d: Debate, full: bool = True) -> dict:
    jobs = json.loads(d.jobs or "{}")
    steps = session.exec(select(AiJobStep).where(AiJobStep.job_id.in_(list(jobs.values())))).all() if jobs else []
    out = {"id": d.id, "text": d.text, "horizon": d.horizon, "cause": d.cause, "phase": d.phase, "note": d.note,
           "created_at": d.created_at.isoformat(), "devil": d.devil, "spec_id": d.spec_id,
           "progress": {"done": sum(s.status in ("done", "failed") for s in steps), "total": len(steps)},
           "requests": requests_used(session, d), "inputs": json.loads(d.inputs or "{}")}
    waiting = session.exec(select(AiJob).where(AiJob.id.in_(list(jobs.values())), AiJob.status == "waiting_quota")).first() if jobs else None
    if waiting:
        out["waiting"] = waiting.note
    if full:
        out["result"] = json.loads(d.result or "{}")
        out["labels"] = json.loads(d.labels or "{}")
        out["transcript"] = transcript(session, d)
    return out


def listing(session: Session, limit: int = 20) -> list[dict]:
    return [payload(session, d, full=False) for d in session.exec(select(Debate).order_by(Debate.id.desc()).limit(limit)).all()]


# ------------------------------------------------------------------ scoring and leaderboard

def score_spec(session: Session, spec: dict, start: dt.date, horizon: str) -> dict | None:
    end = start + dt.timedelta(days=round(HORIZON_DAYS.get(horizon, 21) * 7 / 5))
    exp, rg = expected_moves(spec), ranges(spec)
    hits = n = inside = rn = 0
    errs = []
    for f, e in exp.items():
        actual = specs.factor_move(session, f, start, end)
        if actual is None:
            continue
        errs.append(abs(actual - e))
        if abs(e) >= MIN_DIRECTION:
            n += 1
            hits += (actual > 0) == (e > 0)
        lo, hi = rg[f]
        rn += 1
        inside += lo <= actual <= hi
    if not errs:
        return None
    return {"direction_hits": hits, "direction_n": n, "abs_error": sum(errs) / len(errs), "in_range": inside, "range_n": rn}


def score_due(session: Session, today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    done = 0
    for row in session.exec(select(DebateSpec).where(DebateSpec.scored_at.is_(None))).all():
        end = row.start + dt.timedelta(days=round(HORIZON_DAYS.get(row.horizon, 21) * 7 / 5))
        if end > today:
            continue  # forward only: never scored before the horizon has passed
        sc = score_spec(session, json.loads(row.spec), row.start, row.horizon)
        if sc is None:
            continue
        for k, v in sc.items():
            setattr(row, k, v)
        row.scored_at = today
        session.add(row)
        done += 1
    session.commit()
    return done


def leaderboard(session: Session) -> list[dict]:
    groups: dict[str, list[DebateSpec]] = {}
    for r in session.exec(select(DebateSpec).where(DebateSpec.scored_at.is_not(None))).all():
        groups.setdefault(("Judge · " if r.kind == "judge" else "") + r.model, []).append(r)
    out = []
    for name, rows in groups.items():
        n = sum(r.direction_n for r in rows)
        rn = sum(r.range_n for r in rows)
        errs = [r.abs_error for r in rows if r.abs_error is not None]
        out.append({"model": name, "scenarios": len(rows), "direction_hit_rate": sum(r.direction_hits for r in rows) / n if n else None,
                    "avg_error": sum(errs) / len(errs) if errs else None, "in_range_rate": sum(r.in_range for r in rows) / rn if rn else None})
    return sorted(out, key=lambda x: (-(x["direction_hit_rate"] or 0), x["avg_error"] or 1e9))


def track_record_by_label(session: Session, d: Debate) -> dict:
    """Past accuracy per analyst of this debate, once there are enough scored scenarios."""
    scored = session.exec(select(DebateSpec).where(DebateSpec.scored_at.is_not(None), DebateSpec.kind == "round1")).all()
    if len({r.debate_id for r in scored}) < WEIGHT_AFTER:
        return {}
    by_model = {row["model"]: row for row in leaderboard(session)}
    out = {}
    for lbl, info in json.loads(d.labels or "{}").items():
        r = by_model.get(info["models"][0])
        if r:
            out[f"Model {lbl}"] = {"direction_hit_rate": r["direction_hit_rate"], "avg_error": r["avg_error"], "scenarios": r["scenarios"]}
    return out


# ------------------------------------------------------------------ actions

def send_to_queue(session: Session, d: Debate, k: int = 10) -> int:
    from . import action_queue
    from .impact import run as impact_run

    if not d.spec_id:
        return 0
    from .models import ResearchedSpec

    row = session.get(ResearchedSpec, d.spec_id)
    spec = json.loads(row.spec) if row else None
    if not spec:
        return 0
    out = impact_run(session, spec, analogs=False)
    items = []
    for key, rows in out["holdings"].items():
        if key == "family":
            continue
        for r in rows:
            if r["change_amt"] < 0:
                items.append((int(key), r))
    items.sort(key=lambda x: x[1]["change_amt"])
    for pid, r in items[:k]:
        top = max(r["why"], key=lambda w: abs(w["effect"])) if r["why"] else None
        driver = FACTOR_LABEL.get(top["factor"], top["factor"]) if top else "the scenario"
        action_queue.add(session, "debate", d.id, f"Review {r['symbol']}: {r['move'] * 100:+.1f}% in “{spec['title']}”",
                         f"About {'₪' if out.get('base_currency') == 'ILS' else '$'}{abs(r['change_amt']):,.0f} of this portfolio, driven mostly by {driver.lower()}.",
                         "Check the position size against this scenario.", portfolio_id=pid, symbol=r["symbol"], impact_amt=r["change_amt"])
    return min(k, len(items))


ai_jobs.on_done("debate:", _on_done)
