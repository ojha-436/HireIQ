"""Candidate-facing notifications — the read side of the `Notification` table.

Rows are written at the point something changes for a candidate that they did not
just do themselves: an employer starts an interview on their application, advances
or rejects them, or releases feedback. See `notify_candidate` below (the write side,
called from routers/interview.py) and app/interview/session.py's auto-advance path.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_candidate
from ..models import Candidate, Notification

router = APIRouter(prefix="/api/candidate/me/notifications", tags=["candidate-notifications"])


def notify_candidate(db: Session, *, candidate_id: int, kind: str, payload: dict[str, Any]) -> None:
    """Queue one candidate notification. The caller still owns `db.commit()` — this
    is meant to be added alongside whatever other write triggered it, in one
    transaction, not as a separate round-trip."""
    db.add(Notification(recipient_type="candidate", recipient_id=candidate_id,
                        kind=kind, payload_json=payload))


@router.get("")
def list_notifications(
    cand: Candidate = Depends(current_candidate), db: Session = Depends(get_db),
) -> dict[str, Any]:
    rows = db.scalars(
        select(Notification)
        .where(Notification.recipient_type == "candidate", Notification.recipient_id == cand.id)
        .order_by(Notification.created_at.desc())
        .limit(50)
    ).all()
    return {
        "unread_count": sum(1 for r in rows if r.read_at is None),
        "notifications": [
            {"id": r.id, "kind": r.kind, "payload": r.payload_json or {},
             "created_at": r.created_at, "read": r.read_at is not None}
            for r in rows
        ],
    }


@router.post("/{notification_id}/read")
def mark_read(
    notification_id: int, cand: Candidate = Depends(current_candidate), db: Session = Depends(get_db),
) -> dict[str, bool]:
    n = db.get(Notification, notification_id)
    if n is None or n.recipient_type != "candidate" or n.recipient_id != cand.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
    if n.read_at is None:
        n.read_at = datetime.now(timezone.utc)
        db.commit()
    return {"ok": True}


@router.post("/read-all")
def mark_all_read(
    cand: Candidate = Depends(current_candidate), db: Session = Depends(get_db),
) -> dict[str, Any]:
    rows = db.scalars(
        select(Notification).where(
            Notification.recipient_type == "candidate",
            Notification.recipient_id == cand.id,
            Notification.read_at.is_(None),
        )
    ).all()
    now = datetime.now(timezone.utc)
    for r in rows:
        r.read_at = now
    db.commit()
    return {"ok": True, "marked": len(rows)}
