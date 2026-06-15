"use client";

// Detect whether the current thread has a run already in flight on the server.
//
// Chat streams use an explicit resume path instead of the SDK's sessionStorage
// reconnect. The SDK reconnect can double-join the same run during /new -> /id
// route transitions while the original submit stream is still open. This hook
// polls the server's `runs.list({ status: "running" })` when the page mounts and
// exposes the running run id so `useRejoinRunningRun` can join real resumed or
// out-of-band runs exactly once.

import { useEffect, useState } from "react";

import { getAPIClient } from "../api/api-client";

export interface RunningRunInfo {
  runId: string;
  createdAt: string | null;
}

export function useRunningRun(
  threadId: string | null | undefined,
  pollBump = 0,
): {
  runningRun: RunningRunInfo | null;
  loading: boolean;
  pollFailed: boolean;
} {
  const [runningRun, setRunningRun] = useState<RunningRunInfo | null>(null);
  const [loading, setLoading] = useState<boolean>(Boolean(threadId));
  const [pollFailed, setPollFailed] = useState(false);

  useEffect(() => {
    if (!threadId) {
      setRunningRun(null);
      setLoading(false);
      setPollFailed(false);
      return;
    }
    const client = getAPIClient();
    let cancelled = false;
    const pollRunningRun = async (isInitial: boolean) => {
      if (isInitial) {
        setLoading(true);
      }
      try {
        const runs = await client.runs.list(threadId, { status: "running", limit: 1 });
        if (cancelled) return;
        setPollFailed(false);
        const first = runs?.[0];
        if (first) {
          setRunningRun({
            runId: String(first.run_id),
            createdAt:
              typeof first.created_at === "string" ? first.created_at : null,
          });
        } else {
          setRunningRun(null);
        }
      } catch {
        if (cancelled) return;
        setPollFailed(true);
      } finally {
        if (isInitial && !cancelled) {
          setLoading(false);
        }
      }
    };

    void pollRunningRun(true);
    const interval = window.setInterval(() => {
      void pollRunningRun(false);
    }, 15000);
    const onFocus = () => {
      void pollRunningRun(false);
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        void pollRunningRun(false);
      }
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [pollBump, threadId]);

  return { runningRun, loading, pollFailed };
}
