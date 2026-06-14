"use client";

import {
  BotIcon,
  CableIcon,
  CheckCircle2Icon,
  DownloadIcon,
  NewspaperIcon,
  PlayIcon,
  RefreshCwIcon,
  SearchIcon,
  ServerIcon,
  TriangleAlertIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useRunSetupAction,
  useRunWebSearchLiveTest,
  useSetupStatus,
  useTestWebSearchConnection,
} from "@/core/setup";
import type {
  SetupAction,
  SetupComponentStatus,
} from "@/core/setup/types";

import { SettingsSection } from "./settings-section";

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

function badgeVariant(status: string) {
  if (["healthy", "configured", "running", "current", "succeeded"].includes(status)) {
    return "secondary" as const;
  }
  if (["failed", "unhealthy", "missing", "stopped", "unavailable"].includes(status)) {
    return "destructive" as const;
  }
  return "outline" as const;
}

function StatusCard({
  title,
  description,
  status,
  icon: Icon,
  actions,
}: {
  title: string;
  description: string;
  status: SetupComponentStatus;
  icon: React.ComponentType<{ className?: string }>;
  actions?: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border p-4">
      <div className="flex items-start gap-3">
        <div className="bg-muted rounded-md p-2">
          <Icon className="size-4" />
        </div>
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="font-medium">{title}</div>
            <Badge variant={badgeVariant(status.status)}>
              {status.status.replaceAll("_", " ")}
            </Badge>
          </div>
          <p className="text-muted-foreground text-sm">{description}</p>
          {status.message && (
            <p className="text-destructive text-sm">{status.message}</p>
          )}
          {actions && <div className="flex flex-wrap gap-2 pt-2">{actions}</div>}
        </div>
      </div>
    </div>
  );
}

