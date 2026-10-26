"""FastAPI application: JSON API under /api, the built frontend at /, worker in-process
for local use (set DOCINTEL_RUN_WORKER_IN_API=false and run `docintel worker` to split)."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..db import init_db
from ..settings import REPO_ROOT, get_settings
from .documents import router as documents_router
from .evaluation import router as eval_router
from .ops import router as ops_router

log = logging.getLogger("docintel")
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db(settings)
    worker_task = None
    worker = None
    if settings.run_worker_in_api:
        from ..queue.worker import Worker
        worker = Worker(settings)
        worker_task = asyncio.create_task(worker.run())
    yield
    if worker is not None:
        worker.stop()
        await asyncio.gather(worker_task, return_exceptions=True)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="DocIntel", version="0.1.0", lifespan=lifespan,
                  description="Agreement intelligence: contracts → verified, structured data.")
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    app.include_router(documents_router)
    app.include_router(eval_router)
    app.include_router(ops_router)
    if FRONTEND_DIST.exists():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            candidate = FRONTEND_DIST / path
            if path and candidate.is_file() and FRONTEND_DIST in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST / "index.html")
    return app


app = create_app()


def static_dir() -> Path:
    return FRONTEND_DIST
