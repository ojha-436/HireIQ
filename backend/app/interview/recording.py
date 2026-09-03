"""Agora Cloud Recording integration (Phase 7).

All functions are no-ops when AGORA_RECORDING_KEY is not set — the interview runs
normally, just without a persistent audio recording. This makes local dev and CI
work without Agora credentials while keeping the code path exercisable via mocking.

Region is locked to ap-southeast (covers India) per the product decisions in
changeplan.md.

GCS signed URL generation requires the cryptography package (optional dep). If it
is not installed, generate_signed_url() returns None — the employer sees the raw
GCS path instead of a playable link.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from app.config import settings

AGORA_RECORDING_BASE = "https://api.agora.io/v1/apps/{app_id}/cloud_recording"


def _configured() -> bool:
    """True only when all three Agora credentials are present."""
    return bool(
        settings.AGORA_APP_ID
        and settings.AGORA_RECORDING_KEY
        and settings.AGORA_RECORDING_SECRET
    )


def _auth_header() -> str:
    cred = base64.b64encode(
        f"{settings.AGORA_RECORDING_KEY}:{settings.AGORA_RECORDING_SECRET}".encode()
    ).decode()
    return f"Basic {cred}"


def acquire(channel: str, uid: int = 1) -> Optional[str]:
    """Acquire a recording resource. Returns resourceId or None if not configured."""
    if not _configured():
        return None
    url = AGORA_RECORDING_BASE.format(app_id=settings.AGORA_APP_ID) + "/acquire"
    body = json.dumps({"cname": channel, "uid": str(uid), "clientRequest": {}}).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": _auth_header(), "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r).get("resourceId")
    except Exception:  # noqa: BLE001
        return None


def start(resource_id: str, channel: str, uid: int = 1) -> Optional[str]:
    """Start cloud recording. Returns sid (Agora recording session ID) or None."""
    if not _configured() or not resource_id:
        return None
    url = (
        AGORA_RECORDING_BASE.format(app_id=settings.AGORA_APP_ID)
        + f"/resourceid/{resource_id}/mode/mix/start"
    )
    storage_config = {
        "vendor": 2,  # Google Cloud Storage
        "region": 0,  # GCS region is controlled by the bucket location, not this field
        "bucket": settings.GCS_RECORDING_BUCKET,
        "accessKey": "",  # filled from service account at deploy time
        "secretKey": "",
    }
    body = json.dumps({
        "cname": channel,
        "uid": str(uid),
        "clientRequest": {
            "recordingConfig": {"maxIdleTime": 60, "streamTypes": 1},  # audio only
            "storageConfig": storage_config,
        },
    }).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": _auth_header(), "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r).get("sid")
    except Exception:  # noqa: BLE001
        return None


def stop(resource_id: str, sid: str, channel: str, uid: int = 1) -> Optional[str]:
    """Stop cloud recording. Returns GCS URI (gs://...) or None."""
    if not _configured() or not resource_id or not sid:
        return None
    url = (
        AGORA_RECORDING_BASE.format(app_id=settings.AGORA_APP_ID)
        + f"/resourceid/{resource_id}/sid/{sid}/mode/mix/stop"
    )
    body = json.dumps({"cname": channel, "uid": str(uid), "clientRequest": {}}).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": _auth_header(), "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
            files = data.get("serverResponse", {}).get("fileList", [])
            if files:
                fname = files[0].get("fileName", "")
                if fname:
                    return f"gs://{settings.GCS_RECORDING_BUCKET}/{fname}"
        return None
    except Exception:  # noqa: BLE001
        return None


def delete_gcs_object(gcs_uri: str) -> bool:
    """Delete a GCS object. Returns True if deleted or already gone, False otherwise."""
    if not gcs_uri or not settings.GCS_PROJECT_ID:
        return False
    if not gcs_uri.startswith("gs://"):
        return False
    parts = gcs_uri[5:].split("/", 1)
    if len(parts) != 2:
        return False
    bucket, obj_path = parts
    auth = _gcs_auth_header()
    if not auth:
        return False
    url = (
        f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/"
        + urllib.parse.quote(obj_path, safe="")
    )
    req = urllib.request.Request(url, method="DELETE", headers={"Authorization": auth})
    try:
        urllib.request.urlopen(req, timeout=15)
        return True
    except urllib.error.HTTPError as exc:
        return exc.code == 404  # already gone counts as success
    except Exception:  # noqa: BLE001
        return False


def generate_signed_url(gcs_uri: str, expires_seconds: int = 86400) -> Optional[str]:
    """Generate a V2 signed URL for GCS playback (24h default). Returns None if not configured."""
    if not gcs_uri or not settings.GCS_PROJECT_ID or not settings.GCS_SERVICE_ACCOUNT_JSON:
        return None
    try:
        import time  # noqa: PLC0415

        sa = json.loads(settings.GCS_SERVICE_ACCOUNT_JSON)
        parts = gcs_uri[5:].split("/", 1)
        if len(parts) != 2:
            return None
        bucket, obj = parts
        expiry = int(time.time()) + expires_seconds
        string_to_sign = f"GET\n\n\n{expiry}\n/{bucket}/{obj}"
        private_key_pem = sa["private_key"].encode()
        try:
            from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
            from cryptography.hazmat.primitives.asymmetric import padding  # noqa: PLC0415

            key = serialization.load_pem_private_key(private_key_pem, password=None)
            sig = key.sign(string_to_sign.encode(), padding.PKCS1v15(), hashes.SHA256())
            sig_b64 = base64.b64encode(sig).decode()
        except ImportError:
            return None
        params = urllib.parse.urlencode({
            "GoogleAccessId": sa["client_email"],
            "Expires": expiry,
            "Signature": sig_b64,
        })
        return (
            f"https://storage.googleapis.com/{bucket}/"
            + urllib.parse.quote(obj)
            + "?"
            + params
        )
    except Exception:  # noqa: BLE001
        return None


def _gcs_auth_header() -> Optional[str]:
    """Return an Authorization header for GCS REST API calls, or None if not configured.

    For the hackathon, this returns None when no service account is configured — callers
    treat None as "GCS not available" and skip the deletion gracefully. A full production
    implementation would use google-auth to exchange the service account for an OAuth2
    bearer token.
    """
    if not settings.GCS_SERVICE_ACCOUNT_JSON:
        return None
    # Placeholder: real implementation needs google-auth or manual JWT/OAuth2 exchange.
    # Returning None means delete_gcs_object() short-circuits safely.
    return None
