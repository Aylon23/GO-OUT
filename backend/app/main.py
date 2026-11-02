from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .db import get_engine

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Benchmark", version="0.1.0")
app.include_router(router)

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@app.on_event("startup")
def _startup() -> None:
    get_engine()
    # start the Telegram bot if one is set up
    from sqlmodel import Session

    from . import telegram_bot
    from .services import get_settings

    with Session(get_engine()) as s:
        token = get_settings(s).telegram_token
    if token and os.environ.get("PORTFOLIO_NO_BOT") != "1":
        telegram_bot.start(token)


if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
