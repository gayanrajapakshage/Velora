-- Drop leftover tables from an earlier schema if they still exist.
-- Harmless on a fresh database and on every rerun after the first.
DROP TABLE IF EXISTS bids;
DROP TABLE IF EXISTS lots;
DROP TABLE IF EXISTS bidders;

CREATE TABLE IF NOT EXISTS venues (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS showings (
    id SERIAL PRIMARY KEY,
    venue_id INTEGER NOT NULL REFERENCES venues (id),
    title TEXT NOT NULL,
    auditorium TEXT NOT NULL,
    starts_at TIMESTAMPTZ NOT NULL,
    row_count INTEGER NOT NULL,
    col_count INTEGER NOT NULL
);

-- status holds the durable outcome only: 'available' or 'booked'. A 'held'
-- seat is ephemeral and lives solely in Redis under a TTL, so it is never
-- written here — a hold that outlived a process restart would be a lie.
CREATE TABLE IF NOT EXISTS seats (
    id SERIAL PRIMARY KEY,
    showing_id INTEGER NOT NULL REFERENCES showings (id),
    seat_id TEXT NOT NULL,
    row_label TEXT NOT NULL,
    row_index INTEGER NOT NULL,
    col_index INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'available'
        CHECK (status IN ('available', 'held', 'booked')),
    UNIQUE (showing_id, seat_id)
);

-- The UNIQUE constraint is a second line of defense behind the Lua
-- script. Redis decides who wins a contested seat; if a bug ever let two
-- bookings through, Postgres refuses the second INSERT rather than
-- silently selling one seat twice.
CREATE TABLE IF NOT EXISTS bookings (
    id SERIAL PRIMARY KEY,
    showing_id INTEGER NOT NULL REFERENCES showings (id),
    seat_id TEXT NOT NULL,
    holder_id TEXT NOT NULL,
    holder_name TEXT NOT NULL,
    booked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (showing_id, seat_id),
    FOREIGN KEY (showing_id, seat_id) REFERENCES seats (showing_id, seat_id)
);
