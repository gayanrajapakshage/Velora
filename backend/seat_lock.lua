-- Atomic seat claim / booking (SPEC.md core algorithm).
--
-- WHY EVAL: Redis runs this script to completion before any other command
-- touches KEYS[1]. Two patrons clicking the same seat in the same instant
-- are serialized here, so the "is it free?" check and the "it's mine now"
-- write cannot be split by the other claim. REST is transport only and adds
-- no race window.
--
-- The claim goes further than a check-then-write: it is a single SET with
-- NX, so even outside a script the second writer could not win. Inside
-- EVAL that makes the claim doubly safe, and it means the reject path never
-- has to undo a partial write.
--
-- A hold's lifetime is a real Redis key TTL, not a timestamp someone has
-- to sweep. When it lapses the key is gone and the seat is available again
-- by definition, with no reaper in the correctness path.
--
-- Seat value format: "<status>|<holder_id>|<expires_at_ms>"
--   held|h_9fc3|1757300000000     (carries a TTL)
--   booked|h_9fc3|0               (no TTL)
--   absent key                    (available)
--
-- KEYS[1] = seat:{showing_id}:<seat_id>
-- KEYS[2] = seat:{showing_id}:stats hash
-- KEYS[3..] = remaining seats in the showing (claim only; used to cap
--             how many seats one holder can own at once)
-- ARGV[1] = action: "claim" | "book" | "release"
-- ARGV[2] = holder_id
-- ARGV[3] = now_ms
-- ARGV[4] = hold_ttl_seconds
-- ARGV[5] = max seats one holder may have on hold at once (claim only)
--
-- Returns {ok, status, holder_id, expires_at_ms}

local key = KEYS[1]
local stats_key = KEYS[2]
local action = ARGV[1]
local holder = ARGV[2]
local now_ms = tonumber(ARGV[3])
local ttl_seconds = tonumber(ARGV[4])
local max_owned = tonumber(ARGV[5]) or 0

-- Counted in the same execution that resolves the claim, so the tallies
-- cannot drift from what actually happened. A "collision" is a claim that
-- was refused because another patron already had the seat — the exact
-- event this whole design exists to get right.
local function tally(field)
  redis.call("HINCRBY", stats_key, field, 1)
end

-- One definition of the value format, shared by both actions, so a claim
-- and a booking can never disagree about how to read a seat.
local function parse(raw)
  local first = string.find(raw, "|", 1, true)
  local second = string.find(raw, "|", first + 1, true)
  return
    string.sub(raw, 1, first - 1),
    string.sub(raw, first + 1, second - 1),
    tonumber(string.sub(raw, second + 1))
end

if action == "claim" then
  -- Cap is checked in this same EVAL as the SET NX, so two rapid clicks
  -- cannot both see "3 seats" and both write a fourth and fifth.
  if max_owned > 0 and #KEYS > 2 then
    local target_raw = redis.call("GET", key)
    local already_mine = false
    if target_raw then
      local _, owner = parse(target_raw)
      already_mine = (owner == holder)
    end
    if not already_mine then
      local mine = 0
      for i = 1, #KEYS do
        if i ~= 2 then
          local raw = redis.call("GET", KEYS[i])
          if raw then
            local status, owner = parse(raw)
            if owner == holder and status == "held" then
              mine = mine + 1
            end
          end
        end
      end
      if mine >= max_owned then
        return {0, "limit", holder, 0}
      end
    end
  end

  local expires_at = now_ms + ttl_seconds * 1000
  local value = "held|" .. holder .. "|" .. expires_at

  -- NX is the whole claim. If it returns a value the seat was free and is
  -- now ours; if it returns false someone already holds or booked it and
  -- we have written nothing.
  local placed = redis.call("SET", key, value, "EX", ttl_seconds, "NX")
  if placed then
    tally("claims")
    return {1, "held", holder, expires_at}
  end

  local raw = redis.call("GET", key)
  if not raw then
    -- The hold lapsed between the refused SET and this GET. Nothing is
    -- lost: report the seat as free and let the caller click again rather
    -- than guessing at a state we no longer observed.
    return {0, "available", "", 0}
  end

  local status, owner, owner_expires = parse(raw)
  if status == "held" and owner == holder then
    -- Same patron re-clicking their own held seat: extend their window
    -- instead of reporting a conflict against themselves.
    local extended = now_ms + ttl_seconds * 1000
    redis.call("SET", key, "held|" .. holder .. "|" .. extended, "EX", ttl_seconds)
    return {1, "held", holder, extended}
  end

  -- Lost the race, or the seat is already sold. Report the truth. This is
  -- the collision path: a real patron was refused because someone else
  -- held the seat first.
  tally("claims")
  tally("collisions")
  return {0, status, owner, owner_expires}
end

if action == "book" then
  local raw = redis.call("GET", key)
  if not raw then
    -- The hold expired before confirmation. The seat is genuinely open
    -- again, so booking must fail rather than resurrect a dead hold.
    return {0, "available", "", 0}
  end

  local status, owner, owner_expires = parse(raw)
  if status == "booked" then
    return {0, "booked", owner, 0}
  end
  if owner ~= holder then
    return {0, status, owner, owner_expires}
  end

  -- Held by this patron: promote to booked and drop the TTL in the same
  -- execution. WHY here and not two commands: between a PERSIST and a
  -- rewrite the hold could expire and another patron could claim the
  -- seat, which would book a seat out from under its new holder.
  redis.call("SET", key, "booked|" .. holder .. "|0")
  tally("bookings")
  return {1, "booked", holder, 0}
end

if action == "release" then
  local raw = redis.call("GET", key)
  if not raw then
    -- Already open. Treat as success so a double-click cannot invent a
    -- conflict after the seat is gone.
    return {1, "available", "", 0}
  end

  local status, owner, owner_expires = parse(raw)
  if owner ~= holder then
    return {0, status, owner, owner_expires}
  end

  -- WHY DEL inside this EVAL: ownership was just confirmed against the
  -- same key. A GET then a later DEL would let another claim land in
  -- between, and we would delete the new holder's seat. The check and
  -- the delete must not be split.
  redis.call("DEL", key)
  return {1, "available", "", 0}
end

return redis.error_reply("unknown action: " .. tostring(action))
