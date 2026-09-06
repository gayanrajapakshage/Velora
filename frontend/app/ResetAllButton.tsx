"use client";

import { useState } from "react";

import { resetAllShowings } from "../lib/api";
import styles from "./page.module.css";

export function ResetAllButton({ ids }: { ids: number[] }) {
  const [busy, setBusy] = useState(false);

  async function onReset() {
    if (busy || ids.length === 0) return;
    setBusy(true);
    try {
      await resetAllShowings(ids);
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      className={styles.navReset}
      onClick={onReset}
      disabled={busy || ids.length === 0}
      title="Opens every seat on every concert."
    >
      <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
        <path
          d="M2.2 8A5.8 5.8 0 0 1 12 4.3M13.2 2.5v3.2H10M13.8 8A5.8 5.8 0 0 1 4 11.7M2.8 13.5V10.3H6"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="square"
        />
      </svg>
      {busy ? "Resetting" : "Reset all bookings"}
    </button>
  );
}
