export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * Defaults to the API origin with the matching socket scheme. A page served
 * over https cannot open a plain ws:// socket, so deriving this keeps the
 * two from drifting when a deploy sets only NEXT_PUBLIC_API_URL.
 */
export const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL ?? API_URL.replace(/^http(s?):\/\//, "ws$1://");

export type SeatStatus = "available" | "held" | "booked";

export type Showing = {
  id: number;
  title: string;
  auditorium: string;
  starts_at: string;
  row_count: number;
  col_count: number;
  venue_name: string;
  venue_city: string;
};

export type Seat = {
  seat_id: string;
  col: number;
  status: SeatStatus;
  /** Derived public id of the holder, never their private token. */
  holder: string | null;
  mine: boolean;
  expires_at: string | null;
};

export type SeatRow = {
  label: string;
  seats: Seat[];
};

export type ClaimStats = {
  claims: number;
  collisions: number;
  bookings: number;
  /** Measured, not asserted: booking rows minus distinct seats booked. */
  double_bookings: number;
};

export type ActivityEvent = {
  seat_id: string;
  kind: "held" | "booked" | "opened";
  holder: string | null;
  holder_name: string | null;
  at: string;
};

export type ShowingState = {
  showing: Showing;
  rows: SeatRow[];
  counts: { available: number; held: number; booked: number };
  your_holds: string[];
  your_public_id: string | null;
  hold_seconds: number;
  max_owned: number;
  version: number;
  stats: ClaimStats;
  events: ActivityEvent[];
};

/** A seat change pushed over the socket. `mine` is resolved client-side. */
export type SeatChange = Omit<Seat, "col" | "mine"> & { mine?: boolean };

export type LiveMessage = {
  version: number;
  changes: SeatChange[];
  events: ActivityEvent[];
};

export type RaceEntrant = {
  name: string;
  you: boolean;
  ok: boolean;
  status: SeatStatus;
};

export type RaceResult = {
  seat_id: string;
  contenders: number;
  results: RaceEntrant[];
  winner: RaceEntrant | null;
  rejected: number;
  seat_was_taken: boolean;
};

export type SeatActionResponse = {
  ok: boolean;
  seat_id: string;
  status: SeatStatus;
  holder: string | null;
  mine: boolean;
  expires_at: string | null;
};

/**
 * A claim has one rejection the other actions do not: the patron already
 * holds the maximum number of seats, which the Lua script reports as
 * `limit`. It describes the claimant, not the seat, which stays untouched.
 */
export type ClaimResponse = Omit<SeatActionResponse, "status"> & {
  status: SeatStatus | "limit";
};

const HOLDER_KEY = "velora.holder_id";
const NAME_KEY = "velora.holder_name";

/** A per-browser secret. Possession of this token is what proves a hold. */
export function holderToken(): string {
  const existing = localStorage.getItem(HOLDER_KEY);
  if (existing) return existing;
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  const token = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(
    "",
  );
  localStorage.setItem(HOLDER_KEY, token);
  return token;
}

export function loadHolderName(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(NAME_KEY) ?? "";
}

export function saveHolderName(name: string): void {
  localStorage.setItem(NAME_KEY, name);
}

export async function fetchShowings(): Promise<Showing[]> {
  const res = await fetch(`${API_URL}/showings`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to load showings (${res.status})`);
  return res.json();
}

export async function resetShowing(showingId: string): Promise<void> {
  const res = await fetch(`${API_URL}/showings/${showingId}/reset`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Failed to reset showing (${res.status})`);
}

export async function resetAllShowings(showingIds: number[]): Promise<void> {
  const results = await Promise.allSettled(
    showingIds.map((id) => resetShowing(String(id))),
  );
  if (results.some((result) => result.status === "rejected")) {
    throw new Error("Failed to reset one or more concerts");
  }
}

export async function fetchState(
  showingId: string,
  holderId: string | null,
): Promise<ShowingState> {
  const qs = holderId ? `?holder_id=${encodeURIComponent(holderId)}` : "";
  const res = await fetch(`${API_URL}/showings/${showingId}/state${qs}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Failed to load seat map (${res.status})`);
  return res.json();
}

async function seatAction<T extends SeatActionResponse | ClaimResponse>(
  action: "claim" | "book" | "release",
  showingId: string,
  seatId: string,
  holderId: string,
  holderName: string,
): Promise<T> {
  const res = await fetch(
    `${API_URL}/showings/${showingId}/seats/${seatId}/${action}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ holder_id: holderId, holder_name: holderName }),
    },
  );
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail =
      typeof body.detail === "string"
        ? body.detail
        : `Request failed (${res.status})`;
    throw new Error(detail);
  }
  return body;
}

export function claimSeat(
  showingId: string,
  seatId: string,
  holderId: string,
  holderName: string,
): Promise<ClaimResponse> {
  return seatAction<ClaimResponse>(
    "claim",
    showingId,
    seatId,
    holderId,
    holderName,
  );
}

export function bookSeat(
  showingId: string,
  seatId: string,
  holderId: string,
  holderName: string,
): Promise<SeatActionResponse> {
  return seatAction<SeatActionResponse>(
    "book",
    showingId,
    seatId,
    holderId,
    holderName,
  );
}

export function releaseSeat(
  showingId: string,
  seatId: string,
  holderId: string,
  holderName: string,
): Promise<SeatActionResponse> {
  return seatAction<SeatActionResponse>(
    "release",
    showingId,
    seatId,
    holderId,
    holderName,
  );
}

export async function raceSeat(
  showingId: string,
  seatId: string,
  holderId: string,
  holderName: string,
  contenders: number,
): Promise<RaceResult> {
  const res = await fetch(
    `${API_URL}/showings/${showingId}/seats/${seatId}/race`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        holder_id: holderId,
        holder_name: holderName,
        contenders,
      }),
    },
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail =
      typeof body.detail === "string"
        ? body.detail
        : `Race failed (${res.status})`;
    throw new Error(detail);
  }
  return res.json();
}

/** "just now", "8s ago", "3m ago" — no library, no absolute timestamps. */
export function timeAgo(iso: string, now: number): string {
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (seconds < 3) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  return `${Math.floor(seconds / 60)}m ago`;
}

export function formatShowtime(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
