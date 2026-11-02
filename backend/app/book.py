"""The scenario book: my current view of the world.

Each saved scenario gets my probability that it happens within its horizon; whatever is left is
"nothing special" (normal conditions). The book runs as one combined spec through the same engine:
every scenario's branches with probability P(scenario) x P(branch), plus the remainder branch.
All scenarios are measured over the book's horizon (the longest of them).
"""
from __future__ import annotations

import datetime as dt
import json

import numpy as np
from sqlmodel import Session, select

from . import actions, impact
from .market.cache import NoPriceData, load_history
from .models import BookVersion

HORIZON_ORDER = ["1w", "2w", "1m", "3m", "6m", "12m"]
NEAR = 0.05  # within 5% of a signpost's trigger value counts as near
FRAGILE_TAIL = 0.10


class BookError(ValueError):
    pass


def current(session: Session) -> BookVersion | None:
    return session.exec(select(BookVersion).order_by(BookVersion.id.desc())).first()


def entries_of(v: BookVersion | None) -> list[dict]:
    return json.loads(v.entries) if v else []


def save(session: Session, entries: list[dict], note: str = "") -> BookVersion:
    """Saves a new version (every change is kept)."""
    total = sum(float(e["probability"]) for e in entries)
    if any(not 0 <= float(e["probability"]) <= 1 for e in entries):
        raise BookError("Each probability must be between 0% and 100%.")
    if total > 1 + 1e-9:
        raise BookError(f"The probabilities add up to {total:.0%}; together they can't pass 100%.")
    ids = [e["scenario_id"] for e in entries]
    if len(ids) != len(set(ids)):
        raise BookError("A scenario can be in the book only once.")
    known = {s["id"]: s for s in actions.saved_specs(session)}
    for e in entries:
        if e["scenario_id"] not in known:
            raise BookError(f"Scenario {e['scenario_id']} isn't saved any more.")
        e["title"] = known[e["scenario_id"]]["title"]
    v = BookVersion(entries=json.dumps(entries, ensure_ascii=False), note=note)
    session.add(v)
    session.commit()
    session.refresh(v)
    return v


def combined_spec(session: Session, entries: list[dict]) -> tuple[dict, dict[int, str]]:
    """One spec for the whole book, and which scenario each branch came from (index -> scenario id)."""
    specs = {s["id"]: s for s in actions.saved_specs(session)}
    used = [(e, specs[e["scenario_id"]]) for e in entries if e["scenario_id"] in specs and float(e["probability"]) > 0]
    horizons = [s["spec"].get("horizon", "1m") for _, s in used] or ["1m"]
    horizon = max(horizons, key=lambda h: HORIZON_ORDER.index(h) if h in HORIZON_ORDER else 2)
    branches, owner = [], {}
    for e, s in used:
        p = float(e["probability"])
        bs = s["spec"]["branches"]
        tot = sum(b["probability"] for b in bs) or 1.0
        for b in bs:
            owner[len(branches)] = s["id"]
            branches.append({**b, "name": f"{s['title']}: {b['name']}", "probability": p * b["probability"] / tot})
    rest = max(0.0, 1.0 - sum(float(e["probability"]) for e, _ in used))
    if rest > 1e-9:
        normal = actions.normal_spec(session, horizon)["branches"][0]
        owner[len(branches)] = "nothing"
        branches.append({**normal, "name": "Nothing special", "probability": rest})
    spec = {"spec_version": 1, "kind": "scenario", "id": "book", "title": "My book", "horizon": horizon, "branches": branches}
    return spec, owner


