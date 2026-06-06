"use client";

import { ChevronUpIcon, Clock3Icon, GaugeIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { useThread } from "@/components/workspace/messages/context";
import { PhaseProgress } from "@/components/workspace/phase-progress";
import { asActivityTimelineState, mergeActivityEvents, useActivityContext } from "@/core/activity";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { api } from "@/core/workspace-io/api";
import { cn } from "@/lib/utils";

import {
  TIMELINE_MAX_ITEMS,
  type TimelineIcon,
  type TimelineItem,
} from "./timeline-helpers";
import { TimelineItemRow } from "./timeline-item-row";

function iconFromEventKind(kind: string, actor: string): TimelineIcon {
  if (kind.includes("failed") || kind.includes("timed_out")) {
    return "failed";
  }
  if (kind.includes("completed")) {
    return "done";
  }
  if (kind.includes("tool") || kind.includes("task")) {
    return "tool";
  }
  if (actor === "baby_capy") {
    return "assistant";
  }
  if (actor === "system") {
    return "tool";
  }
  return "assistant";
}

type WorkflowExecution = {
  status?: string;
  max_parallel?: number;
  current_row_index?: number;
  completed_rows?: number;
  consecutive_failures?: number;
  failure_rows?: unknown[];
  last_run_seconds?: number;
  average_run_seconds?: number;
  estimated_remaining_seconds?: number;
};

type WorkflowStatusResponse = {
  exists: boolean;
  initialized?: boolean;
  workflow?: {
    source?: {
      row_count?: number;
    };
    execution?: WorkflowExecution;
  } | null;
};

function formatDuration(seconds: number | undefined): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) {
    return "-";
  }
  const rounded = Math.round(seconds);
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const secs = rounded % 60;
  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  if (minutes > 0) {
    return `${minutes}m ${secs}s`;
  }
  return `${secs}s`;
}

function WorkflowStatusCard({
  workflowStatus,
}: {
  workflowStatus: WorkflowStatusResponse | null;
}) {
  const workflow = workflowStatus?.workflow;
  const execution = workflow?.execution;
  if (!workflowStatus?.exists || !workflow || !execution) {
    return null;
  }

  const totalRows = Math.max(0, Number(workflow.source?.row_count ?? 0));
  const completedRows = Math.max(0, Number(execution.completed_rows ?? 0));
  const failureRows = Array.isArray(execution.failure_rows) ? execution.failure_rows : [];
  const failureCount = failureRows.length;
  const processedRows = totalRows > 0 ? Math.min(totalRows, completedRows + failureCount) : completedRows + failureCount;
  const percentage = totalRows > 0 ? Math.round((processedRows / totalRows) * 100) : 0;
  const currentRowIndex = Math.max(0, Number(execution.current_row_index ?? 0));
  const maxParallel = Math.max(1, Number(execution.max_parallel ?? 1));
  const remainingRows = Math.max(0, totalRows - processedRows);
  const estimatedRemainingSeconds =
    typeof execution.estimated_remaining_seconds === "number"
      ? execution.estimated_remaining_seconds
      : typeof execution.average_run_seconds === "number"
        ? (remainingRows * execution.average_run_seconds) / maxParallel
        : undefined;
  const timePerRun = execution.average_run_seconds ?? execution.last_run_seconds;
  const status = String(execution.status ?? "ready");
  const nextRow = totalRows > 0 && processedRows >= totalRows ? "-" : String(currentRowIndex + 1);

  return (
    <section className="space-y-3 rounded-lg border p-3">
      <header className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          <GaugeIcon className="size-4" />
          Workflow
        </div>
        <Badge variant={status === "stopped_failed_threshold" ? "destructive" : "secondary"}>
          {percentage}%
        </Badge>
      </header>
      <div className="bg-muted h-2 overflow-hidden rounded-full">
        <div
          className="bg-primary h-full rounded-full transition-[width] duration-300"
          style={{ width: `${Math.min(100, Math.max(0, percentage))}%` }}
        />
      </div>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <WorkflowMetric label="Status" value={status} />
        <WorkflowMetric label="Rows" value={`${processedRows}/${totalRows || "-"}`} />
        <WorkflowMetric label="Time per run" value={formatDuration(timePerRun)} />
        <WorkflowMetric label="ETA" value={formatDuration(estimatedRemainingSeconds)} />
        <WorkflowMetric label="Next row" value={nextRow} />
        <WorkflowMetric label="Max parallel" value={String(maxParallel)} />
        <WorkflowMetric label="Failures" value={String(failureCount)} />
        <WorkflowMetric label="Consecutive" value={String(Number(execution.consecutive_failures ?? 0))} />
      </div>
    </section>
  );
}

