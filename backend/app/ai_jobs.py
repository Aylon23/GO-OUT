"""Queued AI jobs that survive restarts and run out of daily quota gracefully.

A job is a list of steps, each one AI call. Steps are stored before anything is sent; a finished
step is never sent again. When the free daily quota runs out, the job waits until the next UTC day
and continues where it stopped. If the app stops mid-step, that step (only) runs again on resume.
One worker thread runs jobs one at a time, oldest first.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import socket
import threading
import uuid

from sqlmodel import Session, select, update

from . import openrouter
from .models import AiJob, AiJobStep

log = logging.getLogger("ai_jobs")
_hooks: dict[str, "callable"] = {}  # job kind prefix -> fn(session, job) called when a job finishes


def on_done(prefix: str, fn) -> None:
    """Register what happens when a job of this kind finishes (e.g. start the next debate round)."""
    _hooks[prefix] = fn


def _finished(session: Session, job: AiJob) -> None:
    for prefix, fn in list(_hooks.items()):
        if job.kind.startswith(prefix):
            try:
                fn(session, job)
            except Exception as exc:  # a broken follow-up must not break the queue
                log.warning("Follow-up for job %s failed: %s", job.id, exc)
                job.note = (job.note + f" · follow-up failed: {exc}")[:500]
                session.add(job)
                session.commit()
_run_lock = threading.Lock()
_ME = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
STALE_CLAIM = dt.timedelta(minutes=15)  # a claim by a process on another computer counts as abandoned after this
_wake = threading.Event()
_started = False


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _aware(t: dt.datetime | None) -> dt.datetime | None:
    return t.replace(tzinfo=dt.timezone.utc) if t is not None and t.tzinfo is None else t


def next_utc_day(now: dt.datetime | None = None) -> dt.datetime:
    now = now or _now()
    d = (now + dt.timedelta(days=1)).date()
    return dt.datetime(d.year, d.month, d.day, 0, 5, tzinfo=dt.timezone.utc)


def create(session: Session, kind: str, title: str, steps: list[dict]) -> AiJob:
    """steps: [{key, role, models, messages, schema, schema_name?, max_tokens?}]"""
    job = AiJob(kind=kind, title=title)
    session.add(job)
    session.commit()
    session.refresh(job)
    for i, s in enumerate(steps):
        req = {k: s[k] for k in ("models", "messages", "schema") if k in s}
        req["schema_name"] = s.get("schema_name", "answer")
        req["max_tokens"] = s.get("max_tokens", openrouter.MAX_TOKENS)
        session.add(AiJobStep(job_id=job.id, idx=i, key=s["key"], role=s.get("role", ""), request=json.dumps(req)))
    session.commit()
    _wake.set()
    return job


def run(session: Session, job_id: int) -> AiJob:
    """Run a job's unfinished steps in order. Stops (and waits) when the daily quota runs out."""
    with _run_lock:
        job = session.get(AiJob, job_id)
        if job is None or job.status in ("done", "failed"):
            return job
        if not openrouter.api_key():  # nothing can be sent; leave it queued until a key is added
            job.note = "Waiting for OPENROUTER_API_KEY in the .env file."
            session.add(job)
            session.commit()
            return job
        job.status, job.resume_after, job.updated_at = "running", None, _now()
        session.add(job)
        session.commit()
        steps = session.exec(select(AiJobStep).where(AiJobStep.job_id == job_id, AiJobStep.status != "done",
                                                     AiJobStep.status != "failed").order_by(AiJobStep.idx)).all()
        for st in steps:
            if not _claim(session, st):
                continue  # another process is sending this step
            session.refresh(st)
            req = json.loads(st.request)
            try:
                answer, call = openrouter.chat_json(session, st.role, req["models"], req["messages"], req["schema"],
                                                    req.get("schema_name", "answer"), req.get("max_tokens", openrouter.MAX_TOKENS),
                                                    job_id=job_id, step_key=st.key)
            except openrouter.QuotaExhausted:
                st.status = "pending"
                job.status, job.resume_after = "waiting_quota", next_utc_day()
                job.note = "Out of free AI requests for today. Continues after midnight UTC."
                session.add(st)
                session.add(job)
                session.commit()
                return job
            except openrouter.NoKey as exc:
                st.status = "pending"
                job.status, job.note = "queued", str(exc)
                session.add(st)
                session.add(job)
                session.commit()
                return job
            except Exception as exc:  # this step failed for good (retry and repair already used)
                st.status, st.error = "failed", str(exc)[:500]
                last = session.exec(select(openrouter.AiCall).where(openrouter.AiCall.job_id == job_id,
                                                                    openrouter.AiCall.step_key == st.key)
                                    .order_by(openrouter.AiCall.id.desc())).first()
                st.call_id = last.id if last else None
            else:
                st.status, st.result, st.call_id = "done", json.dumps(answer, ensure_ascii=False), call.id
            session.add(st)
            job.updated_at = _now()
            session.add(job)
            session.commit()
        session.expire_all()
        left = session.exec(select(AiJobStep).where(AiJobStep.job_id == job_id)).all()
        if any(x.status in ("pending", "running") for x in left):
            return session.get(AiJob, job_id)  # another process still has a step; it finishes the job
        ok = sum(x.status == "done" for x in left)
        note = f"{ok} of {len(left)} steps answered" + (f"; {len(left) - ok} failed" if ok < len(left) else "")
        # only one process may finish a job (and start what follows it)
        won = session.execute(update(AiJob).where(AiJob.id == job_id, AiJob.status == "running")
                              .values(status="done" if ok else "failed", note=note, updated_at=_now())).rowcount
        session.commit()
        job = session.get(AiJob, job_id)
        session.refresh(job)
        if not won:
            return job
    _finished(session, job)
    return job


