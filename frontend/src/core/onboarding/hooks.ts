import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  loadLlmEndpoints,
  saveLlmEndpoints,
  testComfyuiEndpoint,
  testGenericEndpoint,
  testLlmEndpoint,
} from "./api";
import type {
  ComfyuiTestResult,
  GenericTestResult,
  LlmTestResult,
  UserLlmEndpoint,
} from "./types";

export function useLlmEndpoints() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["llmEndpoints"],
    queryFn: () => loadLlmEndpoints(),
  });
  return { endpoints: data?.userModels ?? ({} as Record<string, UserLlmEndpoint>), isLoading, error };
}

export function useSaveLlmEndpoints() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (userModels: Record<string, UserLlmEndpoint>) => {
      await saveLlmEndpoints(userModels);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["llmEndpoints"] });
    },
  });
}

export function useTestLlmEndpoint() {
  return useMutation<LlmTestResult, Error, { baseUrl: string; apiKey: string }>({
    mutationFn: ({ baseUrl, apiKey }) => testLlmEndpoint(baseUrl, apiKey),
  });
}

export function useTestComfyuiEndpoint() {
  return useMutation<ComfyuiTestResult, Error, { baseUrl: string }>({
    mutationFn: ({ baseUrl }) => testComfyuiEndpoint(baseUrl),
  });
}

export function useTestGenericEndpoint() {
  return useMutation<GenericTestResult, Error, { url: string; timeoutSeconds?: number }>({
    mutationFn: ({ url, timeoutSeconds }) => testGenericEndpoint(url, timeoutSeconds),
  });
}
