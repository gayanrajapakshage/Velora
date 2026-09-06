"""Fire simultaneous claims at one seat through the live HTTP API.

Unlike test_seat_claim.py (which talks to Redis directly), this goes
through the real endpoint, so it proves the whole request path is safe
under contention, not just the script.

    python race_claim.py --seat H12
    python race_claim.py --seat H11 --patrons 12
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter

import httpx


async def claim(
    client: httpx.AsyncClient,
    showing_id: int,
    seat: str,
    index: int,
    gate: asyncio.Event,
) -> dict:
    holder = f"race-patron-{index:02d}-{'x' * 12}"
    # Every task waits on the same gate, then all are released together,
    # so the claims genuinely overlap instead of queueing politely.
    await gate.wait()
    response = await client.post(
        f"/showings/{showing_id}/seats/{seat}/claim",
        json={"holder_id": holder, "holder_name": f"Racer {index}"},
    )
    response.raise_for_status()
    body = response.json()
    body["patron"] = index
    return body


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--showing-id", type=int, default=1)
    parser.add_argument("--seat", required=True, help="seat id, e.g. H12")
    parser.add_argument("--patrons", type=int, default=2)
    args = parser.parse_args()

    gate = asyncio.Event()
    async with httpx.AsyncClient(base_url=args.api, timeout=15.0) as client:
        state = (await client.get(f"/showings/{args.showing_id}/state")).json()
        seats = {
            seat["seat_id"]: seat
            for row in state["rows"]
            for seat in row["seats"]
        }
        if args.seat not in seats:
            raise SystemExit(f"seat {args.seat} is not in this showing")
        before = seats[args.seat]["status"]
        print(f"seat {args.seat} is currently {before}")
        if before != "available":
            print("Pick an available seat, or run reset_showing.py first.")
            raise SystemExit(1)

        tasks = [
            asyncio.create_task(
                claim(client, args.showing_id, args.seat, i, gate)
            )
            for i in range(args.patrons)
        ]
        await asyncio.sleep(0.4)  # let every task reach the gate
        print(f"releasing {args.patrons} simultaneous claims on {args.seat}")
        gate.set()
        results = await asyncio.gather(*tasks)

    winners = [r for r in results if r["ok"]]
    losers = [r for r in results if not r["ok"]]

    print()
    for result in sorted(results, key=lambda r: r["patron"]):
        mark = "WON " if result["ok"] else "lost"
        print(
            f"  patron {result['patron']:02d}  {mark}  "
            f"status={result['status']}  holder={result['holder']}"
        )

    print()
    print(f"winners: {len(winners)}   rejected: {len(losers)}")
    print(f"rejection statuses: {dict(Counter(r['status'] for r in losers))}")
    holders = {r["holder"] for r in results if r["holder"]}
    print(f"distinct holders reported: {len(holders)} -> {holders}")

    if len(winners) != 1:
        print("\nFAIL: expected exactly one winner")
        raise SystemExit(1)
    if len(holders) != 1:
        print("\nFAIL: results disagree about who holds the seat")
        raise SystemExit(1)
    print("\nPASS: exactly one claim won and everyone agrees who holds it")


if __name__ == "__main__":
    asyncio.run(main())
