"""In-memory pub/sub for broadcasting interview events to employer monitoring SSE channels.

Works because Cloud Run is --session-affinity --min-instances=1.
For multi-instance scale-out, replace with Redis pub/sub — the interface is the same.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

# session_id → list of asyncio.Queues (one per connected employer tab)
_QUEUES: Dict[str, List[asyncio.Queue]] = {}


def subscribe(session_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _QUEUES.setdefault(session_id, []).append(q)
    return q


def unsubscribe(session_id: str, q: asyncio.Queue) -> None:
    qs = _QUEUES.get(session_id, [])
    if q in qs:
        qs.remove(q)
    if not qs:
        _QUEUES.pop(session_id, None)


async def broadcast(session_id: str, event: Dict[str, Any]) -> None:
    for q in list(_QUEUES.get(session_id, [])):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass   # slow employer client — drop rather than block audio
