# Velora

Real-time seat maps for pop tours in 96-seat rooms. Click a seat, hold it for
90 seconds, book it.

The interesting part is contention: when two fans click the same seat in the
same instant, exactly one gets it, and the other is told immediately that the
seat was just taken.

## Local run

### Backend (FastAPI)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# backend/.env: DATABASE_URL, UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN
python seed.py
uvicorn main:app --reload --port 8000
```

Health check: [http://localhost:8000/health](http://localhost:8000/health)

Simulated patrons start when you open a tour, not at API boot. They claim,
hold, and book through the same endpoints a real click uses. Set
`SIMULATE_PATRONS=0` in `backend/.env` to turn them off.

Simulated patrons book seats permanently. Empty one house from the showing
page, or every house from the homepage **Reset all bookings** control.

```bash
python reset_showing.py --showing-id 1
```

### Frontend (Next.js)

```bash
cd frontend
npm install
npm run dev
```

App: [http://localhost:3000](http://localhost:3000)

If the UI is not on localhost:3000, set `CORS_ORIGINS` on the API (comma-separated).

## Deploy

The backend ships as a container (`backend/Dockerfile`) and the repo carries a
Render blueprint (`render.yaml`) that builds it, health-checks `/health`, and
pins the service to one instance. The frontend goes to Vercel.

The backend host has to support WebSockets. That is what ruled out AWS App
Runner, which is HTTP-only; without a socket the client falls back to
resyncing through `GET /state` on every failed reconnect, which works but
polls hard and leaves the live feed permanently off.

Set on Render: `DATABASE_URL`, `UPSTASH_REDIS_REST_URL`,
`UPSTASH_REDIS_REST_TOKEN`, and `CORS_ORIGINS` (must list the Vercel origin,
or the browser blocks every call). Render provides `PORT` itself.

Set on Vercel: `NEXT_PUBLIC_API_URL` pointing at the Render URL. The socket
URL is derived from it, so `https://` becomes `wss://` on its own; override
with `NEXT_PUBLIC_WS_URL` only if the API and socket live apart.

Seed the database once before the first visit:

```bash
cd backend && python seed.py
```

## Showing it to someone non-technical

The guarantee is invisible until two people want one seat, and that almost
never happens by chance. So the page stages it.

The "Simulate 12 people, 1 seat" button picks an open seat and fires twelve
genuinely simultaneous claims, one of them yours, then reports who won and
that nobody ended up with a double-booked seat. It goes through the ordinary
claim path rather than a shortcut around it, which is the whole point.

Alongside that, a scoreboard tracks seat clicks, ties, and seats accidentally
sold twice. That last number is measured as booking rows minus distinct seats
booked, not hardcoded, so a real double sale would actually show up. A feed
narrates changes in plain language ("D4 held by Maya Chen", "G7 opened back
up"), and a status line follows your own outcomes, including why a seat you
were holding opened again.

## Verifying the atomic claim

Two simultaneous claims on one seat, through the live HTTP API:

```bash
cd backend && source .venv/bin/activate
python race_claim.py --seat H12            # two patrons
python race_claim.py --seat H11 --patrons 12
```

Every request waits on a shared gate and is released together, so the claims
genuinely overlap. Exactly one prints `WON`; the rest report the seat as
`held` and all of them name the same holder.

The engine scenarios run against Redis directly, no server needed:

```bash
python test_seat_claim.py
```

That covers 12 simultaneous claims on one seat, a non-holder being refused a
booking, the rightful holder promoting a hold to booked, and a lapsed hold
reverting to available with no sweeper involved.

## How the claim stays race-free

A claim is a single `SET seat_key "held|holder|expires_at" EX 90 NX` inside a
Lua script. There is no read-then-write window to lose: the command that
decides the winner is the command that writes the hold. Concurrent claimants
are serialized by Redis, and losers observe the winner's write rather than a
stale snapshot.

Booking is the same idea in reverse: the script requires the seat to be
`held` by exactly this holder, then rewrites it as `booked` without a TTL in
the same execution, so an expiry cannot slip between the check and the write.

A hold's 90 seconds is a real Redis key TTL, so an abandoned seat frees
itself. Nothing has to sweep for correctness. A background watcher re-reads
the map every 1.2s only so the UI repaints when a hold lapses with no request
behind it.

Holder identities on the wire are a derivation of the patron's private token,
never the token itself. Possession of the token is what proves ownership of a
hold, so publishing it would let anyone watching the feed book someone else's
seat.

## Next steps

Out of scope for the current MVP:

- Payment / checkout
- Seat pricing tiers, accessible-seat rules, companion seating
- Kafka-backed event sourcing and audit logging, where every claim and
  booking publishes to a topic and a consumer persists to Postgres. Today
  bookings are written straight to the `bookings` table after Redis EVAL.
- Redis pub/sub fan-out so a second backend instance shares WebSocket
  subscribers (today the hub is per-process; clients self-heal by resyncing
  through `GET /state` on reconnect)
- Multi-showing box-office dashboard
- Prometheus/Grafana observability
- Formal load testing (Locust/k6)
- CI/CD pipeline