function WorkflowMetric({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-md border bg-background/60 px-2 py-1.5">
      <div className="text-muted-foreground">{label}</div>
      <div className="mt-0.5 truncate font-medium" title={value}>{value}</div>
    </div>
  );
}

export function ChatActivityPanel({
  className,
  threadId,
}: {
  className?: string;
  threadId: string;
}) {
  const { t } = useI18n();
  const { thread } = useThread();
  const { liveEvents } = useActivityContext();

  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [timelineCollapsed, setTimelineCollapsed] = useState(false);
  const [workflowStatus, setWorkflowStatus] = useState<WorkflowStatusResponse | null>(null);

  const refreshWorkflowStatus = useCallback(async () => {
    try {
      const response = await fetch(`${getBackendBaseURL()}${api.threads.workflowStatus(threadId)}`);
      if (!response.ok) {
        setWorkflowStatus(null);
        return;
      }
      const payload = (await response.json()) as WorkflowStatusResponse;
      setWorkflowStatus(payload.exists ? payload : null);
    } catch {
      setWorkflowStatus(null);
    }
  }, [threadId]);

  useEffect(() => {
    void refreshWorkflowStatus();
  }, [refreshWorkflowStatus, thread.isLoading, thread.messages.length]);

  useEffect(() => {
    const status = String(workflowStatus?.workflow?.execution?.status ?? "").toLowerCase();
    if (!workflowStatus?.exists || ["done", "stopped_failed_threshold"].includes(status)) {
      return;
    }
    const timer = window.setInterval(() => {
      void refreshWorkflowStatus();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [refreshWorkflowStatus, workflowStatus?.exists, workflowStatus?.workflow?.execution?.status]);

  const timeline = useMemo<TimelineItem[]>(() => {
    const persisted = asActivityTimelineState(thread.values.activity_timeline);
    const merged = mergeActivityEvents(persisted, liveEvents);

    const items: TimelineItem[] = merged.map((event, index) => {
      const title = event.line || "CapyHome is working on the next step...";
      const detail = event.tool_summary ?? undefined;
      return {
        id: event.id ?? `activity:${event.timestamp}:${index}`,
        timestamp: event.timestamp,
        order: index,
        kind: event.kind.includes("failed")
          ? "task_failed"
          : event.kind.includes("completed")
            ? "task_completed"
            : "task_started",
        icon: iconFromEventKind(event.kind, event.actor),
        title,
        detail,
        groupId: event.group_id ?? undefined,
      };
    });

    if (items.length > TIMELINE_MAX_ITEMS) {
      return items.slice(items.length - TIMELINE_MAX_ITEMS);
    }
    return items;
  }, [liveEvents, thread.values.activity_timeline]);

  const todos = useMemo(() => thread.values.todos ?? [], [thread.values.todos]);
  const phaseExecution = thread.values.phase_execution;
  const effectivePhaseExecution = useMemo(() => {
    if (todos.length === 0) {
      return phaseExecution;
    }

    // Keep Phase Progress aligned with the latest todo list, which is the most
    // up-to-date execution signal in some runs.
    const derivedResults = todos.map((todo, index) => {
      const existing = phaseExecution?.phase_results?.[index];
      const status = todo.status ?? existing?.status ?? "pending";
      return {
        phase_index: existing?.phase_index ?? index + 1,
        todo_id: existing?.todo_id ?? `todo-${index + 1}`,
        content: todo.content ?? existing?.content ?? `Todo ${index + 1}`,
        status,
        subagent_type: existing?.subagent_type,
        completed_at: existing?.completed_at,
      };
    });

    const hasDrift =
      !phaseExecution ||
      (phaseExecution.phase_results?.length ?? 0) !== todos.length ||
      derivedResults.some((result, index) => {
        const existing = phaseExecution.phase_results?.[index];
        if (!existing) return true;
        return existing.status !== result.status || existing.content !== result.content;
      });

    if (!hasDrift) {
      return phaseExecution;
    }

    return {
      ...(phaseExecution ?? {}),
      total_phases: todos.length,
      phase_results: derivedResults,
    };
  }, [phaseExecution, todos]);

  const hasInProgressPhase = (effectivePhaseExecution?.phase_results ?? []).some(
    (phase) => phase.status === "in_progress",
  );
  const mergedActivityForRunSignal = useMemo(
    () => mergeActivityEvents(asActivityTimelineState(thread.values.activity_timeline), liveEvents),
    [liveEvents, thread.values.activity_timeline],
  );
  const hasRecentLiveRunSignal = useMemo(() => {
    const now = Date.now() / 1000;
    return mergedActivityForRunSignal.some((event) => {
      const kind = (event.kind ?? "").toLowerCase();
      const isStartLike =
        kind.includes("start") ||
        kind.includes("running") ||
        kind.includes("work_");
      const isEndLike =
        kind.includes("completed") ||
        kind.includes("failed") ||
        kind.includes("timed_out") ||
        kind.includes("cancel");
      const recent = now - event.timestamp < 120;
      return isStartLike && !isEndLike && recent;
    });
  }, [mergedActivityForRunSignal]);

  const runState: "run" | "idle" =
    thread.isLoading && hasInProgressPhase && hasRecentLiveRunSignal ? "run" : "idle";

  const displayPhaseExecution = useMemo(() => {
    if (!effectivePhaseExecution) {
      return effectivePhaseExecution;
    }
    if (runState === "run") {
      return effectivePhaseExecution;
    }
    // If no live run signal exists, avoid showing stale in-progress rows from
    // persisted thread snapshots as currently running.
    return {
      ...effectivePhaseExecution,
      phase_results: (effectivePhaseExecution.phase_results ?? []).map((phase) => (
        phase.status === "in_progress"
          ? { ...phase, status: "pending" as const }
          : phase
      )),
    };
  }, [effectivePhaseExecution, runState]);

  const orderedTimeline = useMemo(
    () => [...timeline].sort((a, b) => a.timestamp !== b.timestamp ? a.timestamp - b.timestamp : a.order - b.order),
    [timeline],
  );

  const groupSizeMap = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of orderedTimeline) {
      if (item.groupId) counts.set(item.groupId, (counts.get(item.groupId) ?? 0) + 1);
    }
    return counts;
  }, [orderedTimeline]);

  const groupFirstItemId = useMemo(() => {
    const first = new Map<string, string>();
    for (const item of orderedTimeline) {
      if (item.groupId && !first.has(item.groupId)) first.set(item.groupId, item.id);
    }
    return first;
  }, [orderedTimeline]);

  const handleToggleGroup = useCallback((groupId: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(groupId)) next.delete(groupId);
      else next.add(groupId);
      return next;
    });
  }, []);

  const visibleTimeline = useMemo(() => {
    return orderedTimeline.filter((item) => {
      if (!item.groupId) return true;
      const isHeader = groupFirstItemId.get(item.groupId) === item.id;
      if (isHeader) return true;
      return !collapsedGroups.has(item.groupId);
    });
  }, [orderedTimeline, groupFirstItemId, collapsedGroups]);

  const trimmed = timeline.length >= TIMELINE_MAX_ITEMS;

  return (
    <div className={cn("flex h-full flex-col overflow-hidden", className)}>
      <div className="flex-1 overflow-y-auto">
        <div className="space-y-2 p-3">
          <WorkflowStatusCard workflowStatus={workflowStatus} />
          <PhaseProgress phaseExecution={displayPhaseExecution} runState={runState} />
          <section className="mt-2 space-y-2 rounded-lg border p-3">
            <header
              className="flex cursor-pointer items-center justify-between gap-2 text-sm font-medium"
              onClick={() => setTimelineCollapsed((prev) => !prev)}
            >
              <div className="flex items-center gap-2 text-sm font-medium">
                <Clock3Icon className="size-4" />
                {t.chatActivity.title}
                <Badge variant="secondary">{orderedTimeline.length}</Badge>
              </div>
              <ChevronUpIcon
                className={cn(
                  "text-muted-foreground size-4 transition-transform duration-300 ease-out",
                  timelineCollapsed ? "" : "rotate-180",
                )}
              />
            </header>
            {!timelineCollapsed && (
              <>
                {trimmed && (
                  <div className="text-muted-foreground rounded border px-2 py-1.5 text-xs">
                    {t.chatActivity.trimmedNotice(TIMELINE_MAX_ITEMS)}
                  </div>
                )}
                {visibleTimeline.length === 0 ? (
                  <div className="text-muted-foreground text-xs">{t.chatActivity.noActivity}</div>
                ) : (
                  <div>
                    {visibleTimeline.map((item) => {
                      const groupId = item.groupId;
                      const groupSize = groupId ? (groupSizeMap.get(groupId) ?? 1) : 1;
                      const isGroupHeader = groupId ? groupFirstItemId.get(groupId) === item.id : false;
                      const groupCollapsed = groupId ? collapsedGroups.has(groupId) : false;

                      return (
                        <TimelineItemRow
                          key={item.id}
                          item={item}
                          isGroupHeader={isGroupHeader}
                          groupSize={groupSize}
                          groupCollapsed={groupCollapsed}
                          onToggleGroup={handleToggleGroup}
                        />
                      );
                    })}
                  </div>
                )}
              </>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
