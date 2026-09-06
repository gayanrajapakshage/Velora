"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  API_URL,
  type ActivityEvent,
  type ClaimStats,
  type LiveMessage,
  type RaceResult,
  type Seat,
  type SeatRow,
  type Showing,
  WS_URL,
  bookSeat,
  claimSeat,
  fetchState,
  formatShowtime,
  holderToken,
  loadHolderName,
  raceSeat,
  releaseSeat,
  resetShowing,
  saveHolderName,
  timeAgo,
} from "../../../lib/api";
import { BrandMark } from "../../BrandMark";
import styles from "./seatmap.module.css";

const RACE_CONTENDERS = 12;
type Layout = { label: string; seats: { seat_id: string; col: number }[] };

function toLayout(rows: SeatRow[]): Layout[] {
  return rows.map((row) => ({
    label: row.label,
    seats: row.seats.map((seat) => ({ seat_id: seat.seat_id, col: seat.col })),
  }));
}

function toSeatMap(rows: SeatRow[]): Record<string, Seat> {
  const out: Record<string, Seat> = {};
  for (const row of rows) {
    for (const seat of row.seats) out[seat.seat_id] = seat;
  }
  return out;
}

function secondsLeft(expiresAt: string | null, now: number): number {
  if (!expiresAt) return 0;
  return Math.max(0, (new Date(expiresAt).getTime() - now) / 1000);
}

function eventLine(event: ActivityEvent, publicId: string | null): string {
  const who =
    event.holder && event.holder === publicId
      ? "you"
      : (event.holder_name ?? "someone");
  if (event.kind === "opened") return `${event.seat_id} opened back up`;
  if (event.kind === "booked") return `${event.seat_id} booked by ${who}`;
  return `${event.seat_id} held by ${who}`;
}