export function SetupSettingsPage({
  onOpenLlm,
}: {
  onOpenLlm: () => void;
}) {
  const statusQuery = useSetupStatus();
  const actionMutation = useRunSetupAction();
  const connectionMutation = useTestWebSearchConnection();
  const liveTestMutation = useRunWebSearchLiveTest();
  const [selectedRuntime, setSelectedRuntime] = useState<"docker" | "podman">(
    "docker",
  );
  const status = statusQuery.data;
  const latestJob = status?.latest_job;
  const operationActive =
    actionMutation.isPending ||
    ACTIVE_JOB_STATUSES.has(latestJob?.status ?? "");

  useEffect(() => {
    if (status?.websearch_runtime) {
      setSelectedRuntime(status.websearch_runtime);
    }
  }, [status?.websearch_runtime]);

  function run(action: SetupAction) {
    actionMutation.mutate(action, {
      onSuccess: (job) => {
        toast.success(
          action === "update_all"
            ? "Updating both repositories. CapyHome will restart."
            : `Setup operation queued (${job.job_id.slice(0, 8)}).`,
        );
      },
      onError: (error) => toast.error(error.message),
    });
  }

  if (statusQuery.isLoading) {
    return (
      <SettingsSection
        title="Setup"
        description="Checking the local CapyHome installation."
      >
        <div className="text-muted-foreground text-sm">Loading setup status...</div>
      </SettingsSection>
    );
  }

  if (!status) {
    return (
      <SettingsSection title="Setup" description="Local installation status.">
        <div className="text-destructive text-sm">
          {statusQuery.error?.message ?? "Setup status is unavailable."}
        </div>
      </SettingsSection>
    );
  }

  const dockerHealthy = status.docker.status === "running";
  const podmanHealthy = status.podman.status === "running";
  const selectedRuntimeHealthy =
    selectedRuntime === "docker" ? dockerHealthy : podmanHealthy;
  const testingSelectedRuntime =
    !status.websearch_runtime ||
    status.websearch_runtime === selectedRuntime;
  const runtimeDownloadUrl =
    selectedRuntime === "docker"
      ? "https://www.docker.com/products/docker-desktop/"
      : "https://podman-desktop.io/downloads";

  function testConnection() {
    connectionMutation.mutate(undefined, {
      onSuccess: (result) => toast.success(result.message),
      onError: (error) => toast.error(error.message),
    });
  }

  function runLiveTest() {
    liveTestMutation.mutate(undefined, {
      onSuccess: (result) => toast.success(result.message),
      onError: (error) => toast.error(error.message),
    });
  }

  return (
    <SettingsSection
      title="Setup"
      description="Manage the local production stack, LLM provider, WebSearch, and software updates."
    >
      <div className="space-y-3">
        <StatusCard
          title="CapyHome"
          description="Gateway and workspace services."
          status={status.capyhome}
          icon={ServerIcon}
          actions={
            <Button
              size="sm"
              variant="outline"
              onClick={() => void statusQuery.refetch()}
            >
              <RefreshCwIcon />
              Check again
            </Button>
          }
        />

        <div className="rounded-lg border p-4">
          <div className="flex items-start gap-3">
            <div className="bg-muted rounded-md p-2">
              <SearchIcon className="size-4" />
            </div>
            <div className="min-w-0 flex-1 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div className="font-medium">WebSearch Container</div>
                  <p className="text-muted-foreground text-sm">
                    Download a runtime, launch {status.websearch_replicas} replicas,
                    verify MCP connectivity, then run a real news search.
                  </p>
                </div>
                <Badge variant={badgeVariant(status.websearch.status)}>
                  {status.websearch.status.replaceAll("_", " ")}
                </Badge>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium">Container runtime</span>
                <Select
                  value={selectedRuntime}
                  onValueChange={(value) =>
                    setSelectedRuntime(value as "docker" | "podman")
                  }
                >
                  <SelectTrigger size="sm" className="w-[150px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="docker">Docker</SelectItem>
                    <SelectItem value="podman">Podman</SelectItem>
                  </SelectContent>
                </Select>
                <Badge
                  variant={badgeVariant(
                    selectedRuntime === "docker"
                      ? status.docker.status
                      : status.podman.status,
                  )}
                >
                  {selectedRuntimeHealthy ? "running" : "not ready"}
                </Badge>
              </div>

              {status.websearch.message && (
                <p className="text-destructive text-sm">
                  {status.websearch.message}
                </p>
              )}

              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                <Button size="sm" variant="outline" asChild>
                  <a
                    href={runtimeDownloadUrl}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <DownloadIcon />
                    Download
                  </a>
                </Button>
                <Button
                  size="sm"
                  disabled={
                    operationActive ||
                    !dockerHealthy ||
                    !selectedRuntimeHealthy ||
                    !status.managed_setup_enabled
                  }
                  onClick={() =>
                    run(
                      selectedRuntime === "docker"
                        ? "websearch_enable_docker"
                        : "websearch_enable_podman",
                    )
                  }
                >
                  <PlayIcon />
                  Launch Containers
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={
                    !testingSelectedRuntime ||
                    connectionMutation.isPending
                  }
                  onClick={testConnection}
                >
                  <CableIcon />
                  {connectionMutation.isPending
                    ? "Testing..."
                    : "Test Connection"}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={
                    !testingSelectedRuntime ||
                    liveTestMutation.isPending
                  }
                  onClick={runLiveTest}
                >
                  <NewspaperIcon />
                  {liveTestMutation.isPending ? "Searching..." : "Live Test"}
                </Button>
              </div>

              {(connectionMutation.data != null ||
                liveTestMutation.data != null) && (
                <p className="text-sm">
                  {liveTestMutation.data?.message ??
                    connectionMutation.data?.message}
                </p>
              )}
              {(connectionMutation.error != null ||
                liveTestMutation.error != null) && (
                <p className="text-destructive text-sm">
                  {liveTestMutation.error?.message ??
                    connectionMutation.error?.message}
                </p>
              )}

              {!status.managed_setup_enabled && (
                <p className="text-muted-foreground text-xs">
                  Container launch is available when CapyHome is started with{" "}
                  <code>make local-prod</code>.
                </p>
              )}
            </div>
          </div>
        </div>

        <StatusCard
          title="LLM Provider"
          description="At least one configured provider is required before chatting."
          status={status.llm}
          icon={BotIcon}
          actions={
            <>
              <Button size="sm" variant="outline" onClick={onOpenLlm}>
                Configure provider
              </Button>
              <Button size="sm" variant="outline" asChild>
                <a
                  href="https://lmstudio.ai/download"
                  target="_blank"
                  rel="noreferrer"
                >
                  <DownloadIcon />
                  LM Studio
                </a>
              </Button>
              <Button size="sm" variant="outline" asChild>
                <a
                  href="https://ollama.com/download"
                  target="_blank"
                  rel="noreferrer"
                >
                  <DownloadIcon />
                  Ollama
                </a>
              </Button>
            </>
          }
        />

        <div className="rounded-lg border p-4">
          <div className="flex items-start gap-3">
            <div className="bg-muted rounded-md p-2">
              {latestJob?.status === "failed" ? (
                <TriangleAlertIcon className="text-destructive size-4" />
              ) : (
                <CheckCircle2Icon className="size-4" />
              )}
            </div>
            <div className="min-w-0 flex-1 space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="font-medium">Software Update</div>
                {latestJob && (
                  <Badge variant={badgeVariant(latestJob.status)}>
                    {latestJob.status.replaceAll("_", " ")}
                  </Badge>
                )}
              </div>
              <p className="text-muted-foreground text-sm">
                Pull the latest configured branches for CapyHome and WebSearch,
                rebuild, restart, and verify both services.
              </p>
              {latestJob?.message && (
                <p className="text-sm">{latestJob.message}</p>
              )}
              <Button
                size="sm"
                disabled={
                  operationActive ||
                  !dockerHealthy ||
                  !status.managed_setup_enabled
                }
                onClick={() => run("update_all")}
              >
                <RefreshCwIcon
                  className={operationActive ? "animate-spin" : undefined}
                />
                {operationActive ? "Updating and restarting..." : "Update All"}
              </Button>
              {!status.managed_setup_enabled && (
                <p className="text-muted-foreground text-xs">
                  Managed actions are available when CapyHome is started with{" "}
                  <code>make local-prod</code>.
                </p>
              )}
            </div>
          </div>
        </div>
      </div>
    </SettingsSection>
  );
}
