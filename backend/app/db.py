from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from sqlmodel import Session, SQLModel, create_engine

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("PORTFOLIO_DB", ROOT / "data" / "portfolio.db"))

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(_engine)
        migrate(_engine)
    return _engine


def migrate(engine) -> None:
    """Add columns introduced after the first release, and make sure portfolio 1 exists."""
    from sqlalchemy import text

    with engine.begin() as conn:
        cols = {r[1] for r in conn.execute(text('PRAGMA table_info("transaction")'))}
        if "portfolio_id" not in cols:
            conn.execute(text('ALTER TABLE "transaction" ADD COLUMN portfolio_id INTEGER NOT NULL DEFAULT 1'))
        if "cost_price" not in cols:
            conn.execute(text('ALTER TABLE "transaction" ADD COLUMN cost_price FLOAT'))
        if not conn.execute(text("SELECT 1 FROM portfolio WHERE id = 1")).first():
            conn.execute(text("INSERT INTO portfolio (id, name, note, created_at) VALUES (1, 'My portfolio', '', CURRENT_TIMESTAMP)"))


def set_engine(engine) -> None:
    """Used by tests to point the app at an in-memory database."""
    global _engine
    _engine = engine
    SQLModel.metadata.create_all(engine)
    migrate(engine)


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
