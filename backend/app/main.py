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

    from .services import backfill_aliases

    with Session(get_engine()) as s:
        backfill_aliases(s)
        token = get_settings(s).telegram_token
    if token and os.environ.get("PORTFOLIO_NO_BOT") != "1":
        telegram_bot.start(token)
    if os.environ.get("PORTFOLIO_NO_WARM") != "1":
        import threading

        threading.Thread(target=_warm_market, name="warm-market", daemon=True).start()
        from . import factors

        factors.start_nightly()
    if os.environ.get("PORTFOLIO_NO_REPORTS") != "1":
        from . import person_reports

        person_reports.start_scheduler()
    if os.environ.get("PORTFOLIO_NO_BACKUP") != "1":
        from . import backup

        backup.start_scheduler()  # today's backup at the first start of the day, then hourly checks
    if os.environ.get("PORTFOLIO_NO_AI_WORKER") != "1":
        from . import ai_jobs

        ai_jobs.start_worker()
    if os.environ.get("PORTFOLIO_NO_INBOX") != "1":
        from . import specs

        specs.start_watcher()


def _warm_market() -> None:
    """Fetch the scenario page's market factors in the background, so its first run is quick."""
    from sqlmodel import Session

    from .market.cache import NoPriceData, ensure_history
    from .scenario_library import FACTOR_SYMBOLS

    log = logging.getLogger("warm")
    with Session(get_engine()) as s:
        for sym in FACTOR_SYMBOLS:
            try:
                ensure_history(s, sym)
            except NoPriceData as exc:
                log.info("No history for %s: %s", sym, exc)
            except Exception as exc:  # never let warming break the app
                log.warning("Warming %s failed: %s", sym, exc)


if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
