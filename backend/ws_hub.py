from __future__ import annotations

import asyncio
from collections import defaultdict

from fastapi import WebSocket


class ShowingHub:
    """In-process fan-out for WS /showings/{id}/live.

    Subscribers live in this process only, so a second server instance
    would not see these sockets. Redis pub/sub would be the multi-instance
    fix; it is on the roadmap, not today, which is why the deploy runs a
    single instance. Correctness of the seat map does not depend on it —
    every client resyncs through GET /state on connect, so a missed frame
    self-heals.
    """

    def __init__(self) -> None:
        self._sockets: dict[int, set[WebSocket]] = defaultdict(set)
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def lock_for(self, showing_id: int) -> asyncio.Lock:
        # WHY: EVAL decides seat ownership atomically, but this process
        # must publish those outcomes in the same order it obtained them.
        # Without this lock a slower claim can finish its snapshot read
        # and send after a faster one already published a newer map,
        # leaving clients painted with a stale seat colour.
        return self._locks[showing_id]

    def subscribe(self, showing_id: int, ws: WebSocket) -> None:
        self._sockets[showing_id].add(ws)

    def unsubscribe(self, showing_id: int, ws: WebSocket) -> None:
        self._sockets[showing_id].discard(ws)

    def has_subscribers(self, showing_id: int) -> bool:
        return bool(self._sockets[showing_id])

    async def broadcast(self, showing_id: int, payload: dict) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._sockets[showing_id]):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._sockets[showing_id].discard(ws)
