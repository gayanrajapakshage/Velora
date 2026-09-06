# Velora — MVP spec

## What this is
Real-time seat map for pop tours in small rooms. What it has to get right is
atomic seat claiming: when two fans click the same seat in the same instant,
exactly one gets it, and live state stays correct across reconnects.

## In scope
- Venue + showing with a real seat grid (8 rows x 12 seats)
- Atomic seat claim via Redis Lua script, with a 90-second hold TTL
- Held -> booked confirmation, also atomic
- Automatic revert to available when a hold's TTL expires
- Live seat-status broadcast via WebSocket
- Reconnection resync endpoint (client always recovers true current state)
- Continuous simulated patrons, which both prove correctness and keep the
  live demo populated
- Deployed: Next.js frontend on Vercel, backend on Render, Redis on Upstash,
  Postgres on Neon

## Out of scope
Carried in the README under "Next steps":

- Payment / checkout
- Seat pricing tiers, accessible-seat rules, companion seating
- Kafka-backed event sourcing / audit logging
- Multi-showing box-office dashboard
- Prometheus/Grafana observability
- Formal load testing (Locust/k6)
- CI/CD pipeline

## Tech stack
- Backend: FastAPI (Python), Redis (Upstash), PostgreSQL (Neon)
- Frontend: Next.js + TypeScript
- Deploy: Vercel (frontend), Render (backend, via Dockerfile). The backend
  host has to carry WebSockets, which ruled out AWS App Runner.

## Data model (Postgres)
- `venues`: id, name, city
- `showings`: id, venue_id, title, auditorium, starts_at, row_count, col_count
- `seats`: id, showing_id, seat_id, row_label, row_index, col_index, status
  (`available` | `held` | `booked`), unique on (showing_id, seat_id)
- `bookings`: id, showing_id, seat_id, holder_id, holder_name, booked_at,
  unique on (showing_id, seat_id)

Redis is the live authority for seat status. Postgres records the durable
outcome: `available` and `booked`. A `held` state is deliberately never
persisted — a hold is ephemeral by definition and dies with its TTL.

## Core algorithm: atomic seat claim
A patron claims a seat:
1. One `SET seat_key "held|holder|expires_at" EX 90 NX` attempt
2. If the write landed, the patron holds the seat for 90 seconds
3. If it did not land, the seat was already held or booked — read and return
   the true current status, change nothing
4. All of this happens inside a single Redis Lua script, so the check and the
   write cannot be split by another claim

Booking confirmation:
1. Read the seat; require `held` by exactly this holder
2. Rewrite as `booked|holder` with no TTL, in the same script execution, so
   an expiry cannot land between the check and the write
3. Any other state (expired, held by someone else, already booked) rejects

Hold expiry:
- The 90-second TTL is a real Redis key TTL, so a seat returns to available
  with no sweeper required for correctness

## API endpoints
- `GET /showings` — list showings
- `GET /showings/{id}/state` — resync endpoint, returns the true seat map
  (called on WS reconnect)
- `POST /showings/{id}/seats/{seat_id}/claim` — attempt an atomic claim
- `POST /showings/{id}/seats/{seat_id}/book` — confirm a held seat
- `POST /showings/{id}/seats/{seat_id}/release` — owner-only cancel of a hold
  or booking
- `POST /showings/{id}/seats/{seat_id}/race` — N simultaneous claims on one
  open seat (demo)
- `POST /showings/{id}/reset` — open every seat and zero tallies
- `WS /showings/{id}/live` — live seat-status broadcast
