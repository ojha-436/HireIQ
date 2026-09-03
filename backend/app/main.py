"""HireIQ API entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import Base, engine
from .routers import (candidate_auth, candidate_jobs, employer_auth, employer_jobs,
                      interview)

settings = get_settings()

@asynccontextmanager
async def lifespan(_: FastAPI):
    # Dev convenience. Production uses Alembic migrations.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    lifespan=lifespan,
    title="HireIQ API",
    version="0.1.0",
    description="Adaptive multi-persona AI interview panel (PS11).",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(employer_auth.router)
app.include_router(employer_jobs.router)
app.include_router(candidate_auth.router)
app.include_router(candidate_jobs.router)
app.include_router(interview.router)


@app.get("/api/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "hireiq", "version": "0.1.0"}


# The SPA is served by this app, at the same origin as the API. That is the whole
# point: a separate static server means a second port, and a second port means the
# frontend can silently talk to the wrong backend. Mounted last so /api/* wins.
#
# The directory sits in a different place in each layout, so both are tried:
#   repo      backend/app/main.py  ->  <repo>/frontend
#   container /app/app/main.py     ->  /app/frontend
# Hardcoding one of them 404s the entire SPA in the other.
def _find_frontend() -> Path | None:
    here = Path(__file__).resolve()
    for candidate in (here.parent.parent.parent / "frontend",   # repo checkout
                      here.parent.parent / "frontend"):        # container image
        if (candidate / "index.html").is_file():
            return candidate
    return None


_FRONTEND = _find_frontend()
if _FRONTEND is not None:
    app.mount("/", StaticFiles(directory=str(_FRONTEND), html=True), name="spa")
