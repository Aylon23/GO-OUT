"""Audit log: every write the app makes on the user's behalf, with how to undo it."""
from __future__ import annotations

import json

from sqlmodel import Session, select

from .models import AuditEntry


def record(session: Session, action: str, summary: str, detail: dict | None = None, undo: dict | None = None) -> AuditEntry:
    e = AuditEntry(action=action, summary=summary, detail=json.dumps(detail or {}, ensure_ascii=False, default=str),
                   undo=json.dumps(undo) if undo else "")
    session.add(e)
    session.commit()
    session.refresh(e)
    return e


def recent(session: Session, limit: int = 50, action: str | None = None) -> list[dict]:
    q = select(AuditEntry).order_by(AuditEntry.id.desc()).limit(limit)
    if action:
        q = q.where(AuditEntry.action == action)
    return [{"id": e.id, "at": e.at.isoformat(), "action": e.action, "summary": e.summary,
             "detail": json.loads(e.detail or "{}"), "can_undo": bool(e.undo) and e.undone_at is None,
             "undone_at": e.undone_at.isoformat() if e.undone_at else None} for e in session.exec(q).all()]
