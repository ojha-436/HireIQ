"""Stdlib-only auth primitives: PBKDF2-SHA256 password hashing + HS256 JWT.

Three token audiences ('employer', 'candidate', 'admin') signed with three different
secrets, so a token minted for one portal is structurally unusable against the others.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from .config import get_settings

_ITERATIONS = 240_000
_ALGO = "pbkdf2_sha256"


# --------------------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != _ALGO:
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


# --------------------------------------------------------------------------- jwt
def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64u_decode(seg: str) -> bytes:
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def _secret_for(audience: str) -> str:
    s = get_settings()
    if audience == "employer":
        return s.employer_jwt_secret
    if audience == "admin":
        return s.admin_jwt_secret
    return s.jwt_secret


def mint_token(subject: str, audience: str, claims: dict[str, Any] | None = None) -> str:
    """audience is 'employer' or 'candidate' — it selects the signing secret."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": str(subject),
        "aud": audience,
        "iat": now,
        "exp": now + get_settings().jwt_ttl_hours * 3600,
    }
    payload.update(claims or {})

    header = _b64u(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64u(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode()
    sig = hmac.new(_secret_for(audience).encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{body}.{_b64u(sig)}"


class TokenError(Exception):
    pass


def read_token(token: str, expected_audience: str) -> dict[str, Any]:
    """Verify signature, audience and expiry. Raises TokenError on any failure."""
    try:
        header_b64, body_b64, sig_b64 = token.split(".")
    except ValueError as exc:
        raise TokenError("malformed token") from exc

    signing_input = f"{header_b64}.{body_b64}".encode()
    expected = hmac.new(
        _secret_for(expected_audience).encode(), signing_input, hashlib.sha256
    ).digest()
    if not hmac.compare_digest(_b64u_decode(sig_b64), expected):
        raise TokenError("bad signature")

    try:
        payload = json.loads(_b64u_decode(body_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise TokenError("bad payload") from exc

    if payload.get("aud") != expected_audience:
        raise TokenError("wrong audience")
    if int(payload.get("exp", 0)) < int(time.time()):
        raise TokenError("expired")
    return payload


# --------------------------------------------------------------- engine compatibility
def decode_access_token(token: str) -> dict[str, Any] | None:
    """Employer-audience token -> claims, or None. Used by the ported interview engine."""
    try:
        return read_token(token, "employer")
    except TokenError:
        return None


def decode_candidate_token(token: str) -> dict[str, Any] | None:
    """Candidate-audience token -> claims, or None."""
    try:
        return read_token(token, "candidate")
    except TokenError:
        return None
