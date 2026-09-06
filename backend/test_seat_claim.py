"""Prove the claim engine is atomic under genuine contention.

  1. 12 patrons fire a claim at the SAME seat at the same moment
     → exactly one ok=true, eleven rejected with status "held"

  2. A non-holder tries to book that seat
     → rejected; the seat still belongs to the winner

  3. The rightful holder books it
     → held -> booked, TTL dropped

  4. A hold with a short TTL lapses
     → the seat reports available again with no sweeper involved

  5. Release is owner-only
     → a non-owner cannot open the seat; the holder can

Run against Redis directly (no API server needed):
    python test_seat_claim.py
"""

from __future__ import annotations

import asyncio
import sys

from seat_lock import (
    AVAILABLE,
    BOOKED,
    HELD,
    book_seat,
    claim_seat,
    read_seat_map,
    redis_from_env,
    release_seat,
    seat_key,
)

SHOWING_ID = "claim-self-test"
SEAT = "F7"
CONTENDERS = 12


def check(label: str, passed: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if passed else 'FAIL'} {label}")
    if detail:
        print(f"       {detail}")
    if not passed:
        raise SystemExit(1)


async def main() -> None:
    redis = redis_from_env()
    try:
        await redis.delete(seat_key(SHOWING_ID, SEAT))

        # 1. Twelve simultaneous claims on one seat.
        print(f"Scenario 1: {CONTENDERS} patrons claim {SEAT} simultaneously")
        results = await asyncio.gather(
            *[
                claim_seat(redis, SHOWING_ID, SEAT, f"patron-{i}")
                for i in range(CONTENDERS)
            ]
        )
        winners = [r for r in results if r.ok]
        losers = [r for r in results if not r.ok]
        print(f"  winners={len(winners)} rejected={len(losers)}")
        check("exactly one claim succeeded", len(winners) == 1)
        check(
            "every rejection reports the seat as held",
            all(r.status == HELD for r in losers),
            f"statuses={sorted({r.status for r in losers})}",
        )
        winner = winners[0]
        check(
            "all rejections name the same holder",
            all(r.holder_id == winner.holder_id for r in losers),
            f"holder={winner.holder_id}",
        )

        live = await read_seat_map(redis, SHOWING_ID, [SEAT])
        check(
            "stored seat matches the winner",
            live[SEAT].status == HELD and live[SEAT].holder_id == winner.holder_id,
            f"stored={live[SEAT].status} holder={live[SEAT].holder_id}",
        )
        print()

        # 2. A stranger cannot book someone else's hold.
        print("Scenario 2: a non-holder tries to book the held seat")
        stolen = await book_seat(redis, SHOWING_ID, SEAT, "patron-impostor")
        check("booking rejected", not stolen.ok)
        check(
            "seat still held by the winner",
            stolen.status == HELD and stolen.holder_id == winner.holder_id,
            f"status={stolen.status} holder={stolen.holder_id}",
        )
        print()

        # 3. The holder books it. Identify the winner by its token.
        print("Scenario 3: the rightful holder books the seat")
        winner_token = next(
            f"patron-{i}"
            for i in range(CONTENDERS)
            if results[i].ok
        )
        confirmed = await book_seat(redis, SHOWING_ID, SEAT, winner_token)
        check("booking accepted", confirmed.ok)
        check("status is booked", confirmed.status == BOOKED)
        ttl = await redis.pttl(seat_key(SHOWING_ID, SEAT))
        check("TTL dropped so the booking is permanent", ttl < 0, f"pttl={ttl}")

        late = await claim_seat(redis, SHOWING_ID, SEAT, "patron-latecomer")
        check(
            "a booked seat cannot be claimed",
            not late.ok and late.status == BOOKED,
        )
        print()

        # 4. Expiry needs no sweeper: the key simply stops existing.
        print("Scenario 4: a lapsed hold reverts to available on its own")
        await redis.delete(seat_key(SHOWING_ID, SEAT))
        await redis.set(
            seat_key(SHOWING_ID, SEAT), "held|patron-briefly|0", ex=1
        )
        before = await read_seat_map(redis, SHOWING_ID, [SEAT])
        check("seat is held now", before[SEAT].status == HELD)
        await asyncio.sleep(2.2)
        after = await read_seat_map(redis, SHOWING_ID, [SEAT])
        check(
            "seat is available after the TTL lapsed",
            after[SEAT].status == AVAILABLE,
            f"status={after[SEAT].status}",
        )
        reclaimed = await claim_seat(redis, SHOWING_ID, SEAT, "patron-next")
        check("the freed seat can be claimed again", reclaimed.ok)
        print()

        # 5. Only the owner can release, and a released seat can be claimed.
        print("Scenario 5: release is owner-only and opens the seat")
        stolen = await release_seat(redis, SHOWING_ID, SEAT, "patron-impostor")
        check("a non-owner cannot release", not stolen.ok)
        check("seat still held after a stolen release", stolen.status == HELD)
        opened = await release_seat(redis, SHOWING_ID, SEAT, "patron-next")
        check("the holder can release", opened.ok)
        check("released seat is available", opened.status == AVAILABLE)
        again = await claim_seat(redis, SHOWING_ID, SEAT, "patron-after")
        check("someone else can claim after release", again.ok)
        print()

        print("All scenarios passed")
    finally:
        await redis.delete(seat_key(SHOWING_ID, SEAT))
        await redis.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyError as exc:
        print(f"Missing env var {exc}. Set it in backend/.env", file=sys.stderr)
        raise SystemExit(1) from exc
