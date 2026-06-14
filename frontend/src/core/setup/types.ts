export type SetupAction =
  | "update_all"
  | "websearch_enable_docker"
  | "websearch_enable_podman"
  | "websearch_disable"
  | "websearch_repair";

export interface SetupComponentStatus {
  status: string;
  message?: string | null;
}

export interface SetupJob {
  job_id: string;
  action: string;
  status: string;
  message?: string | null;
  updated_at?: string | null;
}

export interface SetupStatus {
  managed_setup_enabled: boolean;
  docker: SetupComponentStatus;
  podman: SetupComponentStatus;
  daemon: SetupComponentStatus;
  capyhome: SetupComponentStatus;
  llm: SetupComponentStatus;
  websearch: SetupComponentStatus;
  websearch_replicas: number;
  websearch_runtime?: "docker" | "podman" | null;
  latest_job?: SetupJob | null;
}

export interface WebSearchTestResult {
  ok: boolean;
  message: string;
  query?: string | null;
  result_count?: number | null;
}
