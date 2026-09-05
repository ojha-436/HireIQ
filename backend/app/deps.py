"""Auth dependencies. Audience isolation is enforced here, once."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from .db import get_db
from .models import AdminUser, Candidate, Tenant, TenantUser
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
    tenant = db.get(Tenant, user.tenant_id)
    if tenant is not None and not tenant.active:
        # An admin suspended this workspace. A previously-issued, still-unexpired
        # token must stop working immediately, not just at the next login.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been suspended")
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
    if not cand.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been suspended")
    return cand


def current_admin(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> AdminUser:
    try:
        payload = read_token(_bearer(authorization), "admin")
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {exc}") from exc

    admin = db.get(AdminUser, int(payload["sub"]))
    if admin is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown admin")
    return admin
