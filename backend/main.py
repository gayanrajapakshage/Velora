from __future__ import annotations

import asyncio
import os
import random
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from db import get_session
from models import Booking, Seat, Showing, Venue
from seat_lock import (
    AVAILABLE,
    BOOKED,
    HELD,
    HOLD_TTL_SECONDS,
    MAX_OWNED_SEATS,
    ClaimResult,
    SeatLive,
    book_seat,
    claim_seat,
    public_holder,
    read_seat_map,
    read_stats,
    redis_from_env,
    release_seat,
    seat_key,
    stats_key,
)
from ws_hub import ShowingHub

hub = ShowingHub()

# Rotating pool of simulated patrons for the live demo.
SIM_PATRONS = [
    "Maya Chen",
    "Jordan Hale",
    "Priya Nair",
    "Luis Ortega",
    "Amina Diallo",
    "Noah Patel",
    "Elena Rossi",
    "Kai Nakamura",
]

# Simulated bookings stop at this many seats so a long-running demo does
# not sell out the house and go static.
SIM_BOOKED_CAP = 18

# Pacing. An abandoned hold occupies its seat for the full 90s TTL, so the
# claim rate sets how much of the house is tied up in steady state:
# roughly (workers / mean sleep) * 90 seats. These values target ~15 held
# seats out of 96 — visibly alive every few seconds, never locked up.
SIM_MIN_WAIT = 20.0
SIM_MAX_WAIT = 45.0

# Names used for the "race me for this seat" demo.
DEMO_RACERS = [
    "Sam Rivera",
    "Leah Brooks",
    "Omar Hassan",
    "Nina Volkov",
    "Chris Nguyen",
    "Ivy Parker",
    "Theo Marin",
    "Jade Okonkwo",
    "Ben Walsh",
    "Sofia Alvarez",
    "Marcus Kim",
]

# Seat ids never change for a showing, so they are read once.
_seat_ids: dict[int, list[str]] = {}
# Last published seat signature per showing, for change detection.
_snapshots: dict[int, dict[str, tuple]] = {}
_versions: dict[int, int] = {}
# Recent seat changes in plain language, newest first.
_events: dict[int, deque] = {}
# Public holder id -> display name, so the feed can name people without
# ever storing or broadcasting their private token.
_holder_names: dict[str, str] = {}
# Simulated patrons and expiry watchers, started when a showing is opened.
_sim_tasks: dict[int, list[asyncio.Task]] = {}
_sim_lock = asyncio.Lock()
# In-process seat map. Redis stays the write authority (Lua), but free-tier
# Upstash cannot survive a full 96-key mget on every /state and every
# watcher tick. Reads and expiry paints use this cache; Redis is refreshed
# periodically and after mutations.
_live_cache: dict[int, dict[str, SeatLive]] = {}
_stats_cache: dict[int, dict[str, int]] = {}
_double_booking_cache: dict[int, int] = {}
_cache_locks: dict[int, asyncio.Lock] = {}
_watcher_ticks: dict[int, int] = {}


def _cache_lock(showing_id: int) -> asyncio.Lock:
    lock = _cache_locks.get(showing_id)
    if lock is None:
        lock = asyncio.Lock()
        _cache_locks[showing_id] = lock
    return lock


def _expire_holds_in_cache(showing_id: int) -> None:
    """Open holds whose local expiry has passed, without asking Redis.

    The Redis TTL already deleted the key. This only keeps the cached map
    and the UI in sync so free-tier hosting is not mget-polled to death.
    """
    cache = _live_cache.get(showing_id)
    if not cache:
        return
    now = _now()
    for seat_id, seat in list(cache.items()):
        if (
            seat.status == HELD
            and seat.expires_at is not None
            and seat.expires_at <= now
        ):
            cache[seat_id] = SeatLive(seat_id, AVAILABLE, "", None)


