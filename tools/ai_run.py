"""Run the AI scenario debate or the stock council from the command line and print a report.

    .venv/bin/python tools/ai_run.py debate "תהיה מלחמה עם איראן" --horizon 1m
    .venv/bin/python tools/ai_run.py debate "oil falls $10 per barrel" --horizon 1m --cause supply
    .venv/bin/python tools/ai_run.py council --top 10

Uses the app's own database, the OPENROUTER_API_KEY in .env and the same rules as the app (free models,
privacy check on every request, pacing, daily quota). If the day's free requests run out, the job waits and
the app finishes it after midnight UTC.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlmodel import Session, select  # noqa: E402

from app import ai_jobs, council, debate, debate_input, openrouter  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.factors import FACTORS  # noqa: E402

LABEL = {f["id"]: f["label"] for f in FACTORS}


def quota(s: Session) -> str:
    q = openrouter.quota_status(s)
    return f"{q.get('remaining')} of {q.get('limit')} free AI requests left today" if q.get("known") else "quota unknown"


def run_debate(s: Session, text: str, horizon: str | None, cause: str | None) -> None:
    inp = debate_input.read(s, text, horizon, cause)
    if inp["needs"]:
        print("Missing:", ", ".join(f"--{k} ({'/'.join(v)})" for k, v in inp["needs"].items()))
        sys.exit(2)
    for c in inp["dollar_moves"]:
        print("Read:", c.get("text") or c)
    print("Preparing the data (the first time this fetches a lot of price history)...")
    d = debate.start(s, inp["text"], inp["horizon"], inp["cause"], inp, background=False)
    print(f"Debate #{d.id} started ({quota(s)}).")
    ai_jobs.drain(s)
    s.refresh(d)
    p = debate.payload(s, d)
    print(f"\n=== {text} ({d.horizon}, {d.cause}) ===")
    print(f"Phase: {p['phase']} · requests used: {p['requests']} · {quota(s)}")
    if p["phase"] != "done":
        print(p.get("note") or p.get("waiting") or "Not finished; the app will continue it.")
        return
    r = p["result"]
    print("\n" + r.get("answer", ""))
    print("\nDisputes:")
    for x in r["disputes"] or [{"topic": "(none listed)", "views": ""}]:
        print(f"  - {x['topic']}: {x['views']} {x.get('why', '')}".rstrip())
    for title, rows in (("Most hurt", r.get("hurt", [])), ("Most helped", r.get("helped", []))):
        print(f"\n{title}:")
        for h in rows:
            print(f"  {h['symbol']:<8} {h['move'] * 100:+6.1f}%  {h['change_amt']:+12,.0f}  driven by {LABEL.get(h['driver'], h['driver'])}")
    if r.get("most_exposed"):
        print(f"\nMost exposed: {r['most_exposed']['name']} ({r['most_exposed']['median'] * 100:+.1f}%)")


def run_council(s: Session, top: int) -> None:
    syms = council.universe(s)[:top]
    run = council.start(s, syms, kind="full", background=False)
    print(f"Council run #{run.id} on {', '.join(syms)} ({quota(s)}).")
    ai_jobs.drain(s)
    st = council.run_status(s, s.get(council.CouncilRun, run.id))
    print(f"\n{st['rated']} of {st['total']} rated · requests used: {st['requests']} · {quota(s)}")
    if st.get("waiting"):
        print(st["waiting"])
    vs = [v for v in s.exec(select(council.CouncilVerdict).where(council.CouncilVerdict.run_id == run.id)).all()]
    print(f"\n{'Stock':<8} {'Buy':>3} {'Hold':>4} {'Sell':>4} {'Insuf':>5}  {'Consensus':<11} {'Majority':<8} {'Quality':<14} Crowded")
    for v in vs:
        t = json.loads(v.tally)
        print(f"{v.ticker:<8} {t['buy']:>3} {t['hold']:>4} {t['sell']:>4} {t['insufficient']:>5}  {v.consensus:<11} {v.majority or '-':<8} "
              f"{v.quality or '-':<14} {'yes' if v.crowded else ''}")
    split = [v for v in vs if v.consensus == "split"]
    print("\nBiggest disagreements:")
    for v in split or []:
        print(f"  {v.ticker}: {json.loads(v.tally)} · dissent: {v.dissent[:200]}")
    if not split:
        print("  none")
    print("\nCrowded:", ", ".join(v.ticker for v in vs if v.crowded) or "none")
    from app import action_queue

    print("\nPer-person actions (suggestions, nothing recorded):")
    for a in action_queue.listing(s, source="council"):
        if a["source_ref"] == str(run.id):
            print(f"  {a['who']}: {a['what']} · {a['why']}")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):  # Hebrew in the Windows console
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("debate")
    d.add_argument("text")
    d.add_argument("--horizon", choices=debate_input.HORIZONS)
    d.add_argument("--cause", choices=debate_input.CAUSES)
    c = sub.add_parser("council")
    c.add_argument("--top", type=int, default=10)
    a = ap.parse_args()
    if not openrouter.api_key():
        print("Add OPENROUTER_API_KEY=... to the .env file next to the app first.")
        sys.exit(1)
    with Session(get_engine()) as s:
        if a.cmd == "debate":
            run_debate(s, a.text, a.horizon, a.cause)
        else:
            run_council(s, a.top)


if __name__ == "__main__":
    main()
