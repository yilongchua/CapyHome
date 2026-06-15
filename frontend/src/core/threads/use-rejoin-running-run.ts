"use client";

import { useEffect, useRef, useState } from "react";

import { decideRejoinAction } from "./rejoin-policy";
import { useRunningRun } from "./use-running-run";

type ThreadRunStreamMode =
  | "messages-tuple"
  | "values"
  | "updates"
  | "custom"
  | "events";

export const THREAD_RUN_STREAM_MODES: ThreadRunStreamMode[] = [
  "messages-tuple",
  "updates",
  "custom",
  "events",
];

const LOCAL_STREAM_SUPPRESS_MS = 5 * 60 * 1000;
const LOCAL_STREAM_STORAGE_PREFIX = "capyhome.localStreamUntil.";
const MAX_REJOIN_RETRIES = 3;
const REJOIN_RETRY_BASE_MS = 1000;

function localStreamStorageKey(threadId: string): string {
  return `${LOCAL_STREAM_STORAGE_PREFIX}${threadId}`;
}

export function markLocalThreadStream(threadId: string | null | undefined): void {
  if (!threadId || typeof window === "undefined") {
    return;
  }
  try {
    window.sessionStorage.setItem(
      localStreamStorageKey(threadId),
      String(Date.now() + LOCAL_STREAM_SUPPRESS_MS),
    );
  } catch {
    // Storage can be unavailable in private or restricted browser contexts.
  }
}

export function clearLocalThreadStream(threadId: string | null | undefined): void {
  if (!threadId || typeof window === "undefined") {
    return;
  }
  try {
    window.sessionStorage.removeItem(localStreamStorageKey(threadId));
  } catch {
    // Storage can be unavailable in private or restricted browser contexts.
  }
}

function hasActiveLocalThreadStream(threadId: string | null | undefined): boolean {
  if (!threadId || typeof window === "undefined") {
    return false;
  }
  try {
    const key = localStreamStorageKey(threadId);
    const raw = window.sessionStorage.getItem(key);
    const until = raw ? Number(raw) : 0;
    if (!Number.isFinite(until) || until <= Date.now()) {
      window.sessionStorage.removeItem(key);
      return false;
    }
    return true;
  } catch {
    return false;
  }
}

type ThreadStream = {
  error?: unknown;
  isLoading: boolean;
  joinStream: (
    runId: string,
    lastEventId?: string,
    options?: { streamMode?: ThreadRunStreamMode[] },
  ) => Promise<void>;
};

/**
 * Rejoin an out-of-band server run (execute plan, planner auto-handoff) via SSE.
 */
export function useRejoinRunningRun(
  threadId: string | null | undefined,
  thread: ThreadStream,
  options?: { pollBump?: number },
): ReturnType<typeof useRunningRun> {
  const pollBump = options?.pollBump ?? 0;
  const { error: streamError, isLoading: streamLoading, joinStream } = thread;
  const { runningRun, loading, pollFailed } = useRunningRun(threadId, pollBump);
  const lastJoinedRunningRunRef = useRef<string | null>(null);
  const retryRunIdRef = useRef<string | null>(null);
  const retryCountRef = useRef(0);
  const retryTimerRef = useRef<number | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);

  useEffect(() => {
    lastJoinedRunningRunRef.current = null;
    retryRunIdRef.current = null;
    retryCountRef.current = 0;
    if (retryTimerRef.current !== null) {
      window.clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
    return () => {
      if (retryTimerRef.current !== null) {
        window.clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
    };
  }, [threadId]);

  useEffect(() => {
    if (streamError) {
      clearLocalThreadStream(threadId);
    }
  }, [streamError, threadId]);

  useEffect(() => {
    const runId = runningRun?.runId;
    const action = decideRejoinAction({
      pollLoading: loading,
      pollFailed,
      runId: runId ?? null,
      streamLoading,
      streamError: Boolean(streamError),
      ownsLocalStream: hasActiveLocalThreadStream(threadId),
      lastJoinedRunId: lastJoinedRunningRunRef.current,
    });
    if (action === "wait") {
      return;
    }
    if (action === "clear-local-owner") {
      clearLocalThreadStream(threadId);
      lastJoinedRunningRunRef.current = null;
      retryRunIdRef.current = null;
      retryCountRef.current = 0;
      return;
    }
    if (!runId) {
      return;
    }
    if (retryRunIdRef.current !== runId) {
      retryRunIdRef.current = runId;
      retryCountRef.current = 0;
      lastJoinedRunningRunRef.current = null;
    }
    lastJoinedRunningRunRef.current = runId;
    void joinStream(runId, undefined, {
      streamMode: THREAD_RUN_STREAM_MODES,
    }).catch((error) => {
      retryCountRef.current += 1;
      if (retryCountRef.current > MAX_REJOIN_RETRIES) {
        console.warn("Failed to rejoin running stream after retries:", error);
        return;
      }
      const delay = REJOIN_RETRY_BASE_MS * 2 ** (retryCountRef.current - 1);
      retryTimerRef.current = window.setTimeout(() => {
        retryTimerRef.current = null;
        lastJoinedRunningRunRef.current = null;
        setRetryNonce((value) => value + 1);
      }, delay);
    });
  }, [
    loading,
    pollFailed,
    retryNonce,
    runningRun?.runId,
    joinStream,
    streamError,
    streamLoading,
    threadId,
  ]);

  return { runningRun, loading, pollFailed };
}