def _apply_result_to_cache(showing_id: int, result: ClaimResult) -> None:
    if result.status == "limit":
        return
    cache = _live_cache.setdefault(showing_id, {})
    cache[result.seat_id] = SeatLive(
        result.seat_id,
        result.status,
        result.holder_id or "",
        result.expires_at,
    )


async def _refresh_live_cache(showing_id: int) -> dict[str, SeatLive]:
    """Reload the showing from Redis into process memory."""
    ids = await seat_ids_for(showing_id)
    live = await read_seat_map(app.state.redis, showing_id, ids)
    _live_cache[showing_id] = live
    try:
        _stats_cache[showing_id] = await read_stats(app.state.redis, showing_id)
    except Exception:
        _stats_cache.setdefault(
            showing_id, {"claims": 0, "collisions": 0, "bookings": 0}
        )
    return live


async def _get_live_map(showing_id: int) -> dict[str, SeatLive]:
    """Prefer the in-process map; load Redis only on a cold cache."""
    async with _cache_lock(showing_id):
        if showing_id in _live_cache:
            _expire_holds_in_cache(showing_id)
            return _live_cache[showing_id]
        try:
            return await _refresh_live_cache(showing_id)
        except Exception:
            if showing_id in _live_cache:
                _expire_holds_in_cache(showing_id)
                return _live_cache[showing_id]
            raise


def remember_holder(token: str, name: str) -> str:
    """Map a token's public id to a display name for the activity feed."""
    public = public_holder(token)
    cleaned = (name or "").strip()
    if cleaned:
        _holder_names[public] = cleaned
    return public


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = redis_from_env()
    try:
        yield
    finally:
        for tasks in _sim_tasks.values():
            for task in tasks:
                task.cancel()
        for tasks in _sim_tasks.values():
            for task in tasks:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await app.state.redis.close()


app = FastAPI(title="Velora API", lifespan=lifespan)

_cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class HolderRequest(BaseModel):
    holder_id: str = Field(min_length=8, max_length=120)
    holder_name: str = Field(min_length=1, max_length=80)


class RaceRequest(HolderRequest):
    contenders: int = Field(default=12, ge=2, le=12)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


async def ensure_house_runtime(showing_id: int) -> None:
    """Start the expiry watcher and simulated patrons for this showing.

    WHY a lock: GET /state and the live socket can land in the same instant
    when a visitor opens a tour. Without the lock both would spawn a full
    patron pool and the house would run twice as hot.
    """
    async with _sim_lock:
        if showing_id in _sim_tasks:
            return
        # Warm the cache once so the first /state does not race the watcher.
        try:
            await _refresh_live_cache(showing_id)
        except Exception:
            pass
        tasks = [asyncio.create_task(_watch_expiries(showing_id))]
        # Default off: free-tier Redis cannot absorb continuous claim traffic.
        if os.environ.get("SIMULATE_PATRONS", "0") != "0":
            tasks.extend(
                asyncio.create_task(_simulate_patron(showing_id, index))
                for index in range(len(SIM_PATRONS))
            )
        _sim_tasks[showing_id] = tasks


def _load_seat_ids(showing_id: int) -> list[str]:
    session = get_session()
    try:
        return list(
            session.scalars(
                select(Seat.seat_id)
                .where(Seat.showing_id == showing_id)
                .order_by(Seat.row_index, Seat.col_index)
            )
        )
    finally:
        session.close()


async def seat_ids_for(showing_id: int) -> list[str]:
    if showing_id not in _seat_ids:
        _seat_ids[showing_id] = await asyncio.to_thread(_load_seat_ids, showing_id)
    return _seat_ids[showing_id]


