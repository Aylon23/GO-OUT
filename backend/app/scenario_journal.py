"""Scenario prediction journal: every imported scenario's prediction is stored, and scored once its
horizon has passed, using only prices up to the end of the horizon.

If past outcomes fall outside the 5-95% band too often, future bands are widened automatically
(Settings.band_scale), and the app says so.
"""
from __future__ import annotations

import datetime as dt
import json

import numpy as np
from sqlmodel import Session, select

from . import factors, impact, services
from .models import ScenarioPrediction
from .specs import factor_move
from .stresstests import local_prices, window_move

HORIZON_DAYS = {"1w": 7, "2w": 14, "1m": 30, "3m": 91, "6m": 182, "12m": 365}
TARGET_COVERAGE = 0.90  # the 5-95% band should hold about 9 in 10 outcomes
POOR_COVERAGE = 0.75
MIN_SCORED = 5
WIDEN = 1.25
MAX_SCALE = 2.0


def record(session: Session, spec: dict, start: dt.date | None = None, n: int = 4000) -> ScenarioPrediction | None:
    """Stores what the engine predicts now for an imported scenario (replacing an unscored earlier record)."""
    start = start or dt.date.today()
    res = impact.run(session, spec, n=n, analogs=False)
    if res.get("empty"):
        return None
    probs = [b["probability"] for b in spec["branches"]]
    tot = sum(probs) or 1.0
    mids = {}
    for f in factors.BASE_IDS:
        vals = [(p / tot, b["factor_moves"][f]["mid"] / 100) for p, b in zip(probs, spec["branches"]) if f in (b.get("factor_moves") or {})]
        if vals:
            mids[f] = sum(p * v for p, v in vals)  # a branch that doesn't name the factor counts it as flat
    fam = res["blended"][0]
    from .scenarios import books

    _, family = books(session, None)
    predicted = {"factors": mids, "family": {k: fam[k] for k in ("median", "p5", "p95")},
                 "branches": [{"name": b["name"], "probability": b["probability"], "median": b["books"][0]["median"]} for b in res["branches"]],
                 "snapshot": [{"symbol": h.symbol, "value": h.value} for h in family.holdings],
                 "cash": family.value - sum(h.value for h in family.holdings), "band_scale": services.get_settings(session).band_scale}
    old = session.exec(select(ScenarioPrediction).where(ScenarioPrediction.spec_id == spec["id"], ScenarioPrediction.scored_at == None)).all()  # noqa: E711
    for o in old:
        session.delete(o)
    row = ScenarioPrediction(spec_id=spec["id"], title=spec.get("title", ""), start=start,
                             end=start + dt.timedelta(days=HORIZON_DAYS.get(spec.get("horizon", "1m"), 30)),
                             horizon=spec.get("horizon", "1m"), predicted=json.dumps(predicted))
    session.add(row)
    session.commit()
    return row


def score(session: Session, row: ScenarioPrediction, today: dt.date | None = None) -> bool:
    """Scores one prediction if its horizon has ended. Uses prices up to the end date only."""
    today = today or dt.date.today()
    if row.end > today:
        return False
    pred = json.loads(row.predicted)
    actual_f = {f: (m / 100 if (m := factor_move(session, f, row.start, row.end)) is not None else None) for f in pred["factors"]}
    tot = covered = 0.0
    for h in pred["snapshot"]:
        w = window_move(local_prices(session, h["symbol"]), row.start, row.end)
        if w is not None:
            tot += w[0] * h["value"]
            covered += h["value"]
    if covered == 0:
        return False
    fam_actual = tot / covered
    p = pred["family"]
    row.actual = json.dumps({"factors": actual_f, "family": fam_actual, "covered": covered / sum(h["value"] for h in pred["snapshot"])})
    row.abs_error = abs(p["median"] - fam_actual)
    row.inside = bool(p["p5"] <= fam_actual <= p["p95"])
    row.scored_at = dt.datetime.now(dt.timezone.utc)
    session.add(row)
    return True


def score_due(session: Session, today: dt.date | None = None) -> int:
    n = 0
    for row in session.exec(select(ScenarioPrediction).where(ScenarioPrediction.scored_at == None)).all():  # noqa: E711
        n += score(session, row, today)
    session.commit()
    if n:
        calibrate(session)
    return n


def calibrate(session: Session) -> None:
    """Widen future bands when coverage of the 5-95% band is poor."""
    rows = session.exec(select(ScenarioPrediction).where(ScenarioPrediction.scored_at != None)).all()  # noqa: E711
    if len(rows) < MIN_SCORED:
        return
    cov = float(np.mean([bool(r.inside) for r in rows]))
    st = services.get_settings(session)
    if cov < POOR_COVERAGE and st.band_scale < MAX_SCALE:
        st.band_scale = min(MAX_SCALE, st.band_scale * WIDEN)
        st.band_note = (f"only {cov:.0%} of {len(rows)} past outcomes fell inside the 5–95% band (target {TARGET_COVERAGE:.0%}), "
                        f"so ranges are now {st.band_scale:.2f}× wider.")
        session.add(st)
        session.commit()


def panel(session: Session) -> dict:
    score_due(session)
    rows = session.exec(select(ScenarioPrediction).order_by(ScenarioPrediction.start.desc())).all()
    scored = [r for r in rows if r.scored_at]
    st = services.get_settings(session)
    out_rows = []
    for r in rows:
        pred = json.loads(r.predicted)
        act = json.loads(r.actual) if r.actual else {}
        out_rows.append({"id": r.id, "spec_id": r.spec_id, "title": r.title, "start": r.start.isoformat(), "end": r.end.isoformat(),
                         "predicted": pred["family"], "actual": act.get("family"), "abs_error": r.abs_error, "inside": r.inside,
                         "factors": [{"factor": f, "predicted": v, "actual": (act.get("factors") or {}).get(f)} for f, v in pred["factors"].items()],
                         "scored": r.scored_at is not None})
    summary = None
    if scored:
        summary = {"n": len(scored), "mae": float(np.mean([r.abs_error for r in scored])),
                   "coverage": float(np.mean([bool(r.inside) for r in scored])),
                   "worst": [x for x in sorted(out_rows, key=lambda x: -(x["abs_error"] or 0)) if x["scored"]][:3]}
    return {"rows": out_rows, "summary": summary, "pending": len(rows) - len(scored), "band_scale": st.band_scale, "band_note": st.band_note,
            "target": TARGET_COVERAGE}
