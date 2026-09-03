from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import current_employer
from ..models import Tenant, TenantUser
from ..schemas import EmployerMe, EmployerRegister, LoginRequest, TokenResponse
from ..security import hash_password, mint_token, verify_password

router = APIRouter(prefix="/api/employer/auth", tags=["employer-auth"])


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(body: EmployerRegister, db: Session = Depends(get_db)) -> TokenResponse:
    existing = db.scalar(select(TenantUser).where(TenantUser.email == body.email.lower()))
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")

    tenant = Tenant(
        name=body.company_name,
        domain=body.domain,
        industry=body.industry,
        size_band=body.size_band,
    )
    db.add(tenant)
    db.flush()

    user = TenantUser(
        tenant_id=tenant.id,
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        role="admin",
        full_name=body.full_name,
    )
    db.add(user)
    db.commit()

    return TokenResponse(
        token=mint_token(user.id, "employer", {"tid": tenant.id, "role": user.role}),
        role="employer",
        expires_in=get_settings().jwt_ttl_hours * 3600,
    )


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.scalar(select(TenantUser).where(TenantUser.email == body.email.lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        # Same message for both branches — don't leak which emails exist.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email or password is incorrect")

    return TokenResponse(
        token=mint_token(user.id, "employer", {"tid": user.tenant_id, "role": user.role}),
        role="employer",
        expires_in=get_settings().jwt_ttl_hours * 3600,
    )


@router.get("/me", response_model=EmployerMe)
def me(user: TenantUser = Depends(current_employer), db: Session = Depends(get_db)) -> EmployerMe:
    tenant = db.get(Tenant, user.tenant_id)
    return EmployerMe(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        tenant_id=user.tenant_id,
        tenant_name=tenant.name if tenant else "",
    )