export default function SeatMapClient({ showingId }: { showingId: string }) {
  const [showing, setShowing] = useState<Showing | null>(null);
  const [layout, setLayout] = useState<Layout[]>([]);
  const [seats, setSeats] = useState<Record<string, Seat>>({});
  const [publicId, setPublicId] = useState<string | null>(null);
  const [holdSeconds, setHoldSeconds] = useState(90);
  const [maxOwned, setMaxOwned] = useState(4);
  const [name, setName] = useState("Guest");
  const [error, setError] = useState<string | null>(null);
  const [feed, setFeed] = useState<"off" | "on" | "resync">("off");
  const [now, setNow] = useState(() => Date.now());
  const [taken, setTaken] = useState<Record<string, number>>({});
  const [pending, setPending] = useState<string | null>(null);
  const [stats, setStats] = useState<ClaimStats | null>(null);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [race, setRace] = useState<RaceResult | null>(null);
  const [racing, setRacing] = useState(false);
  const [spotlight, setSpotlight] = useState<string | null>(null);
  const [strip, setStrip] = useState<{ tone: string; text: string } | null>(
    null,
  );
  const tokenRef = useRef<string | null>(null);
  const publicIdRef = useRef<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const stopRef = useRef(false);
  const genRef = useRef(0);
  const backoffRef = useRef(1000);
  const [statsBase, setStatsBase] = useState<ClaimStats | null>(null);
  const [resetting, setResetting] = useState(false);

  useEffect(() => {
    tokenRef.current = holderToken();
    setName(loadHolderName() || "Guest");
  }, []);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(timer);
  }, []);

  const sync = useCallback(async () => {
    const next = await fetchState(showingId, tokenRef.current);
    setShowing(next.showing);
    setLayout(toLayout(next.rows));
    setSeats(toSeatMap(next.rows));
    setPublicId(next.your_public_id);
    publicIdRef.current = next.your_public_id;
    setHoldSeconds(next.hold_seconds);
    setMaxOwned(next.max_owned);
    setStats(next.stats);
    // The scoreboard counts this visit, so the first sync after mount or
    // reset fixes the baseline and later syncs leave it alone.
    setStatsBase((prev) => prev ?? next.stats);
    setError(null);
  }, [showingId]);

  const connect = useCallback(async () => {
    const gen = ++genRef.current;
    setFeed("resync");
    try {
      await sync();
      backoffRef.current = 1000;
    } catch (err) {
      // Surface the failure, then back off and retry. A tight loop against
      // a flaky free-tier API makes the 500s worse.
      if (gen === genRef.current) {
        setError(err instanceof Error ? err.message : "Failed to load seat map");
        if (!stopRef.current) {
          const delay = backoffRef.current;
          backoffRef.current = Math.min(delay * 2, 15000);
          window.setTimeout(() => {
            if (gen === genRef.current && !stopRef.current) void connect();
          }, delay);
        }
      }
      return;
    }
    if (stopRef.current || gen !== genRef.current) return;

    const socket = new WebSocket(`${WS_URL}/showings/${showingId}/live`);
    wsRef.current = socket;
    socket.onopen = () => {
      if (gen === genRef.current) {
        setFeed("on");
        backoffRef.current = 1000;
      }
    };
    socket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data as string) as LiveMessage;
        if (msg?.events?.length) {
          setEvents((prev) => [...msg.events, ...prev].slice(0, 14));
        }
        if (!msg?.changes?.length) return;
        setSeats((prev) => {
          const next = { ...prev };
          for (const change of msg.changes) {
            const existing = next[change.seat_id];
            if (!existing) continue;
            next[change.seat_id] = {
              ...existing,
              status: change.status,
              holder: change.holder,
              expires_at: change.expires_at,
              mine:
                change.holder != null &&
                change.holder === publicIdRef.current,
            };
          }
          return next;
        });
      } catch {
        void sync().catch(() => undefined);
      }
    };
    socket.onclose = () => {
      if (wsRef.current !== socket) return;
      setFeed("off");
      if (!stopRef.current) {
        const delay = backoffRef.current;
        backoffRef.current = Math.min(delay * 2, 15000);
        window.setTimeout(() => {
          if (gen === genRef.current) void connect();
        }, delay);
      }
    };
  }, [showingId, sync]);

  useEffect(() => {
    stopRef.current = false;
    void connect();
    return () => {
      stopRef.current = true;
      wsRef.current?.close();
    };
  }, [connect]);

  const say = useCallback((tone: string, text: string) => {
    setStrip({ tone, text });
  }, []);

  const prevHoldsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    const current = new Set(
      Object.values(seats)
        .filter((seat) => seat.mine && seat.status === "held")
        .map((seat) => seat.seat_id),
    );
    const lost = [...prevHoldsRef.current].filter((id) => !current.has(id));
    prevHoldsRef.current = current;
    const opened = lost.filter((id) => seats[id]?.status === "available");
    if (opened.length > 0) {
      setStrip({
        tone: "lost",
        text: `Your hold on ${opened.join(", ")} ran out, so the seat opened back up for someone else.`,
      });
    }
  }, [seats]);

  const flashTaken = useCallback((seatId: string) => {
    setTaken((prev) => ({ ...prev, [seatId]: Date.now() }));
    window.setTimeout(() => {
      setTaken((prev) => {
        const next = { ...prev };
        delete next[seatId];
        return next;
      });
    }, 1800);
  }, []);

  async function onSeatClick(seat: Seat) {
    if (seat.status !== "available" || pending) return;
    const token = tokenRef.current;
    if (!token) return;
    const heldNow = Object.values(seats).filter(
      (s) =>
        s.mine && s.status === "held" && secondsLeft(s.expires_at, now) > 0,
    ).length;
    if (heldNow >= maxOwned) {
      say("lost", `You can only hold ${maxOwned} seats at a time. Book or cancel one first.`);
      return;
    }

    setPending(seat.seat_id);
    setSeats((prev) => ({
      ...prev,
      [seat.seat_id]: {
        ...prev[seat.seat_id],
        status: "held",
        mine: true,
        expires_at: new Date(Date.now() + holdSeconds * 1000).toISOString(),
      },
    }));
    try {
      const result = await claimSeat(
        showingId,
        seat.seat_id,
        token,
        name.trim() || "Guest",
      );
      const { status } = result;
      if (status === "limit") {
        // The cap is checked in the same EVAL as the SET NX, so a refused
        // claim never wrote to the seat. Undoing the optimistic hold is
        // enough; there is no server state to reconcile.
        setSeats((prev) => ({
          ...prev,
          [seat.seat_id]: {
            ...prev[seat.seat_id],
            status: "available",
            mine: false,
            holder: null,
            expires_at: null,
          },
        }));
        say("lost", `You can only hold ${maxOwned} seats at a time. Book or cancel one first.`);
        return;
      }

      setSeats((prev) => ({
        ...prev,
        [seat.seat_id]: {
          ...prev[seat.seat_id],
          status,
          holder: result.holder,
          mine: result.mine,
          expires_at: result.expires_at,
        },
      }));
      if (result.ok) {
        say("good", `${seat.seat_id} is yours for ${holdSeconds} seconds.`);
      } else {
        flashTaken(seat.seat_id);
        say(
          "lost",
          `Someone got ${seat.seat_id} a moment before you did. Nothing was double-booked.`,
        );
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Claim failed");
      void sync();
    } finally {
      setPending(null);
    }
  }

  async function onBookAll() {
    const token = tokenRef.current;
    if (!token || pending) return;
    const holdIds = Object.values(seats)
      .filter(
        (seat) =>
          seat.mine &&
          seat.status === "held" &&
          secondsLeft(seat.expires_at, Date.now()) > 0,
      )
      .map((seat) => seat.seat_id);
    if (holdIds.length === 0) return;
    const trimmed = name.trim();
    if (trimmed) saveHolderName(trimmed);
    const holderName = trimmed || "Guest";
    setPending("all");
    const booked: string[] = [];
    const failed: string[] = [];
    try {
      for (const seatId of holdIds) {
        try {
          const result = await bookSeat(showingId, seatId, token, holderName);
          setSeats((prev) => ({
            ...prev,
            [seatId]: {
              ...prev[seatId],
              status: result.status,
              holder: result.holder,
              mine: result.mine,
              expires_at: result.expires_at,
            },
          }));
          if (result.ok) booked.push(seatId);
          else failed.push(seatId);
        } catch {
          failed.push(seatId);
        }
      }
      if (booked.length > 0 && failed.length === 0) {
        say(
          "good",
          booked.length === 1
            ? `${booked[0]} is booked. It's yours.`
            : `${booked.join(", ")} are booked. They're yours.`,
        );
      } else if (booked.length > 0) {
        say(
          "lost",
          `Booked ${booked.join(", ")}. ${failed.join(", ")} could not be booked.`,
        );
      } else {
        say("lost", "None of the holds could be booked.");
      }
    } finally {
      setPending(null);
    }
  }

  async function onRelease(seatId: string) {
    const token = tokenRef.current;
    if (!token || pending) return;
    setPending(seatId);
    try {
      const result = await releaseSeat(
        showingId,
        seatId,
        token,
        name.trim() || "Guest",
      );
      setSeats((prev) => ({
        ...prev,
        [seatId]: {
          ...prev[seatId],
          status: result.status,
          holder: result.holder,
          mine: result.mine,
          expires_at: result.expires_at,
        },
      }));
      if (result.ok) {
        say("good", `${seatId} is open again.`);
      } else {
        say("lost", `${seatId} is no longer yours to cancel.`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cancel failed");
      void sync();
    } finally {
      setPending(null);
    }
  }

  async function onRace() {
    const token = tokenRef.current;
    if (!token || racing) return;
    setRacing(true);
    setRace(null);
    try {
      const fresh = await fetchState(showingId, token);
      setSeats(toSeatMap(fresh.rows));
      const mineHeld = fresh.rows
        .flatMap((row) => row.seats)
        .filter((seat) => seat.mine && seat.status === "held");
      if (mineHeld.length >= maxOwned) {
        say(
          "lost",
          `You can only hold ${maxOwned} seats at a time. Book or cancel one first.`,
        );
        return;
      }
      const open = fresh.rows
        .flatMap((row) => row.seats)
        .filter((seat) => seat.status === "available");
      if (open.length === 0) {
        say("lost", "No open seats left to race for. Try again in a moment.");
        return;
      }
      const targets = [...open].sort(() => Math.random() - 0.5);
      let result: RaceResult | null = null;
      for (const target of targets) {
        setSpotlight(target.seat_id);
        try {
          result = await raceSeat(
            showingId,
            target.seat_id,
            token,
            name.trim() || "You",
            RACE_CONTENDERS,
          );
          break;
        } catch (err) {
          const message = err instanceof Error ? err.message : "";
          if (message.includes("not open")) continue;
          throw err;
        }
      }
      if (!result) {
        say("lost", "No open seats left to race for. Try again in a moment.");
        return;
      }
      setRace(result);
      if (result.winner?.you) {
        say("good", `You won ${result.seat_id}. It's held for you now.`);
      } else if (result.winner) {
        flashTaken(result.seat_id);
        say(
          "lost",
          `${result.winner.name} won ${result.seat_id} by a fraction of a second.`,
        );
      }
      await sync();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Race failed");
    } finally {
      setRacing(false);
      window.setTimeout(() => setSpotlight(null), 2600);
    }
  }

  async function onResetHouse() {
    if (resetting) return;
    setResetting(true);
    try {
      await resetShowing(showingId);
      setStatsBase(null);
      setEvents([]);
      setRace(null);
      setStrip(null);
      setName("Guest");
      saveHolderName("Guest");
      await sync();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reset failed");
    } finally {
      setResetting(false);
    }
  }

  async function onSimulateReconnect() {
    setFeed("resync");
    genRef.current += 1;
    const previous = wsRef.current;
    wsRef.current = null;
    previous?.close();
    await connect();
  }

  const seatList = useMemo(() => Object.values(seats), [seats]);
  const yourHolds = seatList.filter(
    (seat) =>
      seat.mine && seat.status === "held" && secondsLeft(seat.expires_at, now) > 0,
  );
  const yourBookings = seatList.filter(
    (seat) => seat.mine && seat.status === "booked",
  );
  const counts = {
    available: seatList.filter((s) => s.status === "available").length,
    held: seatList.filter((s) => s.status === "held").length,
    booked: seatList.filter((s) => s.status === "booked").length,
  };
  const base = statsBase;
  const session =
    stats && base
      ? {
          attempts: Math.max(0, stats.claims - base.claims),
          alreadyTaken: Math.max(0, stats.collisions - base.collisions),
          soldTwice: Math.max(0, stats.double_bookings - base.double_bookings),
        }
      : null;

  if (!showing) {
    return (
      <div className={styles.shell} data-page="showing">
        <header className={styles.bar}>
          <BrandMark className={styles.brand} />
        </header>
        <p className={styles.loading}>
          {error ? error : "Loading seat map"}
          {error ? <span>{API_URL}</span> : null}
        </p>
      </div>
    );
  }

  const liveLabel =
    feed === "on" ? "Live" : feed === "resync" ? "Resyncing" : "Reconnecting";

  return (
    <div className={styles.shell} data-page="showing">
      <header className={styles.bar}>
        <div className={styles.identity}>
          <BrandMark className={styles.brand} />
          <span className={styles.sep} aria-hidden />
          <div className={styles.showMeta}>
            <h1 className={styles.title}>{showing.title}</h1>
            <p className={styles.kicker}>
              {showing.venue_name} · {showing.auditorium} ·{" "}
              {formatShowtime(showing.starts_at)}
            </p>
          </div>
        </div>

        <div className={styles.barRight}>
          <p className={styles.live} data-state={feed}>
            <span aria-hidden className={styles.liveDot} />
            {liveLabel}
          </p>
          <div className={styles.kpis} aria-label="House counts">
            <span>
              Open <strong>{counts.available}</strong>
            </span>
            <span>
              Held <strong>{counts.held}</strong>
            </span>
            <span>
              Sold <strong>{counts.booked}</strong>
            </span>
            {session ? (
              <span>
                Double booked <strong>{session.soldTwice}</strong>
              </span>
            ) : null}
          </div>
          <button
            type="button"
            className={styles.primary}
            onClick={onRace}
            disabled={racing || pending != null}
            title="Runs twelve claims at the same instant on one open seat. Exactly one person can get it."
          >
            {racing ? "Racing…" : "Simulate 12 people, 1 seat"}
          </button>
          <button
            type="button"
            className={styles.ghost}
            onClick={onSimulateReconnect}
            title="Closes the live connection, then reloads the true seat map. Same recovery as a dropped network."
          >
            Simulate dropped connection
          </button>
          <button
            type="button"
            className={styles.ghost}
            onClick={onResetHouse}
            disabled={resetting || pending != null}
            title="Opens every seat and zeros session counts."
          >
            <svg
              className={styles.btnIcon}
              viewBox="0 0 16 16"
              aria-hidden="true"
            >
              <path
                d="M2.2 8A5.8 5.8 0 0 1 12 4.3M13.2 2.5v3.2H10M13.8 8A5.8 5.8 0 0 1 4 11.7M2.8 13.5V10.3H6"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="square"
              />
            </svg>
            {resetting ? "Resetting" : "Reset bookings"}
          </button>
        </div>
      </header>

      <div className={styles.workspace}>
        <section className={styles.house} aria-label="Seat map">
          <div className={styles.stage}>Stage</div>
          <div className={styles.grid}>
            {layout.map((row) => (
              <div key={row.label} className={styles.row}>
                <span className={styles.rowLabel}>{row.label}</span>
                {row.seats.map((cell) => {
                  const seat = seats[cell.seat_id];
                  if (!seat) return null;
                  const remaining = secondsLeft(seat.expires_at, now);
                  const lapsed = seat.status === "held" && remaining <= 0;
                  const status = lapsed ? "available" : seat.status;
                  const isMine = seat.mine && !lapsed && status !== "available";
                  const ring =
                    isMine && status === "held"
                      ? Math.max(0, Math.min(1, remaining / holdSeconds))
                      : null;

                  return (
                    <button
                      key={cell.seat_id}
                      type="button"
                      className={styles.seat}
                      data-status={status}
                      data-mine={isMine ? "true" : undefined}
                      data-taken={taken[cell.seat_id] ? "true" : undefined}
                      data-spotlight={
                        spotlight === cell.seat_id ? "true" : undefined
                      }
                      style={
                        ring != null
                          ? ({
                              "--ring": `${ring * 360}deg`,
                            } as React.CSSProperties)
                          : undefined
                      }
                      onClick={() => onSeatClick({ ...seat, status })}
                      disabled={status !== "available" || pending != null}
                      aria-label={`Seat ${cell.seat_id}, ${
                        isMine ? `${status} by you` : status
                      }`}
                    >
                      <span className={styles.seatNum}>{cell.col}</span>
                    </button>
                  );
                })}
                <span className={styles.rowLabel}>{row.label}</span>
              </div>
            ))}
          </div>
          <ul className={styles.legend}>
            <li>
              <span className={styles.chip} data-status="available" /> Open
            </li>
            <li>
              <span className={styles.chip} data-status="held" data-mine="true" />
              Your hold
            </li>
            <li>
              <span className={styles.chip} data-status="held" /> Held
            </li>
            <li>
              <span className={styles.chip} data-status="booked" data-mine="true" />
              Yours
            </li>
            <li>
              <span className={styles.chip} data-status="booked" /> Sold
            </li>
          </ul>

          <section className={styles.tickets} aria-label="Your tickets">
            <h2 className={styles.dockTitle}>Your tickets</h2>
            {yourBookings.length === 0 ? (
              <p className={styles.ticketEmpty}>None yet</p>
            ) : (
              <ul className={styles.ticketList}>
                {yourBookings.map((seat) => (
                  <li key={seat.seat_id} className={styles.ticket}>
                    <span>{seat.seat_id}</span>
                    <button
                      type="button"
                      className={styles.quiet}
                      onClick={() => onRelease(seat.seat_id)}
                      disabled={pending != null}
                    >
                      {pending === seat.seat_id ? "Cancelling" : "Cancel"}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </section>

        <aside className={styles.dock}>
          {strip ? (
            <div
              className={styles.strip}
              data-tone={strip.tone}
              role="status"
              aria-atomic="true"
            >
              <p>{strip.text}</p>
              <button
                type="button"
                className={`${styles.ghost} ${styles.compact}`}
                onClick={() => setStrip(null)}
              >
                Dismiss
              </button>
            </div>
          ) : null}

          {error ? <p className={styles.error}>{error}</p> : null}

          <section className={styles.booking}>
            <h2 className={styles.dockTitle}>Your booking</h2>
            <p className={styles.hint}>
              Select an open seat to hold it for {holdSeconds} seconds, then
              book all holds at once. You can hold up to {maxOwned} seats at a
              time.
            </p>
            <label className={styles.nameField}>
              Name on the booking
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                onBlur={(event) => saveHolderName(event.target.value.trim())}
                placeholder="Guest"
                maxLength={80}
              />
            </label>

            <div className={styles.holdsHeading}>
              <h3 className={styles.dockTitle}>On hold</h3>
              {yourHolds.length > 0 ? (
                <button
                  type="button"
                  className={`${styles.primary} ${styles.compact}`}
                  onClick={onBookAll}
                  disabled={pending != null}
                >
                  {pending === "all" ? "Booking" : "Book all"}
                </button>
              ) : null}
            </div>
            {yourHolds.length === 0 ? (
              <p className={styles.note}>No seats on hold.</p>
            ) : (
              <ul className={styles.holds}>
                {yourHolds.map((seat) => {
                  const remaining = secondsLeft(seat.expires_at, now);
                  return (
                    <li key={seat.seat_id}>
                      <div className={styles.holdRow}>
                        <span className={styles.holdSeat}>{seat.seat_id}</span>
                        <span className={styles.holdClock}>
                          {Math.ceil(remaining)}s
                        </span>
                        <button
                          type="button"
                          className={styles.quiet}
                          onClick={() => onRelease(seat.seat_id)}
                          disabled={pending != null}
                        >
                          Cancel
                        </button>
                      </div>
                      <div
                        className={styles.holdBar}
                        style={
                          {
                            "--fill": `${(remaining / holdSeconds) * 100}%`,
                          } as React.CSSProperties
                        }
                      />
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          {race ? (
            <section className={styles.race} aria-label="Race result">
              <div className={styles.holdRow}>
                <h2 className={styles.dockTitle}>Contention</h2>
                <button
                  type="button"
                  className={`${styles.ghost} ${styles.compact}`}
                  onClick={() => setRace(null)}
                >
                  Dismiss
                </button>
              </div>
              <p className={styles.raceHeadline}>
                {race.seat_was_taken
                  ? `Seat ${race.seat_id} was already taken.`
                  : `${race.contenders} people clicked ${race.seat_id} at the same instant. ${
                      race.winner?.you ? "You" : race.winner?.name
                    } got it.`}
              </p>
              <ul className={styles.raceList}>
                {[...race.results]
                  .sort((a, b) => Number(b.you) - Number(a.you))
                  .map((entrant) => (
                  <li
                    key={entrant.name + String(entrant.you)}
                    data-ok={entrant.ok ? "true" : undefined}
                  >
                    <span
                      className={styles.raceName}
                      title={entrant.you ? "You" : entrant.name}
                    >
                      {entrant.you ? "You" : entrant.name}
                    </span>
                    <span>{entrant.ok ? "held" : "rejected"}</span>
                  </li>
                ))}
              </ul>
              <p className={styles.raceFoot}>No double-booked seats.</p>
            </section>
          ) : null}

          <section className={styles.feed} aria-label="Live activity">
            <h2 className={styles.dockTitle}>Activity</h2>
            {events.length === 0 ? (
              <p className={styles.note}>Waiting for the next seat to move.</p>
            ) : (
              <ul>
                {events.map((event, index) => (
                  <li key={`${event.seat_id}-${event.at}-${index}`}>
                    <span>{eventLine(event, publicId)}</span>
                    <time>{timeAgo(event.at, now)}</time>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {session ? (
            <dl className={styles.scoreline}>
              <div>
                <dt>Seat claims</dt>
                <dd>{session.attempts.toLocaleString()}</dd>
                <p>Times anyone tried to take a seat this session.</p>
              </div>
              <div>
                <dt>Concurrent refusals</dt>
                <dd>{session.alreadyTaken.toLocaleString()}</dd>
                <p>Claims refused because the seat was already taken.</p>
              </div>
              <div>
                <dt>Double bookings</dt>
                <dd>{session.soldTwice.toLocaleString()}</dd>
                <p>Should stay 0. The lock is working.</p>
              </div>
            </dl>
          ) : null}
        </aside>
      </div>
    </div>
  );
}