def _alive(claimed_by: str) -> bool:
    """Whether the process that claimed a step is still running (unknown hosts count as alive until stale)."""
    if claimed_by == _ME:
        return False  # this process: a step left 'running' was interrupted here
    host, pid, _boot = (claimed_by.split(":") + ["", "", ""])[:3]
    if host != socket.gethostname():
        return True
    try:
        os.kill(int(pid), 0)
    except (ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    return True


def _claim(session: Session, st: AiJobStep) -> bool:
    """Atomically take a step to send. A step stays with its process unless that process is gone."""
    now = _now()
    if st.status == "running" and st.claimed_by and _alive(st.claimed_by):
        claimed = _aware(st.claimed_at)
        if claimed and now - claimed < STALE_CLAIM:
            return False
    cond = (AiJobStep.status == "pending") if st.status == "pending" else (
        (AiJobStep.status == "running") & (AiJobStep.claimed_by == st.claimed_by))
    n = session.execute(update(AiJobStep).where(AiJobStep.id == st.id, cond)
                        .values(status="running", claimed_by=_ME, claimed_at=now)).rowcount
    session.commit()
    return n == 1


def drain(session: Session, max_jobs: int = 500) -> int:
    """Run every due job, including the ones finished jobs create. Returns jobs run."""
    n = 0
    while n < max_jobs:
        todo = due(session)
        if not todo:
            return n
        for j in todo:
            before = j.status
            run(session, j.id)
            n += 1
            session.refresh(j)
            if j.status in ("queued", "waiting_quota") and before == j.status:
                return n  # can't progress now (no key or no quota)
    return n


def results(session: Session, job_id: int) -> dict[str, dict | None]:
    """Answers of a job's steps by key (None for failed or unfinished steps)."""
    out = {}
    for st in session.exec(select(AiJobStep).where(AiJobStep.job_id == job_id).order_by(AiJobStep.idx)).all():
        out[st.key] = json.loads(st.result) if st.status == "done" and st.result else None
    return out


def due(session: Session, now: dt.datetime | None = None) -> list[AiJob]:
    """Jobs to run now: queued, interrupted mid-run, or waiting for a new day that has come."""
    now = now or _now()
    out = []
    for j in session.exec(select(AiJob).where(AiJob.status.in_(["queued", "running", "waiting_quota"])).order_by(AiJob.id)).all():
        if j.status == "waiting_quota" and _aware(j.resume_after) and _aware(j.resume_after) > now:
            continue
        out.append(j)
    return out


def summary(session: Session, job: AiJob) -> dict:
    steps = session.exec(select(AiJobStep).where(AiJobStep.job_id == job.id).order_by(AiJobStep.idx)).all()
    return {"id": job.id, "kind": job.kind, "title": job.title, "status": job.status, "note": job.note,
            "created_at": job.created_at.isoformat(), "resume_after": _aware(job.resume_after).isoformat() if job.resume_after else None,
            "total": len(steps), "done": sum(s.status == "done" for s in steps), "failed": sum(s.status == "failed" for s in steps)}


def listing(session: Session, limit: int = 20) -> list[dict]:
    return [summary(session, j) for j in session.exec(select(AiJob).order_by(AiJob.id.desc()).limit(limit)).all()]


def resume(session: Session, job_id: int) -> None:
    job = session.get(AiJob, job_id)
    if job and job.status in ("waiting_quota", "queued", "running"):
        job.status, job.resume_after = "queued", None
        session.add(job)
        session.commit()
        _wake.set()


def start_worker(interval: float = 60.0) -> None:
    """Background worker: runs due jobs, and wakes at least every `interval` seconds."""
    global _started
    if _started:
        return
    _started = True
    from .db import get_engine

    def loop():
        while True:
            try:
                with Session(get_engine()) as s:
                    for j in due(s):
                        run(s, j.id)
            except Exception as exc:  # never let the worker die
                log.warning("AI job worker: %s", exc)
            _wake.wait(interval)
            _wake.clear()

    threading.Thread(target=loop, name="ai-jobs", daemon=True).start()
