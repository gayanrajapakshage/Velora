"""Clear every hold and booking for a showing, in Redis and Postgres.

Simulated patrons book seats permanently, so a demo left running fills up.
This puts the house back to empty.

    python reset_showing.py --showing-id 1
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import delete, select, update

from db import get_session
from models import Booking, Seat
from seat_lock import redis_from_env, seat_key, stats_key


def clear_postgres(showing_id: int) -> int:
    session = get_session()
    try:
        seat_ids = list(
            session.scalars(
                select(Seat.seat_id).where(Seat.showing_id == showing_id)
            )
        )
        session.execute(delete(Booking).where(Booking.showing_id == showing_id))
        session.execute(
            update(Seat)
            .where(Seat.showing_id == showing_id)
            .values(status="available")
        )
        session.commit()
        return seat_ids
    finally:
        session.close()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--showing-id", type=int, required=True)
    args = parser.parse_args()

    seat_ids = clear_postgres(args.showing_id)
    redis = redis_from_env()
    try:
        keys = [seat_key(args.showing_id, s) for s in seat_ids]
        keys.append(stats_key(args.showing_id))
        # Same hash tag, so one DEL covers the whole showing.
        await redis.delete(*keys)
        print(f"showing {args.showing_id}: cleared {len(seat_ids)} seats")
        print("claim/collision tallies reset to zero")
    finally:
        await redis.close()


if __name__ == "__main__":
    asyncio.run(main())
