from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from upstash_redis.asyncio import Redis

load_dotenv(Path(__file__).with_name(".env"), override=True)

LUA_SCRIPT = Path(__file__).with_name("seat_lock.lua").read_text()

# How long a claimed seat stays yours before it opens back up.
HOLD_TTL_SECONDS = 90
# One patron may hold this many seats at once. Booked seats do not count.
MAX_OWNED_SEATS = 4

AVAILABLE = "available"
HELD = "held"
BOOKED = "booked"


@dataclass(frozen=True)
class SeatLive:
    seat_id: str
    status: str
    holder_id: str
    expires_at: datetime | None

    @property
    def is_open(self) -> bool:
        return self.status == AVAILABLE


@dataclass(frozen=True)
class ClaimResult:
    ok: bool
    seat_id: str
    status: str
    holder_id: str
    expires_at: datetime | None


def now_ms() -> int:
    return int(time.time() * 1000)


def from_ms(ms: int | str) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)


# The hash tag pins every seat in a showing to one slot, so a future
# clustered Redis still lets one MGET read the whole map.
def seat_key(showing_id: int | str, seat_id: str) -> str:
    return f"seat:{{{showing_id}}}:{seat_id}"


def stats_key(showing_id: int | str) -> str:
    return f"seat:{{{showing_id}}}:stats"


def redis_from_env() -> Redis:
    return Redis.from_env()


async def _redis_call(operation, attempts: int = 4):
    """Retry brief Upstash blips instead of failing the whole seat map.

    Free-tier Redis over REST is chatty under concurrent mget/eval. A short
    backoff here is cheaper than returning 500 to every open browser tab.
    """
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return await operation()
        except Exception as exc:  # noqa: BLE001 — surface after retries
            last = exc
            await asyncio.sleep(0.2 * (attempt + 1))
    assert last is not None
    raise last


def public_holder(token: str) -> str:
    """Derive the public seat-holder identity from a patron's secret token.

    WHY this exists: possession of the raw token is the only thing that
    proves ownership of a hold, and seat holders are broadcast to every
    connected client. Storing and publishing the derivation instead means
    the feed can say who holds a seat without handing anyone the
    credential needed to book it. A patron presenting a stolen public id
    derives to something else and fails the ownership check.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _parse(raw: str | None, seat_id: str) -> SeatLive:
    """Read one seat value. An absent key means available."""
    if not raw:
        return SeatLive(seat_id, AVAILABLE, "", None)
    parts = str(raw).split("|")
    if len(parts) != 3:
        return SeatLive(seat_id, AVAILABLE, "", None)
    status, holder, expires = parts
    expires_at = from_ms(expires) if expires and expires != "0" else None
    return SeatLive(seat_id, status, holder, expires_at)


async def _run(
    redis: Redis,
    action: str,
    showing_id: int | str,
    seat_id: str,
    holder_token: str,
    extra_seat_ids: list[str] | None = None,
) -> ClaimResult:
    # Only the derivation is ever written to Redis, so the secret token
    # cannot leak through the seat map or the live feed.
    keys = [seat_key(showing_id, seat_id), stats_key(showing_id)]
    if extra_seat_ids:
        for other in extra_seat_ids:
            if other != seat_id:
                keys.append(seat_key(showing_id, other))
    raw = await redis.eval(
        LUA_SCRIPT,
        keys=keys,
        args=[
            action,
            public_holder(holder_token),
            str(now_ms()),
            str(HOLD_TTL_SECONDS),
            str(MAX_OWNED_SEATS),
        ],
    )
    if not isinstance(raw, list) or len(raw) < 4:
        raise RuntimeError(f"unexpected EVAL result: {raw!r}")
    expires = int(raw[3])
    return ClaimResult(
        ok=int(raw[0]) == 1,
        seat_id=seat_id,
        status=str(raw[1]) or AVAILABLE,
        holder_id=str(raw[2]),
        expires_at=from_ms(expires) if expires else None,
    )


async def claim_seat(
    redis: Redis,
    showing_id: int | str,
    seat_id: str,
    holder_token: str,
    extra_seat_ids: list[str] | None = None,
) -> ClaimResult:
    """Atomically hold a seat for 90 seconds, or report who has it.

    WHY this is race-safe: the claim is one SET ... NX inside one EVAL.
    Concurrent claimants are serialized by Redis, and the winner is
    decided by the same command that writes the hold, so there is no
    window where two callers can both believe the seat was free.

    The 4-hold cap is counted in that same EVAL against the other seat
    keys, so two clicks cannot both slip under the limit. Booked seats
    are not counted — only live holds.
    """
    return await _run(
        redis, "claim", showing_id, seat_id, holder_token, extra_seat_ids
    )


async def book_seat(
    redis: Redis, showing_id: int | str, seat_id: str, holder_token: str
) -> ClaimResult:
    """Atomically turn this holder's live hold into a permanent booking.

    Ownership is proved by presenting the token whose derivation matches
    the stored holder, so seeing a public holder id on the feed is not
    enough to book someone else's seat.
    """
    return await _run(redis, "book", showing_id, seat_id, holder_token)


async def release_seat(
    redis: Redis, showing_id: int | str, seat_id: str, holder_token: str
) -> ClaimResult:
    """Atomically open a seat this patron currently holds or booked.

    WHY this is race-safe: the owner check and the DEL run in one EVAL, so
    another claim cannot land between "this is mine" and "delete it" and
    have its seat removed. A non-owner is refused and the key is unchanged.
    """
    return await _run(redis, "release", showing_id, seat_id, holder_token)


async def read_stats(redis: Redis, showing_id: int | str) -> dict[str, int]:
    """Claim tallies kept by the script itself, not by the caller."""
    data = await _redis_call(lambda: redis.hgetall(stats_key(showing_id)))
    return {
        "claims": int((data or {}).get("claims") or 0),
        "collisions": int((data or {}).get("collisions") or 0),
        "bookings": int((data or {}).get("bookings") or 0),
    }


async def read_seat_map(
    redis: Redis, showing_id: int | str, seat_ids: list[str]
) -> dict[str, SeatLive]:
    """Snapshot the whole showing in one round trip.

    Expired holds simply are not there: the TTL removed the key, so this
    read cannot report a hold that has already lapsed.
    """
    if not seat_ids:
        return {}
    keys = [seat_key(showing_id, s) for s in seat_ids]
    values = await _redis_call(lambda: redis.mget(*keys))
    return {
        seat_id: _parse(value, seat_id)
        for seat_id, value in zip(seat_ids, values or [])
    }