def _record_booking(
    showing_id: int, seat_id: str, holder_public: str, holder_name: str
) -> None:
    session = get_session()
    try:
        session.add(
            Booking(
                showing_id=showing_id,
                seat_id=seat_id,
                holder_id=holder_public,
                holder_name=holder_name,
            )
        )
        session.execute(
            update(Seat)
            .where(Seat.showing_id == showing_id, Seat.seat_id == seat_id)
            .values(status=BOOKED)
        )
        session.commit()
    except IntegrityError:
        # Redis already decided this seat belongs to this holder, so
        # a duplicate here means the same booking was submitted twice, not
        # that two patrons won. Treat it as settled and keep the durable
        # row that already exists.
        session.rollback()
    finally:
        session.close()


def _clear_booking(showing_id: int, seat_id: str) -> None:
    """Drop the durable booking after Redis has already opened the seat.

    Redis is the live authority. This runs only after a successful
    release EVAL, so a missing row here is a hold that was never booked,
    not a lost race. We still mark the Postgres seat available so the
    durable outcome cannot stay 'booked' after the live map has opened.
    """
    session = get_session()
    try:
        session.execute(
            delete(Booking).where(
                Booking.showing_id == showing_id, Booking.seat_id == seat_id
            )
        )
        session.execute(
            update(Seat)
            .where(Seat.showing_id == showing_id, Seat.seat_id == seat_id)
            .values(status=AVAILABLE)
        )
        session.commit()
    finally:
        session.close()


def _reset_house_postgres(showing_id: int) -> None:
    session = get_session()
    try:
        session.execute(delete(Booking).where(Booking.showing_id == showing_id))
        session.execute(
            update(Seat)
            .where(Seat.showing_id == showing_id)
            .values(status=AVAILABLE)
        )
        session.commit()
    finally:
        session.close()


def _double_booking_count(showing_id: int) -> int:
    """Seats sold more than once. Measured, not asserted.

    Counts booking rows minus the distinct seats they cover, so a genuine
    double sale would show up as a positive number instead of being
    hidden behind a hardcoded zero.
    """
    session = get_session()
    try:
        total = session.scalar(
            select(func.count())
            .select_from(Booking)
            .where(Booking.showing_id == showing_id)
        )
        distinct = session.scalar(
            select(func.count(func.distinct(Booking.seat_id))).where(
                Booking.showing_id == showing_id
            )
        )
        return int(total or 0) - int(distinct or 0)
    finally:
        session.close()


def _seat_payload(seat, your_public: str | None) -> dict:
    return {
        "seat_id": seat.seat_id,
        "status": seat.status,
        "holder": seat.holder_id or None,
        "mine": bool(your_public) and seat.holder_id == your_public,
        "expires_at": seat.expires_at.isoformat() if seat.expires_at else None,
    }


def _event_for(seat, previous_status: str | None) -> dict | None:
    """Describe one seat change the way a person would say it."""
    if seat.status == HELD:
        kind = "held"
    elif seat.status == BOOKED:
        kind = "booked"
    elif previous_status in (HELD, BOOKED):
        kind = "opened"
    else:
        return None
    return {
        "seat_id": seat.seat_id,
        "kind": kind,
        "holder": seat.holder_id or None,
        # Unknown holders stay anonymous rather than being invented.
        "holder_name": _holder_names.get(seat.holder_id) if seat.holder_id else None,
        "at": _now().isoformat(),
    }


