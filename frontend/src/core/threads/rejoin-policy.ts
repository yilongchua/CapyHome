export type RejoinAction = "wait" | "clear-local-owner" | "join";

export function decideRejoinAction({
  pollLoading,
  pollFailed,
  runId,
  streamLoading,
  streamError,
  ownsLocalStream,
  lastJoinedRunId,
}: {
  pollLoading: boolean;
  pollFailed: boolean;
  runId: string | null;
  streamLoading: boolean;
  streamError: boolean;
  ownsLocalStream: boolean;
  lastJoinedRunId: string | null;
}): RejoinAction {
  if (pollLoading || pollFailed) {
    return "wait";
  }
  if (!runId) {
    return "clear-local-owner";
  }
  if (streamLoading) {
    return "wait";
  }
  if (!streamError && ownsLocalStream) {
    return "wait";
  }
  if (lastJoinedRunId === runId) {
    return "wait";
  }
  return "join";
}
