"""Agora AccessToken2 ("007") builder — Python standard library only.

The repo's auth (PBKDF2 + HS256 JWT) deliberately uses no native/third-party crypto
deps; this follows suit. Wire format, for reference and for the round-trip test:

    token       = "007" + b64( zlib( pack_str(signature) + signing_info ) )
    signing_info= pack_str(app_id) + u32(issue_ts) + u32(expire) + u32(salt)
                  + u16(n_services) + service* ;   services ordered by type
    service     = u16(type) + u16(n_privileges) + (u16(privilege) + u32(expire))*
                  + pack_str(channel) + pack_str(uid)      # RTC service
    signature   = HMAC-SHA256( key = HMAC-SHA256( HMAC-SHA256(u32(issue_ts),
                  app_certificate), u32(salt) ), msg = signing_info )

All integers are little-endian. `expire` fields are SECONDS FROM issue_ts, not
absolute timestamps — a mistake worth stating out loud because it silently yields
tokens Agora rejects as expired.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import struct
import time
import zlib
from typing import Dict

VERSION = "007"
APP_ID_LENGTH = 32

# Service types
SERVICE_RTC = 1

# RTC privileges
PRIVILEGE_JOIN_CHANNEL = 1
PRIVILEGE_PUBLISH_AUDIO = 2
PRIVILEGE_PUBLISH_VIDEO = 3
PRIVILEGE_PUBLISH_DATA = 4


class AgoraTokenError(ValueError):
    """Raised for misconfiguration — never for a merely-expired token."""


# --- little-endian packers -------------------------------------------------

def _u16(x: int) -> bytes:
    return struct.pack("<H", x)


def _u32(x: int) -> bytes:
    return struct.pack("<I", x)


def _pack_str(b: bytes) -> bytes:
    return _u16(len(b)) + b


def _pack_map_u32(m: Dict[int, int]) -> bytes:
    out = _u16(len(m))
    for k in sorted(m):
        out += _u16(k) + _u32(m[k])
    return out


def _is_hex32(s: str) -> bool:
    if len(s) != APP_ID_LENGTH:
        return False
    try:
        binascii.unhexlify(s)
    except (binascii.Error, ValueError):
        return False
    return True


# --- builder ---------------------------------------------------------------

def build_rtc_token(
    app_id: str,
    app_certificate: str,
    channel_name: str,
    uid: int,
    ttl_seconds: int = 3600,
    *,
    publish: bool = True,
    issue_ts: int = 0,
    salt: int = 0,
) -> str:
    """Build an RTC AccessToken2 for `uid` on `channel_name`.

    `uid=0` means "any uid" — Agora encodes that as an empty uid string. We never
    use 0 for the candidate (we want the bot and the human to be distinguishable),
    but the encoding is kept faithful so third-party decoders agree with us.

    `issue_ts` / `salt` are injectable purely so tests are deterministic.
    """
    if not _is_hex32(app_id):
        raise AgoraTokenError("AGORA_APP_ID must be a 32-character hex string.")
    if not _is_hex32(app_certificate):
        raise AgoraTokenError(
            "AGORA_APP_CERTIFICATE must be a 32-character hex string. "
            "Token auth is disabled on the Agora project until a certificate is enabled."
        )
    if not channel_name:
        raise AgoraTokenError("channel_name is required.")
    if ttl_seconds <= 0:
        raise AgoraTokenError("ttl_seconds must be positive.")

    issue_ts = issue_ts or int(time.time())
    salt = salt or secrets.randbelow(99999999) + 1

    privileges: Dict[int, int] = {PRIVILEGE_JOIN_CHANNEL: ttl_seconds}
    if publish:
        privileges[PRIVILEGE_PUBLISH_AUDIO] = ttl_seconds
        privileges[PRIVILEGE_PUBLISH_VIDEO] = ttl_seconds
        privileges[PRIVILEGE_PUBLISH_DATA] = ttl_seconds

    service = (
        _u16(SERVICE_RTC)
        + _pack_map_u32(privileges)
        + _pack_str(channel_name.encode("utf-8"))
        + _pack_str(b"" if uid == 0 else str(uid).encode("utf-8"))
    )

    signing_info = (
        _pack_str(app_id.encode("utf-8"))
        + _u32(issue_ts)
        + _u32(ttl_seconds)
        + _u32(salt)
        + _u16(1)          # one service (RTC)
        + service
    )

    # Two-step key derivation, then sign. Order matters: issue_ts first, then salt.
    key = hmac.new(_u32(issue_ts), app_certificate.encode("utf-8"), hashlib.sha256).digest()
    key = hmac.new(_u32(salt), key, hashlib.sha256).digest()
    signature = hmac.new(key, signing_info, hashlib.sha256).digest()

    payload = zlib.compress(_pack_str(signature) + signing_info)
    return VERSION + base64.b64encode(payload).decode("ascii")


# --- decoder (used by the round-trip test; also handy for debugging) --------

def decode_rtc_token(token: str, app_certificate: str = "") -> Dict[str, object]:
    """Unpack a 007 token. When `app_certificate` is given, the signature is
    recomputed and `signature_ok` reports whether it matches."""
    if not token.startswith(VERSION):
        raise AgoraTokenError("Not an AccessToken2 ('007') token.")
    raw = zlib.decompress(base64.b64decode(token[len(VERSION):]))

    pos = 0

    def take(n: int) -> bytes:
        nonlocal pos
        chunk = raw[pos:pos + n]
        if len(chunk) != n:
            raise AgoraTokenError("Truncated token payload.")
        pos += n
        return chunk

    def take_str() -> bytes:
        return take(struct.unpack("<H", take(2))[0])

    signature = take_str()
    signing_start = pos
    app_id = take_str().decode("utf-8")
    issue_ts = struct.unpack("<I", take(4))[0]
    expire = struct.unpack("<I", take(4))[0]
    salt = struct.unpack("<I", take(4))[0]
    n_services = struct.unpack("<H", take(2))[0]

    services = []
    for _ in range(n_services):
        stype = struct.unpack("<H", take(2))[0]
        n_priv = struct.unpack("<H", take(2))[0]
        privs = {}
        for _ in range(n_priv):
            p = struct.unpack("<H", take(2))[0]
            privs[p] = struct.unpack("<I", take(4))[0]
        entry = {"type": stype, "privileges": privs}
        if stype == SERVICE_RTC:
            entry["channel"] = take_str().decode("utf-8")
            entry["uid"] = take_str().decode("utf-8")
        services.append(entry)

    out: Dict[str, object] = {
        "app_id": app_id, "issue_ts": issue_ts, "expire": expire, "salt": salt,
        "services": services, "expires_at": issue_ts + expire,
    }
    if app_certificate:
        key = hmac.new(_u32(issue_ts), app_certificate.encode("utf-8"), hashlib.sha256).digest()
        key = hmac.new(_u32(salt), key, hashlib.sha256).digest()
        expected = hmac.new(key, raw[signing_start:], hashlib.sha256).digest()
        out["signature_ok"] = hmac.compare_digest(expected, signature)
    return out
