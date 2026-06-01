"use client";

import { useEffect, useRef } from "react";

export interface IdleAutoRunArgs {
  /** Minutes of inactivity before auto-running. 0 disables the feature. */
  idleMinutes: number;
  /** True while an ingest OR lint is in progress (suppresses auto-trigger). */
  isBusy: boolean;
  /** True specifically while a vault ingest is running. */
  ingestActive: boolean;
  /** Start an ingest (caller pins 1 worker + default model). */
  onAutoIngest: () => void;
  /** Start a lint preview (caller pins 1 worker + default model). */
  onAutoLint: () => void;
}

/**
 * Frontend-driven passive auto-runner for the vault, active only while the
 * vault page is mounted (i.e. the app/tab is open).
 *
 * Cycle, once idle for `idleMinutes`:
 *   armed → auto-ingest → (ingest finishes) → auto-lint preview → done.
 * It then stays "done" until user activity re-arms it, which prevents
 * over-running. Any user interaction (or the backend being busy) resets the
 * idle timer.
 *
 * Inputs are mirrored into a ref and read inside a single fixed interval, so
 * the timer isn't torn down and recreated on every render / busy-state change.
 */
export function useIdleAutoRun(args: IdleAutoRunArgs): void {
  const argsRef = useRef(args);
  argsRef.current = args;

  const lastActivityRef = useRef<number>(Date.now());
  const phaseRef = useRef<"armed" | "awaiting-ingest" | "done">("armed");
  const awaitingSinceRef = useRef<number>(0);

  // User-presence + tab-focus events reset the idle timer and re-arm a
  // completed cycle. Backend request activity is captured indirectly: any
  // request the user triggers follows an interaction, and the `isBusy` gate
  // covers in-flight ingest/lint. (The vault page's own background status
  // polling is intentionally NOT treated as activity, else it never idles.)
  useEffect(() => {
    const bump = () => {
      lastActivityRef.current = Date.now();
      if (phaseRef.current === "done") phaseRef.current = "armed";
    };
    const events = ["mousemove", "mousedown", "keydown", "scroll", "touchstart", "visibilitychange"];
    events.forEach((e) => window.addEventListener(e, bump, { passive: true }));
    return () => events.forEach((e) => window.removeEventListener(e, bump));
  }, []);

  useEffect(() => {
    const TICK_MS = 15_000;
    // Grace window after ingest stops (or if it never started, e.g. empty
    // queue) before kicking off lint — lets the manifest/compile settle.
    const SETTLE_MS = 60_000;
    const id = setInterval(() => {
      const { idleMinutes, isBusy, ingestActive, onAutoIngest, onAutoLint } = argsRef.current;
      if (idleMinutes <= 0) {
        phaseRef.current = "armed";
        return;
      }
      const idleMs = idleMinutes * 60_000;
      const phase = phaseRef.current;

      if (phase === "armed") {
        if (isBusy) {
          // Backend busy counts as activity so we never pile on.
          lastActivityRef.current = Date.now();
          return;
        }
        if (Date.now() - lastActivityRef.current >= idleMs) {
          phaseRef.current = "awaiting-ingest";
          awaitingSinceRef.current = Date.now();
          onAutoIngest();
        }
        return;
      }

      if (phase === "awaiting-ingest") {
        if (ingestActive) {
          // Still ingesting — keep the settle clock anchored to "now" so it
          // only elapses after ingest actually stops.
          awaitingSinceRef.current = Date.now();
          return;
        }
        if (Date.now() - awaitingSinceRef.current >= SETTLE_MS) {
          phaseRef.current = "done";
          onAutoLint();
        }
      }
    }, TICK_MS);
    return () => clearInterval(id);
  }, []);
}
