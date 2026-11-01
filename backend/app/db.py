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
    return _engine


def set_engine(engine) -> None:
    """Used by tests to point the app at an in-memory database."""
    global _engine
    _engine = engine
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
