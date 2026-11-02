"""Scenario Specs: researched outside the app, imported by paste or through the inbox folder.

The app never calls a paid API. It extracts the spec, validates it against
docs/scenario-spec.schema.json, checks it against our own price data, and stores it with the
report. It never changes the spec's numbers.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from sqlmodel import Session, select

from .factors import BASE_IDS, FACTORS, SECTOR_ETFS
from .market.cache import NoPriceData, load_history
from .models import ResearchedSpec

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "docs" / "scenario-spec.schema.json"
HORIZON_DAYS = {"1w": 5, "2w": 10, "1m": 21, "3m": 63, "6m": 126, "12m": 252}
PROB_TOLERANCE = 0.01
DIRECTION_MIN = 0.5  # percent: a mid move smaller than this implies no direction
CONTRADICTION_MIN = 0.5  # percent: an actual move smaller than this contradicts nothing
STALE_DAYS = 30
BUILD = {f["id"]: f["build"] for f in FACTORS}


class SpecError(ValueError):
    pass


@lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ extraction

def extract(text: str, kinds: tuple[str, ...] = ("scenario",)) -> dict:
    """The first valid spec in a text: plain JSON, a ```json block, or JSON surrounded by prose."""
    text = text.strip().lstrip("﻿")
    candidates: list[str] = [text]
    candidates += [m.group(1) for m in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.S)]
    # every balanced {...} starting at an opening brace
    for start in [m.start() for m in re.finditer(r"\{", text)]:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                esc = (c == "\\") and not esc
                if c == '"' and not esc:
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:i + 1])
                    break
    seen_json = False
    for c in candidates:
        try:
            obj = json.loads(c)
        except ValueError:
            continue
        seen_json = True
        if isinstance(obj, dict) and obj.get("kind") in kinds:
            return obj
    if seen_json:
        raise SpecError(f"Found JSON, but no object with \"kind\": {' or '.join(repr(k) for k in kinds)}.")
    raise SpecError("No JSON found. Paste the spec itself, or a message that contains it.")


# ------------------------------------------------------------------ validation

def _price(session: Session, symbol: str) -> pd.Series | None:
    try:
        df = load_history(session, symbol)
    except NoPriceData:
        return None
    col = "close" if symbol == "ILS=X" or symbol.startswith("^") else "adj_close"
    return df[col].astype(float)


def _asof(p: pd.Series, d: dt.date) -> tuple[float, dt.date] | None:
    sub = p[p.index <= d]
    return (float(sub.iloc[-1]), sub.index[-1]) if len(sub) else None


def window_move(session: Session, symbol: str, start: dt.date, end: dt.date) -> float | None:
    """Percent move from the last close on or before start to the last close on or before end."""
    p = _price(session, symbol)
    if p is None:
        return None
    a, b = _asof(p, start), _asof(p, end)
    if a is None or b is None or (start - a[1]).days > 6 or b[1] <= a[1] or a[0] <= 0:
        return None
    return (b[0] / a[0] - 1) * 100


def factor_move(session: Session, factor: str, start: dt.date, end: dt.date) -> float | None:
    """A factor's real move over a window, in percent (spreads: the difference of the two moves)."""
    syms = BUILD.get(factor) or ((factor,) if factor in SECTOR_ETFS else None)
    if syms is None:
        return None
    moves = [window_move(session, s, start, end) for s in syms]
    if any(m is None for m in moves):
        return None
    return moves[0] - (moves[1] if len(moves) > 1 else 0.0)


_extremes: dict[tuple, float | None] = {}


def largest_move(session: Session, factor: str, days: int) -> float | None:
    """The largest absolute move of a factor over any `days` trading days in our data, in percent."""
    key = (factor, days, dt.date.today())
    if key in _extremes:
        return _extremes[key]
    syms = BUILD.get(factor) or ((factor,) if factor in SECTOR_ETFS else None)
    series = [_price(session, s) for s in syms] if syms else []
    if not series or any(s is None for s in series):
        _extremes[key] = None
        return None
    df = pd.concat(series, axis=1, join="inner").dropna()
    r = df.iloc[:, 0].pct_change(days)
    if df.shape[1] > 1:
        r = r - df.iloc[:, 1].pct_change(days)
    r = r.dropna()
    _extremes[key] = float(np.abs(r).max() * 100) if len(r) else None
    return _extremes[key]


def validate(session: Session, spec: dict, today: dt.date | None = None) -> dict:
    """{errors, warnings, analogs, ok}. Errors block saving; warnings don't."""
    import jsonschema

    today = today or dt.date.today()
    errors: list[str] = []
    warnings: list[str] = []
    v = jsonschema.Draft202012Validator(schema())
    for e in sorted(v.iter_errors(spec), key=lambda e: list(e.path)):
        where = "/".join(str(p) for p in e.path) or "spec"
        msg = e.message
        if e.validator == "propertyNames" or "is not one of" in msg and ("factor" in str(e.schema_path) or "factor_moves" in where):
            bad = re.findall(r"'([^']+)' is not one of", msg)
            msg = f"unknown factor id {bad[0]!r}; use one of {', '.join(BASE_IDS)}" if bad else msg
        errors.append(f"{where}: {msg}")
    branches = spec.get("branches") if isinstance(spec.get("branches"), list) else []
    # unknown factor ids, reported plainly even when the schema message is cryptic
    for i, b in enumerate(branches):
        for k in (b.get("factor_moves") or {}):
            if k not in BASE_IDS and not any(f"unknown factor id {k!r}" in e for e in errors):
                errors.append(f"branches/{i}/factor_moves: unknown factor id {k!r}; use one of {', '.join(BASE_IDS)}")
        for k in (b.get("sector_overrides") or {}):
            if k not in SECTOR_ETFS and not any(repr(k) in e for e in errors):
                errors.append(f"branches/{i}/sector_overrides: unknown sector ETF {k!r}; use one of {', '.join(SECTOR_ETFS)}")
    probs = [b.get("probability") for b in branches if isinstance(b.get("probability"), (int, float))]
    if branches and len(probs) == len(branches) and abs(sum(probs) - 1) > PROB_TOLERANCE:
        errors.append(f"branches: probabilities add up to {sum(probs):.2f}, not 1.")
    for i, b in enumerate(branches):
        for group in ("factor_moves", "sector_overrides"):
            for k, r in (b.get(group) or {}).items():
                if isinstance(r, dict) and all(isinstance(r.get(x), (int, float)) for x in ("low", "mid", "high")):
                    if r["low"] > r["mid"] or r["mid"] > r["high"]:
                        errors.append(f"branches/{i} ({b.get('name', '?')}): {k} must have low ≤ mid ≤ high (got {r['low']}, {r['mid']}, {r['high']}).")

    # checks against our data (warnings only)
    days = HORIZON_DAYS.get(spec.get("horizon", ""), 21)
    analogs = []
    for i, b in enumerate(branches):
        name = b.get("name", f"branch {i + 1}")
        moves = b.get("factor_moves") or {}
        if len(b.get("analogs") or []) < 2:
            warnings.append(f"{name}: fewer than 2 past analogs, so there is little history to check it against.")
        for k, r in list(moves.items()) + list((b.get("sector_overrides") or {}).items()):
            if k not in BASE_IDS and k not in SECTOR_ETFS or not isinstance(r, dict):
                continue
            big = largest_move(session, k, days)
            vals = [r.get(x) for x in ("low", "mid", "high") if isinstance(r.get(x), (int, float))]
            if big is not None and vals and max(abs(x) for x in vals) > big:
                warnings.append(f"{name}: {k} move of {max(vals, key=abs):+g}% is larger than any {spec.get('horizon')} move in our data (largest {big:.1f}%).")
        for a in b.get("analogs") or []:
            try:
                s, e = dt.date.fromisoformat(a["start"]), dt.date.fromisoformat(a["end"])
            except (KeyError, ValueError, TypeError):
                continue
            rows = []
            for k, r in moves.items():
                if k not in BASE_IDS or not isinstance(r, dict) or not isinstance(r.get("mid"), (int, float)):
                    continue
                actual = factor_move(session, k, s, e)
                claimed = r["mid"]
                implied = 0 if abs(claimed) < DIRECTION_MIN else (1 if claimed > 0 else -1)
                contradicts = actual is not None and implied != 0 and abs(actual) >= CONTRADICTION_MIN and np.sign(actual) != implied
                rows.append({"factor": k, "claimed_mid": claimed, "actual": actual, "contradicts": bool(contradicts)})
                if contradicts:
                    warnings.append(f"{name}: in the analog “{a.get('name')}” ({s} to {e}), {k} actually moved {actual:+.1f}%, "
                                    f"against the branch's {claimed:+g}%.")
            missing = [r["factor"] for r in rows if r["actual"] is None]
            analogs.append({"branch": name, "name": a.get("name"), "start": a.get("start"), "end": a.get("end"),
                            "why_similar": a.get("why_similar", ""), "factors": rows,
                            "no_data": missing})
    for c in spec.get("current_context") or []:
        try:
            age = (today - dt.date.fromisoformat(c["as_of"])).days
        except (KeyError, ValueError, TypeError):
            continue
        if age > STALE_DAYS:
            warnings.append(f"Context “{c.get('item')}” is {age} days old (as of {c['as_of']}).")
    return {"errors": errors, "warnings": warnings, "analogs": analogs, "ok": not errors,
            "checked_at": dt.datetime.now(dt.timezone.utc).isoformat()}


# ------------------------------------------------------------------ storage

def save(session: Session, spec: dict, report: dict, source: str) -> ResearchedSpec:
    if report["errors"]:
        raise SpecError("The spec has errors; fix them before saving.")
    row = session.get(ResearchedSpec, spec["id"]) or ResearchedSpec(id=spec["id"])
    row.title = spec.get("title", "")
    row.spec = json.dumps(spec, ensure_ascii=False)
    row.report = json.dumps(report, ensure_ascii=False)
    row.source = source
    row.imported_at = dt.datetime.now(dt.timezone.utc)
    session.add(row)
    session.commit()
    return row


def row_dict(r: ResearchedSpec, full: bool = True) -> dict:
    spec = json.loads(r.spec)
    rep = json.loads(r.report)
    out = {"id": r.id, "title": r.title, "title_he": spec.get("title_he", ""), "horizon": spec.get("horizon"),
           "branches": len(spec.get("branches", [])), "confidence": spec.get("confidence"), "author": spec.get("author"),
           "created_at": spec.get("created_at"), "source": r.source, "imported_at": r.imported_at.isoformat(),
           "warnings": len(rep.get("warnings", []))}
    if full:
        out["spec"] = spec
        out["report"] = rep
    return out


def listing(session: Session) -> list[dict]:
    rows = session.exec(select(ResearchedSpec).order_by(ResearchedSpec.imported_at.desc())).all()
    return [row_dict(r, full=False) for r in rows]


# ------------------------------------------------------------------ inbox

def inbox_dir(session: Session) -> Path:
    from .services import get_settings

    custom = (get_settings(session).inbox_dir or "").strip()
    d = Path(custom).expanduser() if custom else ROOT / "inbox"
    for sub in ("", "processed", "failed"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


HANDLERS: dict[str, callable] = {}  # kind -> fn(session, obj) -> (ok, message); filled by other modules


def _handle_scenario(session: Session, obj: dict) -> tuple[bool, str]:
    rep = validate(session, obj)
    if rep["errors"]:
        return False, "Errors:\n- " + "\n- ".join(rep["errors"]) + ("\n\nWarnings:\n- " + "\n- ".join(rep["warnings"]) if rep["warnings"] else "")
    save(session, obj, rep, "inbox")
    return True, f"Imported “{obj.get('title')}” with {len(rep['warnings'])} warning(s)."


HANDLERS["scenario"] = _handle_scenario


def process_inbox(session: Session, folder: Path | None = None) -> list[dict]:
    """Import every *.json in the inbox. Each file ends in processed/ or failed/ (+ .error.txt)."""
    from . import earnings  # noqa: F401  (registers the earnings_brief handler)

    folder = folder or inbox_dir(session)
    for sub in ("processed", "failed"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    results = []
    for f in sorted(folder.glob("*.json")):
        try:
            text = f.read_text(encoding="utf-8-sig")
            obj = extract(text, tuple(HANDLERS))
            ok, msg = HANDLERS[obj["kind"]](session, obj)
        except SpecError as exc:
            ok, msg = False, str(exc)
        except Exception as exc:  # a broken file must not stop the others
            log.exception("Inbox file %s failed", f.name)
            ok, msg = False, f"Unexpected error: {exc}"
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = folder / ("processed" if ok else "failed") / f.name
        if dest.exists():
            dest = dest.with_name(f"{f.stem}.{stamp}{f.suffix}")
        f.replace(dest)
        if not ok:
            dest.with_name(dest.name + ".error.txt").write_text(msg + "\n", encoding="utf-8")
        results.append({"file": f.name, "ok": ok, "message": msg, "moved_to": str(dest)})
        log.info("Inbox: %s -> %s", f.name, "processed" if ok else "failed")
    return results


_watching = False


def start_watcher(interval: float = 10.0) -> None:
    global _watching
    if _watching:
        return
    _watching = True
    import threading
    import time

    def loop():
        from . import db

        while True:
            try:
                with Session(db.get_engine()) as s:
                    process_inbox(s)
            except Exception as exc:
                log.warning("Inbox check failed: %s", exc)
            time.sleep(interval)

    threading.Thread(target=loop, name="inbox", daemon=True).start()
