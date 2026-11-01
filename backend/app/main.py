from __future__ import annotations

import logging
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


if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