def run(session: Session, entries: list[dict], seed: int = impact.SEED, n: int = impact.N_DRAWS) -> dict:
    if not entries:
        return {"empty": True, "reason": "no_scenarios"}
    spec, owner = combined_spec(session, entries)
    res = impact.run(session, spec, seed=seed, n=n, analogs=False, keep=True)
    if res.get("empty"):
        return {"empty": True, "reason": "no_holdings"}
    outcomes, pick = res.pop("_outcomes"), res.pop("_pick")
    branch_owner = np.array([owner[int(i)] for i in pick])
    scen_ids = [e["scenario_id"] for e in entries if float(e["probability"]) > 0] + (["nothing"] if "nothing" in owner.values() else [])
    known = {x["id"]: x["title"] for x in actions.saved_specs(session)}
    titles = {e["scenario_id"]: known.get(e["scenario_id"], e.get("title", e["scenario_id"])) for e in entries}
    titles["nothing"] = "Nothing special"
    positioning = []
    for k, b in enumerate(res["blended"]):
        x = outcomes[:, k]
        cut = np.percentile(x, FRAGILE_TAIL * 100)
        tail = x[x <= cut]
        fragility = float(-tail.mean()) if len(tail) else 0.0
        per = []
        for sid in scen_ids:
            rows = branch_owner == sid
            if rows.any():
                per.append({"scenario_id": sid, "title": titles[sid], "median": float(np.median(x[rows])), "share": float(rows.mean()),
                            "median_amt": float(np.median(x[rows]) * b["value_amt"])})
        hurt = sorted([p for p in per if p["median"] < 0], key=lambda p: p["median"])
        helps = sorted([p for p in per if p["median"] > 0], key=lambda p: -p["median"])
        positioning.append({"id": b["id"], "name": b["name"], "fragility": fragility, "fragility_amt": fragility * b["value_amt"],
                            "by_scenario": per, "hurts_most": hurt[:2], "helps_most": helps[:2]})
    people = [p for p in positioning if p["id"] is not None]
    most_fragile = max(people, key=lambda p: p["fragility"]) if people else None
    total = sum(float(e["probability"]) for e in entries)
    res.update({"entries": entries, "remainder": max(0.0, 1 - total), "positioning": positioning,
                "most_fragile": {"id": most_fragile["id"], "name": most_fragile["name"], "fragility": most_fragile["fragility"],
                                 "fragility_amt": most_fragile["fragility_amt"]} if most_fragile else None,
                "scenario_titles": titles})
    res["spec"] = {k: v for k, v in res["spec"].items() if k != "branches"} | {"branch_count": len(spec["branches"])}
    res.pop("holdings", None)
    return res


# ------------------------------------------------------------------ signposts

def _last_close(session: Session, symbol: str) -> tuple[float, str] | None:
    try:
        df = load_history(session, symbol)
    except NoPriceData:
        return None
    if df is None or df.empty:
        return None
    return float(df["close"].iloc[-1]), df.index[-1].isoformat()


def signposts(session: Session, entries: list[dict]) -> list[dict]:
    """Every signpost from the book's scenarios, checked against the latest cached price."""
    specs = {s["id"]: s for s in actions.saved_specs(session)}
    out = []
    for e in entries:
        s = specs.get(e["scenario_id"])
        if not s:
            continue
        for b in s["spec"].get("branches", []):
            for sp in b.get("signposts") or []:
                row = {"scenario": s["title"], "scenario_id": s["id"], "branch": b["name"], "indicator": sp["indicator"],
                       "symbol": sp.get("symbol"), "condition": sp["condition"], "value": sp["value"], "meaning": sp["meaning"],
                       "current": None, "as_of": None, "distance": None, "status": "not tracked"}
                if sp.get("symbol"):
                    last = _last_close(session, sp["symbol"])
                    if last is None:
                        row["status"] = "no data"
                    else:
                        cur, as_of = last
                        row["current"], row["as_of"] = cur, as_of
                        row["distance"] = (sp["value"] - cur) / cur if cur else None
                        met = cur > sp["value"] if sp["condition"] == ">" else cur < sp["value"]
                        row["status"] = "triggered" if met else ("near" if row["distance"] is not None and abs(row["distance"]) <= NEAR else "far")
                out.append(row)
    order = {"triggered": 0, "near": 1, "far": 2, "no data": 3, "not tracked": 4}
    return sorted(out, key=lambda r: (order[r["status"]], abs(r["distance"] or 9)))


# ------------------------------------------------------------------ versions

def versions(session: Session) -> list[dict]:
    rows = session.exec(select(BookVersion).order_by(BookVersion.id.desc())).all()
    return [{"id": v.id, "created_at": v.created_at.isoformat(), "note": v.note, "entries": json.loads(v.entries)} for v in rows]


def compare(session: Session, a: int, b: int, n: int = 4000) -> dict:
    va, vb = session.get(BookVersion, a), session.get(BookVersion, b)
    if va is None or vb is None:
        raise BookError("Version not found.")
    ea, eb = entries_of(va), entries_of(vb)
    pa = {e["scenario_id"]: e for e in ea}
    pb = {e["scenario_id"]: e for e in eb}
    changes = []
    for sid in dict.fromkeys(list(pa) + list(pb)):
        x, y = pa.get(sid), pb.get(sid)
        if x is None or y is None or abs(float(x["probability"]) - float(y["probability"])) > 1e-9:
            changes.append({"scenario_id": sid, "title": (y or x)["title"], "from": float(x["probability"]) if x else None,
                            "to": float(y["probability"]) if y else None})

    def summary(es):
        r = run(session, es, n=n)
        if r.get("empty"):
            return None
        return {"answer": r["answer"], "blended": [{k: v for k, v in x.items() if k in ("id", "name", "median", "p5", "median_amt", "p5_amt")} for x in r["blended"]],
                "most_fragile": r["most_fragile"]}

    return {"a": {"id": a, "created_at": va.created_at.isoformat(), "result": summary(ea)},
            "b": {"id": b, "created_at": vb.created_at.isoformat(), "result": summary(eb)}, "changes": changes}