async def _publish_locked(showing_id: int) -> None:
    """Broadcast seats whose status changed since the last publish.

    Called with the showing's lock held so publish order matches the
    order the EVALs actually resolved in. Uses the in-process cache so a
    free-tier deploy is not doing a 96-key mget on every tick.
    """
    ids = await seat_ids_for(showing_id)
    live = _live_cache.get(showing_id)
    if live is None:
        live = await _refresh_live_cache(showing_id)
    else:
        _expire_holds_in_cache(showing_id)
        live = _live_cache[showing_id]

    signatures = {
        seat_id: (seat.status, seat.holder_id, seat.expires_at)
        for seat_id, seat in live.items()
    }
    # Ensure every known seat appears, even if the cache was partial.
    for seat_id in ids:
        signatures.setdefault(seat_id, (AVAILABLE, "", None))

    previous = _snapshots.get(showing_id)
    _snapshots[showing_id] = signatures

    if previous is None:
        # First snapshot of the process: nothing to diff against, and every
        # client gets a full map from GET /state anyway.
        return

    changed = [
        seat_id
        for seat_id, signature in signatures.items()
        if previous.get(seat_id) != signature
    ]
    if not changed:
        return

    changes = []
    for seat_id in changed:
        seat = live.get(seat_id) or SeatLive(seat_id, AVAILABLE, "", None)
        changes.append(_seat_payload(seat, None))

    feed = _events.setdefault(showing_id, deque(maxlen=14))
    fresh_events = []
    for seat_id in changed:
        previous_status = previous.get(seat_id, (None,))[0]
        seat = live.get(seat_id) or SeatLive(seat_id, AVAILABLE, "", None)
        event = _event_for(seat, previous_status)
        if event:
            fresh_events.append(event)
            feed.appendleft(event)

    _versions[showing_id] = _versions.get(showing_id, 0) + 1
    await hub.broadcast(
        showing_id,
        {
            "version": _versions[showing_id],
            "changes": changes,
            "events": fresh_events,
        },
    )


async def _publish(showing_id: int) -> None:
    async with hub.lock_for(showing_id):
        await _publish_locked(showing_id)


