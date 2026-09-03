# HireIQ — single container: FastAPI serves the API and the static SPA.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/scripts ./scripts
COPY frontend ./frontend

# Seed the role-play bank at build time. Without it the `scenarios` table is empty,
# R7 finds no scenario, and PS11 requirement #6 silently does not happen in production.
ENV DATABASE_URL=sqlite:////app/hireiq.db
RUN python scripts/seed_scenarios.py

# Cloud Run supplies $PORT. Session affinity + a warm instance keep the
# in-memory InterviewRuntime registry alive between reconnects (see ARCHITECTURE.md §8).
ENV PORT=8080
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --timeout-keep-alive 75
