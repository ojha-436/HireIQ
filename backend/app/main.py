"""HireIQ API entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import Base, SessionLocal, engine, run_light_migrations
from .models import AdminUser
from .routers import (admin, candidate_auth, candidate_jobs, employer_auth,
                      employer_jobs, interview, practice)
from .security import hash_password

settings = get_settings()


def _seed_admin() -> None:
    """First run only: 'admin' / 'admin@123', changeable via the admin settings page."""
    db = SessionLocal()
    try:
        if db.query(AdminUser).first() is None:
            db.add(AdminUser(username="admin", password_hash=hash_password("admin@123")))
            db.commit()
    finally:
        db.close()


def _seed_reference_data() -> None:
    """Fill the scenario and question banks if they are empty.

    The Dockerfile seeds both at BUILD time, but it seeds them into the SQLite file
    baked into the image. Pointed at Postgres, that work is invisible: the role-play
    bank is empty, so rule R7 never fires and PS11 requirement #6 quietly does not
    happen, and the fallback question bank is empty, so a lull in an interview has
    nothing behind it. Seeding here instead makes the banks a property of the DATABASE
    rather than of the image.

    Idempotent by construction — both scripts no-op when their table already has rows —
    so this costs one COUNT per boot after the first. Never fatal: reference data is
    what makes an interview good, not what makes the service able to start.
    """
    import os  # noqa: PLC0415

    if os.getenv("HIREIQ_TEST"):
        # The suite builds the app many times over a throwaway SQLite file; shelling out
        # to the seed scripts each time would add minutes and prove nothing.
        return

    from .models import InterviewQuestion, Scenario  # noqa: PLC0415

    db = SessionLocal()
    try:
        need_scenarios = db.query(Scenario).first() is None
        need_questions = db.query(InterviewQuestion).first() is None
    finally:
        db.close()
    if not (need_scenarios or need_questions):
        return

    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    root = Path(__file__).resolve().parent.parent
    jobs = []
    if need_scenarios:
        jobs.append(["scripts/seed_scenarios.py"])
    if need_questions:
        jobs.append(["scripts/seed_questions.py", "--per-skill", "6"])
    for argv in jobs:
        if not (root / argv[0]).is_file():
            continue
        try:
            proc = subprocess.run([sys.executable, *argv], cwd=root, capture_output=True,
                                  text=True, timeout=600, check=False)
            tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-1:]
            print("seed {}: rc={} {}".format(argv[0], proc.returncode, " ".join(tail)))
        except Exception as exc:  # noqa: BLE001
            print("seed {} failed: {}: {}".format(argv[0], type(exc).__name__, exc))


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Dev convenience. Production uses Alembic migrations.
    Base.metadata.create_all(bind=engine)
    run_light_migrations()
    _seed_admin()
    _seed_reference_data()
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
app.include_router(practice.router)
app.include_router(admin.router)


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
