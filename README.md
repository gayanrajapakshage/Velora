# Velora

Real-time seat maps for intimate pop tours in 96-seat rooms.

Pick a date, open the live house map, hold a seat for 90 seconds, then book it.
When two people click the same seat at the same moment, exactly one gets it —
the other is told immediately that the seat was taken. Nothing is double-booked.

## Features

- Live 8×12 seat map over WebSocket, with resync if the connection drops
- Conflict-free holds via a Redis Lua script (`SET NX` + TTL in one shot)
- 90-second holds that expire on their own (Redis key TTL)
- Book and cancel through the same atomic path
- Demo race: twelve simultaneous claims on one open seat
- Optional simulated patrons that use the real claim/book APIs
- Scoreboard for clicks, ties, and measured double sales (should stay at zero)

## Stack

| Layer | Tech |
| --- | --- |
| Frontend | Next.js, TypeScript |
| Backend | FastAPI (Python) |
| Live seats | Redis (Upstash) |
| Durable data | PostgreSQL (Neon) |

## How a seat is claimed

A hold is one Redis command inside a Lua script:

```text
SET seat_key "held|holder|expires_at" EX 90 NX
```

The command that picks the winner is the command that writes the hold, so there
is no read-then-write race. Booking checks that you still hold the seat and
promotes it to `booked` in the same script. Abandoned holds disappear when the
TTL ends — no sweeper required for correctness.

Public feeds show a derived holder id, never the private token that proves
ownership of a hold.

## Run locally

### 1. Backend

Create `backend/.env` with:

```bash
DATABASE_URL=
UPSTASH_REDIS_REST_URL=
UPSTASH_REDIS_REST_TOKEN=
SIMULATE_PATRONS=0
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python seed.py
uvicorn main:app --reload --port 8000
```

Health: [http://localhost:8000/health](http://localhost:8000/health)

Set `SIMULATE_PATRONS=1` if you want background patrons filling the house.
They book permanently — use **Reset all bookings** on the homepage, or:

```bash
python reset_showing.py --showing-id 1
```

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

App: [http://localhost:3000](http://localhost:3000)

## Try the contention demo

On a showing page, use **Simulate 12 people, 1 seat**. It fires twelve real
simultaneous claims (including yours) through the normal claim path and reports
who won.

From the API directly:

```bash
cd backend && source .venv/bin/activate
python race_claim.py --seat H12
python race_claim.py --seat H11 --patrons 12
python test_seat_claim.py
```

## Deploy

- **Frontend:** Vercel (root directory `frontend`). Set `NEXT_PUBLIC_API_URL` to
  the API origin (`https://…`). The WebSocket URL is derived automatically.
- **Backend:** Docker image in `backend/`, blueprint in `render.yaml`. Set
  `DATABASE_URL`, `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`, and
  `CORS_ORIGINS` (your Vercel origin). Seed once with `python seed.py`.
