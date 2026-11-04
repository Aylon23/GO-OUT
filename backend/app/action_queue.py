"""The action queue: suggested decisions from the council, the scenario debate and (later) other sources.

Items are suggestions only. Nothing is recorded in the portfolios until the owner confirms it elsewhere.
Ranked by dollar impact. Step 3 builds the Home view with snooze and dismiss on top of this.
"""
from __future__ import annotations

import datetime as dt

from sqlmodel import Session, select

from .models import ActionItem, Portfolio


def add(session: Session, source: str, source_ref: str, what: str, why: str, suggested: str,
        portfolio_id: int | None = None, symbol: str = "", impact_amt: float = 0.0, replace_key: bool = True) -> ActionItem:
    """Add an item. With replace_key, an open item from the same source for the same person and symbol is replaced."""
    if replace_key:
        for old in session.exec(select(ActionItem).where(ActionItem.source == source, ActionItem.status == "open",
                                                         ActionItem.portfolio_id == portfolio_id, ActionItem.symbol == symbol)).all():
            session.delete(old)
    item = ActionItem(source=source, source_ref=str(source_ref), what=what, why=why, suggested=suggested,
                      portfolio_id=portfolio_id, symbol=symbol, impact_amt=float(abs(impact_amt)))
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def listing(session: Session, limit: int = 50, source: str | None = None, portfolio_id: int | None = None) -> list[dict]:
    today = dt.date.today()
    q = select(ActionItem).where(ActionItem.status.in_(["open", "snoozed"]))
    if source:
        q = q.where(ActionItem.source == source)
    if portfolio_id is not None:
        q = q.where(ActionItem.portfolio_id == portfolio_id)
    names = {p.id: p.name for p in session.exec(select(Portfolio)).all()}
    rows = [i for i in session.exec(q).all() if i.status == "open" or (i.snooze_until and i.snooze_until <= today)]
    rows.sort(key=lambda i: -i.impact_amt)
    return [{"id": i.id, "source": i.source, "source_ref": i.source_ref, "portfolio_id": i.portfolio_id,
             "who": names.get(i.portfolio_id, "Family") if i.portfolio_id else "Family", "symbol": i.symbol,
             "what": i.what, "why": i.why, "suggested": i.suggested, "impact_amt": i.impact_amt,
             "created_at": i.created_at.isoformat()} for i in rows[:limit]]


def set_status(session: Session, item_id: int, status: str, reason: str = "") -> None:
    i = session.get(ActionItem, item_id)
    if i is None:
        return
    i.status = status
    i.reason = reason
    if status == "snoozed":
        i.snooze_until = dt.date.today() + dt.timedelta(days=7)
    session.add(i)
    session.commit()
