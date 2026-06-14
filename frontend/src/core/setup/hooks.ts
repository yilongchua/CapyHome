import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  loadSetupStatus,
  runSetupAction,
  runWebSearchLiveTest,
  testWebSearchConnection,
} from "./api";
import type { SetupAction } from "./types";

const ACTIVE_JOB_STATUSES = new Set([
  "queued",
  "checking_prerequisites",
  "fetching_repositories",
  "validating_fast_forward",
  "updating_repositories",
  "building",
  "reconciling_compose",
  "starting",
  "checking_health",
  "registering_mcp",
  "verifying_tools",
]);

export function useSetupStatus() {
  return useQuery({
    queryKey: ["setupStatus"],
    queryFn: loadSetupStatus,
    refetchInterval: (query) =>
      ACTIVE_JOB_STATUSES.has(query.state.data?.latest_job?.status ?? "")
        ? 2_000
        : 10_000,
    retry: 2,
  });
}

export function useRunSetupAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (action: SetupAction) => runSetupAction(action),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["setupStatus"] });
      void queryClient.invalidateQueries({ queryKey: ["mcpConfig"] });
    },
  });
}

export function useTestWebSearchConnection() {
  return useMutation({ mutationFn: testWebSearchConnection });
}

export function useRunWebSearchLiveTest() {
  return useMutation({ mutationFn: runWebSearchLiveTest });
}
