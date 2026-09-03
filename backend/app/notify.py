"""Notification transport.

SMTP is deferred (plan.md §5 "Out"). The engine calls `send_email` at a few lifecycle
points; this keeps those call sites working and logs instead of silently dropping.
"""
from __future__ import annotations

import logging

log = logging.getLogger("hireiq.notify")


def send_email(to: str, subject: str, body: str, **_: object) -> bool:
    log.info("email suppressed (SMTP deferred) to=%s subject=%s", to, subject)
    return False