async def _watch_expiries(showing_id: int) -> None:
    """Publish seats that went back to available on their own.

    A lapsed hold produces no request and no EVAL, so nothing else would
    notice it. Correctness never depends on this loop — the TTL already
    freed the seat — it exists so the map repaints without a click.
    """
    while True:
        try:
            await asyncio.sleep(3.0)
            tick = _watcher_ticks.get(showing_id, 0) + 1
            _watcher_ticks[showing_id] = tick
            # Occasional Redis resync heals drift without paying for it
            # on every tick.
            if tick % 10 == 0:
                try:
                    await _refresh_live_cache(showing_id)
                    _double_booking_cache[showing_id] = await asyncio.to_thread(
                        _double_booking_count, showing_id
                    )
                except Exception:
                    pass
            await _publish(showing_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(2.0)


async def _simulate_patron(showing_id: int, index: int) -> None:
    """One simulated patron browsing and claiming seats, forever.

    Each runs as its own task and goes through the same claim path as a
    real click, so simulated traffic contends for seats on identical
    terms — including losing races to real visitors.
    """
    name = SIM_PATRONS[index]
    token = f"velora-sim-{showing_id}-{index}-{name}"
    holder_public = remember_holder(token, name)
    # Stagger the first move so all patrons do not act in lockstep, but
    # start within a second of the visitor opening the tour.
    await asyncio.sleep(random.uniform(0.15, 0.6) + 0.2 * index)

    while True:
        try:
            ids = await seat_ids_for(showing_id)
            try:
                live = await _get_live_map(showing_id)
            except Exception:
                await asyncio.sleep(random.uniform(SIM_MIN_WAIT, SIM_MAX_WAIT))
                continue

            open_seats = [s.seat_id for s in live.values() if s.is_open]
            if not open_seats:
                await asyncio.sleep(random.uniform(SIM_MIN_WAIT, SIM_MAX_WAIT))
                continue
            seat_id = random.choice(open_seats)

            claim = await claim_seat(
                app.state.redis, showing_id, seat_id, token, extra_seat_ids=ids
            )
            _apply_result_to_cache(showing_id, claim)
            await _publish(showing_id)
            if not claim.ok:
                # Lost the seat to someone else between the read and the
                # claim. That is the engine working; just move on.
                await asyncio.sleep(random.uniform(SIM_MIN_WAIT, SIM_MAX_WAIT))
                continue

            # Sit on the hold like a real patron deciding, then either
            # confirm or walk away and let the TTL release it.
            await asyncio.sleep(random.uniform(4.0, 14.0))
            # Re-read: the count from before the pause is stale, and
            # several patrons deciding at once would sail past the cap.
            fresh = await _get_live_map(showing_id)
            booked_count = sum(1 for s in fresh.values() if s.status == BOOKED)
            if booked_count < SIM_BOOKED_CAP and random.random() < 0.45:
                booking = await book_seat(
                    app.state.redis, showing_id, seat_id, token
                )
                _apply_result_to_cache(showing_id, booking)
                if booking.ok:
                    await asyncio.to_thread(
                        _record_booking, showing_id, seat_id, holder_public, name
                    )
                await _publish(showing_id)

            await asyncio.sleep(random.uniform(SIM_MIN_WAIT, SIM_MAX_WAIT))
        except asyncio.CancelledError:
            raise
        except Exception:
            # A demo feed must never take the API down, and must not spin
            # hot on a persistent failure either.
            await asyncio.sleep(SIM_MAX_WAIT)


def _get_showing(session, showing_id: int) -> Showing:
    showing = session.get(Showing, showing_id)
    if showing is None:
        raise HTTPException(status_code=404, detail="showing not found")
    return showing


def _showing_out(session, showing: Showing) -> dict:
    venue = session.get(Venue, showing.venue_id)
    return {
        "id": showing.id,
        "title": showing.title,
        "auditorium": showing.auditorium,
        "starts_at": _as_utc(showing.starts_at).isoformat(),
        "row_count": showing.row_count,
        "col_count": showing.col_count,
        "venue_name": venue.name if venue else "",
        "venue_city": venue.city if venue else "",
    }


def _claim_out(result: ClaimResult, your_public: str) -> dict:
    return {
        "ok": result.ok,
        "seat_id": result.seat_id,
        "status": result.status,
        "holder": result.holder_id or None,
        "mine": result.holder_id == your_public,
        "expires_at": result.expires_at.isoformat() if result.expires_at else None,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/showings")
def list_showings() -> list[dict]:
    session = get_session()
    try:
        showings = session.scalars(select(Showing).order_by(Showing.id)).all()
        return [_showing_out(session, showing) for showing in showings]
    finally:
        session.close()


@app.get("/showings/{showing_id}/state")
async def showing_state(
    showing_id: int,
    holder_id: str | None = Query(default=None),
) -> dict:
    session = get_session()
    try:
        showing = _get_showing(session, showing_id)
        rows = session.execute(
            select(Seat.seat_id, Seat.row_label, Seat.row_index, Seat.col_index)
            .where(Seat.showing_id == showing_id)
            .order_by(Seat.row_index, Seat.col_index)
        ).all()
        showing_out = _showing_out(session, showing)
    finally:
        session.close()

    await ensure_house_runtime(showing_id)

    your_public = public_holder(holder_id) if holder_id else None
    try:
        live = await _get_live_map(showing_id)
    except Exception as exc:
        # Surface Redis/Upstash failures instead of a bare 500 with no body.
        raise HTTPException(
            status_code=503,
            detail=f"seat map unavailable ({type(exc).__name__})",
        ) from exc
    stats = dict(
        _stats_cache.get(
            showing_id, {"claims": 0, "collisions": 0, "bookings": 0}
        )
    )
    stats["double_bookings"] = _double_booking_cache.get(showing_id, 0)

    grid: dict[str, dict] = {}
    for row in rows:
        seat = live.get(row.seat_id)
        entry = _seat_payload(seat, your_public) if seat else None
        bucket = grid.setdefault(
            row.row_label, {"label": row.row_label, "seats": []}
        )
        bucket["seats"].append(
            {
                **(entry or {"seat_id": row.seat_id, "status": AVAILABLE}),
                "col": row.col_index,
            }
        )

    seats = list(live.values())
    return {
        "showing": showing_out,
        "rows": list(grid.values()),
        "counts": {
            "available": sum(1 for s in seats if s.status == AVAILABLE),
            "held": sum(1 for s in seats if s.status == HELD),
            "booked": sum(1 for s in seats if s.status == BOOKED),
        },
        "your_holds": [
            s.seat_id
            for s in seats
            if your_public and s.status == HELD and s.holder_id == your_public
        ],
        "your_public_id": your_public,
        "hold_seconds": HOLD_TTL_SECONDS,
        "max_owned": MAX_OWNED_SEATS,
        "version": _versions.get(showing_id, 0),
        "stats": stats,
        "events": list(_events.get(showing_id, [])),
    }


async def _seat_action(
    action: str, showing_id: int, seat_id: str, body: HolderRequest
) -> dict:
    ids = await seat_ids_for(showing_id)
    if seat_id not in ids:
        raise HTTPException(status_code=404, detail="seat not found")

    your_public = remember_holder(body.holder_id, body.holder_name)
    if action == "claim":
        result = await claim_seat(
            app.state.redis,
            showing_id,
            seat_id,
            body.holder_id,
            extra_seat_ids=ids,
        )
    elif action == "book":
        result = await book_seat(
            app.state.redis, showing_id, seat_id, body.holder_id
        )
    else:
        result = await release_seat(
            app.state.redis, showing_id, seat_id, body.holder_id
        )

    _apply_result_to_cache(showing_id, result)
    if result.ok:
        # Keep scoreboard tallies roughly in step without an extra Redis read.
        tallies = _stats_cache.setdefault(
            showing_id, {"claims": 0, "collisions": 0, "bookings": 0}
        )
        if action == "claim":
            tallies["claims"] = int(tallies.get("claims") or 0) + 1
        elif action == "book":
            tallies["bookings"] = int(tallies.get("bookings") or 0) + 1
    elif action == "claim" and result.status != "limit":
        tallies = _stats_cache.setdefault(
            showing_id, {"claims": 0, "collisions": 0, "bookings": 0}
        )
        tallies["claims"] = int(tallies.get("claims") or 0) + 1
        tallies["collisions"] = int(tallies.get("collisions") or 0) + 1

    if action == "book" and result.ok:
        await asyncio.to_thread(
            _record_booking, showing_id, seat_id, your_public, body.holder_name
        )
    if action == "release" and result.ok:
        await asyncio.to_thread(_clear_booking, showing_id, seat_id)

    await _publish(showing_id)
    return _claim_out(result, your_public)


@app.post("/showings/{showing_id}/reset")
async def reset_showing(showing_id: int) -> dict:
    """Open every seat and zero the tallies. Used when the demo page loads."""
    ids = await seat_ids_for(showing_id)
    if not ids:
        raise HTTPException(status_code=404, detail="showing not found")

    await asyncio.to_thread(_reset_house_postgres, showing_id)
    keys = [seat_key(showing_id, seat_id) for seat_id in ids]
    keys.append(stats_key(showing_id))
    await app.state.redis.delete(*keys)

    _events.pop(showing_id, None)
    live = {
        seat_id: SeatLive(seat_id, AVAILABLE, "", None) for seat_id in ids
    }
    _live_cache[showing_id] = live
    _stats_cache[showing_id] = {"claims": 0, "collisions": 0, "bookings": 0}
    _double_booking_cache[showing_id] = 0
    _snapshots[showing_id] = {
        seat_id: (seat.status, seat.holder_id, seat.expires_at)
        for seat_id, seat in live.items()
    }
    _versions[showing_id] = _versions.get(showing_id, 0) + 1
    await hub.broadcast(
        showing_id,
        {
            "version": _versions[showing_id],
            "changes": [_seat_payload(live[seat_id], None) for seat_id in ids],
            "events": [],
        },
    )
    return {"ok": True, "seats": len(ids)}


@app.post("/showings/{showing_id}/seats/{seat_id}/claim")
async def claim(showing_id: int, seat_id: str, body: HolderRequest) -> dict:
    return await _seat_action("claim", showing_id, seat_id, body)


@app.post("/showings/{showing_id}/seats/{seat_id}/book")
async def book(showing_id: int, seat_id: str, body: HolderRequest) -> dict:
    return await _seat_action("book", showing_id, seat_id, body)


@app.post("/showings/{showing_id}/seats/{seat_id}/release")
async def release(showing_id: int, seat_id: str, body: HolderRequest) -> dict:
    return await _seat_action("release", showing_id, seat_id, body)


@app.post("/showings/{showing_id}/seats/{seat_id}/race")
async def race(showing_id: int, seat_id: str, body: RaceRequest) -> dict:
    """Send the caller plus N-1 others at one seat at the same instant.

    This is a demonstration of the guarantee, not a shortcut around it:
    every contender goes through the ordinary claim path. They all wait on
    one gate and are released together, so the claims genuinely overlap
    instead of queueing — which is the only way to show that exactly one
    of them can win.
    """
    ids = await seat_ids_for(showing_id)
    if seat_id not in ids:
        raise HTTPException(status_code=404, detail="seat not found")

    live = await _get_live_map(showing_id)
    current = live.get(seat_id)
    if current is None or current.status != AVAILABLE:
        raise HTTPException(
            status_code=409, detail="seat is not open to race"
        )

    you_name = body.holder_name.strip() or "You"
    entrants: list[tuple[str, str, bool]] = [(you_name, body.holder_id, True)]
    for name in random.sample(DEMO_RACERS, body.contenders - 1):
        # A fresh token each run, so a repeated race is a real contest
        # rather than the same holder re-claiming their own seat.
        entrants.append((name, f"race-{uuid.uuid4().hex}", False))
    # Shuffle so "you" is not always first. asyncio wakes Event waiters
    # in wait order, and the first task to call Redis over HTTP gets a
    # head start — which is why the visitor used to win almost every time.
    random.shuffle(entrants)
    for name, token, _ in entrants:
        remember_holder(token, name)

    gate = asyncio.Event()

    async def run(token: str) -> ClaimResult:
        await gate.wait()
        return await claim_seat(
            app.state.redis, showing_id, seat_id, token, extra_seat_ids=ids
        )

    tasks = [asyncio.create_task(run(token)) for _, token, _ in entrants]
    await asyncio.sleep(0.05)  # let every task reach the gate
    gate.set()
    outcomes = await asyncio.gather(*tasks)
    for outcome in outcomes:
        _apply_result_to_cache(showing_id, outcome)
    await _publish(showing_id)

    results = [
        {
            "name": name,
            "you": is_you,
            "ok": outcome.ok,
            "status": outcome.status,
        }
        for (name, _, is_you), outcome in zip(entrants, outcomes)
    ]
    winner = next((r for r in results if r["ok"]), None)
    # Display order is independent of who ran first. You always sit at
    # the top of the list; the shuffle above is only for fairness.
    results.sort(key=lambda row: (not row["you"], row["name"]))
    return {
        "seat_id": seat_id,
        "contenders": len(results),
        "results": results,
        "winner": winner,
        "rejected": sum(1 for r in results if not r["ok"]),
        # No winner means the seat was already taken before the race began.
        "seat_was_taken": winner is None,
    }


@app.websocket("/showings/{showing_id}/live")
async def showing_live(ws: WebSocket, showing_id: int):
    session = get_session()
    try:
        exists = session.get(Showing, showing_id) is not None
    finally:
        session.close()
    if not exists:
        await ws.close(code=4404)
        return

    await ensure_house_runtime(showing_id)

    await ws.accept()
    hub.subscribe(showing_id, ws)
    try:
        while True:
            # Client messages are ignored; this loop only detects disconnect.
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.unsubscribe(showing_id, ws)
    except Exception:
        hub.unsubscribe(showing_id, ws)
