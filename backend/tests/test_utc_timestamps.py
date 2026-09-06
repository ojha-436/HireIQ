"""Every datetime this app writes is UTC (via utcnow()/datetime.now(timezone.utc)), but
SQLite drops tzinfo on the round trip. Before UTCDateTime (db.py), the API serialized a
naive ISO string with no "Z"/offset, and every browser not itself on UTC parsed it as
ITS OWN local time — a candidate in IST would see "5.5 hours ago" for something that had
just happened. This is the regression test for that: every timestamp in an API response
must be unambiguous.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


def _is_unambiguous_utc_iso(value: str) -> bool:
    """True if a JSON-serialized datetime string carries an explicit UTC marker —
    trailing 'Z', or a '+00:00'/'-00:00' offset. Anything else is ambiguous to a
    JS `new Date(...)` call, which is exactly the bug this guards against."""
    return value.endswith("Z") or value.endswith("+00:00") or value.endswith("-00:00")


def test_employer_me_created_at_is_unambiguous(client):
    reg = client.post("/api/employer/auth/register", json={
        "company_name": f"UTC Test Co {uuid.uuid4().hex[:6]}", "full_name": "Rea Patel",
        "email": f"utc-{uuid.uuid4().hex[:8]}@example.com", "password": "correct-horse-battery",
    }).json()
    h = {"Authorization": f"Bearer {reg['token']}"}

    job = client.post("/api/employer/jobs/", headers=h, json={"title": "UTC Test Role"}).json()
    assert _is_unambiguous_utc_iso(job["created_at"])

    jobs = client.get("/api/employer/jobs/", headers=h).json()
    assert _is_unambiguous_utc_iso(jobs[0]["created_at"])


def test_candidate_application_timestamps_are_unambiguous(client):
    emp = client.post("/api/employer/auth/register", json={
        "company_name": f"UTC Test Co {uuid.uuid4().hex[:6]}", "full_name": "Rea Patel",
        "email": f"utc-emp-{uuid.uuid4().hex[:8]}@example.com", "password": "correct-horse-battery",
    }).json()
    eh = {"Authorization": f"Bearer {emp['token']}"}
    job = client.post("/api/employer/jobs/", headers=eh, json={"title": "UTC Test Role 2"}).json()
    client.post(f"/api/employer/jobs/{job['id']}/publish", headers=eh)

    cand = client.post("/api/candidate/auth/register", json={
        "full_name": "UTC Candidate", "email": f"utc-cand-{uuid.uuid4().hex[:8]}@example.com",
        "password": "another-good-passphrase",
    }).json()
    ch = {"Authorization": f"Bearer {cand['token']}"}
    app_row = client.post(f"/api/candidate/apply/{job['id']}", headers=ch).json()
    assert _is_unambiguous_utc_iso(app_row["applied_at"])
    assert _is_unambiguous_utc_iso(app_row["last_activity_at"])

    apps = client.get("/api/candidate/me/applications", headers=ch).json()
    assert _is_unambiguous_utc_iso(apps[0]["applied_at"])


def test_notification_created_at_is_unambiguous(client):
    emp = client.post("/api/employer/auth/register", json={
        "company_name": f"UTC Test Co {uuid.uuid4().hex[:6]}", "full_name": "Rea Patel",
        "email": f"utc-emp2-{uuid.uuid4().hex[:8]}@example.com", "password": "correct-horse-battery",
    }).json()
    eh = {"Authorization": f"Bearer {emp['token']}"}
    job = client.post("/api/employer/jobs/", headers=eh, json={"title": "UTC Test Role 3"}).json()
    client.post(f"/api/employer/jobs/{job['id']}/publish", headers=eh)

    cand = client.post("/api/candidate/auth/register", json={
        "full_name": "UTC Candidate 2", "email": f"utc-cand2-{uuid.uuid4().hex[:8]}@example.com",
        "password": "another-good-passphrase",
    }).json()
    ch = {"Authorization": f"Bearer {cand['token']}"}
    app_row = client.post(f"/api/candidate/apply/{job['id']}", headers=ch).json()
    client.post(f"/api/employer/applications/{app_row['id']}/start-interview", headers=eh)

    notifs = client.get("/api/candidate/me/notifications", headers=ch).json()
    assert notifs["notifications"], "expected at least one notification"
    assert _is_unambiguous_utc_iso(notifs["notifications"][0]["created_at"])
