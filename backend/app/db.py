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
        _engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False, "timeout": 30})
        SQLModel.metadata.create_all(_engine)
        migrate(_engine)
    return _engine


def migrate(engine) -> None:
    """Bring an existing database up to date: add any column the models have and the file lacks
    (new features keep old data), and make sure portfolio 1 exists."""
    from sqlalchemy import text

    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            have = {r[1] for r in conn.execute(text(f'PRAGMA table_info("{table.name}")'))}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = col.type.compile(dialect=engine.dialect)
                default = col.default.arg if col.default is not None and not callable(col.default.arg) else None
                if isinstance(default, bool):
                    default = int(default)
                if default is None and not col.nullable:
                    default = 0 if ddl.upper() in ("INTEGER", "FLOAT", "BOOLEAN") else ""
                clause = f" DEFAULT {default!r}" if default is not None else ""
                null = " NOT NULL" if not col.nullable and default is not None else ""
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}{null}{clause}'))
    from .models import Portfolio

    from sqlmodel import select

    with Session(engine) as s:
        # only on a brand-new database: a portfolio the user deleted must stay deleted
        if s.exec(select(Portfolio)).first() is None:
            s.add(Portfolio(id=1, name="My portfolio"))
            s.commit()


def set_engine(engine) -> None:
    """Used by tests to point the app at an in-memory database."""
    global _engine
    _engine = engine
    SQLModel.metadata.create_all(engine)
    migrate(engine)


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
