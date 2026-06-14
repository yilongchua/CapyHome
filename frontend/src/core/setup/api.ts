import { getBackendBaseURL } from "@/core/config";

import type {
  SetupAction,
  SetupJob,
  SetupStatus,
  WebSearchTestResult,
} from "./types";

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as
      | { detail?: string }
      | null;
    throw new Error(
      body?.detail ?? `Setup request failed (${response.status})`,
    );
  }
  return response.json() as Promise<T>;
}

export async function loadSetupStatus(): Promise<SetupStatus> {
  const response = await fetch(`${getBackendBaseURL()}/api/setup/status`, {
    cache: "no-store",
  });
  return parseResponse<SetupStatus>(response);
}

export async function runSetupAction(action: SetupAction): Promise<SetupJob> {
  const response = await fetch(`${getBackendBaseURL()}/api/setup/actions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
  return parseResponse<SetupJob>(response);
}

export async function testWebSearchConnection(): Promise<WebSearchTestResult> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/setup/websearch/test-connection`,
    { method: "POST" },
  );
  return parseResponse<WebSearchTestResult>(response);
}

export async function runWebSearchLiveTest(): Promise<WebSearchTestResult> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/setup/websearch/live-test`,
    { method: "POST" },
  );
  return parseResponse<WebSearchTestResult>(response);
}
