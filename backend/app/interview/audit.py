"""Audit log writer — every employer HITL action is immutably appended here."""
from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models import AuditLog

VALID_ACTIONS = {
    "advance", "reject", "override_ai", "add_note",
    "whisper_inject", "pause_interview", "takeover", "resume_ai",
    "release_feedback", "dispute_flag", "dispute_resolve",
    "reveal_identity", "calibration_start",
}


def log(
    db: Session,
    *,
    tenant_id: str,
    actor_id: Optional[str],
    action: str,
    subject_type: str,
    subject_id: str,
    payload: Optional[Dict[str, Any]] = None,
) -> AuditLog:
    assert action in VALID_ACTIONS, f"Unknown audit action: {action!r}"
    entry = AuditLog(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action,
        subject_type=subject_type,
        subject_id=subject_id,
        payload_json=payload or {},
    )
    db.add(entry)
    db.flush()   # visible within same transaction
    return entry
