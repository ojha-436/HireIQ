"""Auth dependencies. Audience isolation is enforced here, once."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from .db import get_db
from .models import Candidate, TenantUser
from .security import TokenError, read_token


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    return authorization.split(" ", 1)[1].strip()


def current_employer(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> TenantUser:
    try:
        payload = read_token(_bearer(authorization), "employer")
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {exc}") from exc

    user = db.get(TenantUser, int(payload["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown user")
    return user


def current_candidate(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Candidate:
    try:
        payload = read_token(_bearer(authorization), "candidate")
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {exc}") from exc

    cand = db.get(Candidate, int(payload["sub"]))
    if cand is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown candidate")
    return cand
