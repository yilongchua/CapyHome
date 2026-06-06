"use client";

import { ArrowUpRightIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { type PromptInputMessage } from "@/components/ai-elements/prompt-input";
import { Badge } from "@/components/ui/badge";
import { AdaptationNotice } from "@/components/workspace/adaptation-notice";
import { useDirectory } from "@/components/workspace/artifacts/context";
import {
  ChatBox,
  useThreadChat,
} from "@/components/workspace/chats";
import {
  InputBox,
  type InputBoxSubmitOptions,
} from "@/components/workspace/input-box";
import { MessageList } from "@/components/workspace/messages";
import { ThreadContext } from "@/components/workspace/messages/context";
import {
  PlanApprovalOverlay,
  PlanClarificationPopup,
  WorkflowApprovalOverlay,
} from "@/components/workspace/plan-approval-overlay";
import { QueuedMessageList } from "@/components/workspace/queued-message-list";
import { ThreadTitle } from "@/components/workspace/thread-title";
import { Welcome } from "@/components/workspace/welcome";
import { urlOfArtifact } from "@/core/artifacts/utils";
import { getBackendBaseURL } from "@/core/config";
import {
  type LiveGenerationNotice,
  useGenerationCompletions,
} from "@/core/generation/hooks";
import { useI18n } from "@/core/i18n/hooks";
import { hasPendingToolResultsInCurrentTurn } from "@/core/messages/utils";
import { useModels } from "@/core/models/hooks";
import { useLocalSettings } from "@/core/settings";
import {
  clearPendingChatLaunchPayload,
  getPendingChatLaunchPayload,
} from "@/core/threads/chat-launch-payload";
import type { ForkDraft } from "@/core/threads/fork";
import type { PlanAdaptedEvent, PlanCreatedEvent } from "@/core/threads/hooks";
import { useRenameThread, useThreadStream } from "@/core/threads/hooks";
import { useContextTokens } from "@/core/threads/use-context-tokens";
import { useRejoinRunningRun } from "@/core/threads/use-rejoin-running-run";
import { useThreadNotification } from "@/core/threads/use-thread-notification";
import { api } from "@/core/workspace-io/api";
import { useMountedFolder } from "@/core/workspace-io/hooks/use-mounted-folder";
import { useMountedFolderFiles } from "@/core/workspace-io/hooks/use-mounted-folder-files";
import { publishWorkspaceRefresh } from "@/core/workspace-refresh";
import { env } from "@/env";
import { useThemeAssets } from "@/hooks/use-theme-assets";
import { cn } from "@/lib/utils";

const EXECUTE_PLAN_INTENTS = new Set([
  "execute plan",
  "implement plan",
  "proceed",
  "proceed with plan",
  "run plan",
  "start plan",
]);

function normalizeIntent(text: string): string {
  return text.toLowerCase().trim().replace(/[.!?]+$/g, "");
}

function getMountedFolderName(
  fallbackPath: string | null | undefined,
): string | null {
  const normalized = fallbackPath?.trim().replace(/[\\/]+$/, "");
  if (!normalized) {
    return null;
  }
  const segments = normalized.split(/[\\/]/).filter(Boolean);
  return segments[segments.length - 1] ?? null;
}

function formatMountedThreadTitle(title: string): string {
  const trimmed = title.trim();
  const normalized = trimmed.startsWith("📁 ")
    ? trimmed.slice("📁 ".length).trim()
    : trimmed;
  return `📁 ${normalized}`;
}

function isMountPlaceholderTitle(title: string): boolean {
  return title === "" || title === "mount-drive";
}

function upsertNotice(
  notices: LiveGenerationNotice[],
  notice: LiveGenerationNotice,
): LiveGenerationNotice[] {
  const next = notices.filter((item) => item.id !== notice.id);
  return [...next, notice];
}

function parseErrorDetail(rawBody: string): string {
  const trimmed = rawBody.trim();
  if (!trimmed) {
    return "Unknown error";
  }
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (typeof parsed === "object" && parsed !== null && "detail" in parsed) {
      const detail = (parsed as { detail?: unknown }).detail;
      if (typeof detail === "string" && detail.trim()) {
        return detail.trim();
      }
    }
  } catch {
    // fall through to raw body
  }
  return trimmed;
}

function isThreadLockError(error: unknown): boolean {
  const message =
    error instanceof Error
      ? error.message
      : typeof error === "string"
        ? error
        : JSON.stringify(error);
  const normalized = message.toLowerCase();
  return (
    normalized.includes("http 409") ||
    normalized.includes("http 423") ||
    normalized.includes("in-flight runs") ||
    normalized.includes("temporarily locked")
  );
}

type ExecutePlanResponse = {
  acknowledged: boolean;
  status: "accepted" | "duplicate" | "conflict" | "failed";
  plan_status?: string | null;
  run_id?: string | null;
  assistant_id?: string | null;
};

type WorkflowStatusResponse = {
  exists: boolean;
  initialized?: boolean;
  workflow?: WorkflowJson | null;
};

type WorkflowJson = {
  source?: {
    row_count?: number;
  };
  runtime?: {
    workflow_json?: string;
    output_csv?: string;
  };
  execution?: {
    status?: string;
    completed_rows?: number;
    max_parallel?: number;
    failure_rows?: string[];
  };
};

type WorkflowExecuteResponse = {
  status: "accepted" | "done" | "stopped" | "stopped_failed_threshold" | "conflict" | "failed";
  completed_rows?: number;
  output_csv?: string | null;
  workflow?: WorkflowJson | null;
};

function getExecutePlanFailureMessage(result: ExecutePlanResponse): string | null {
  if (result.acknowledged && result.status !== "conflict" && result.status !== "failed") {
    return null;
  }
  if (result.status === "conflict") {
    if (result.plan_status) {
      return `Plan execution is blocked while the plan is ${result.plan_status}.`;
    }
    return "Plan execution is blocked. Please resolve the pending plan state and try again.";
  }
  if (result.status === "failed") {
    return "Plan execution failed. Please try again.";
  }
  return "Plan execution was not accepted. Please try again.";
}

export default function ChatPage() {
  const { threadId, isNewThread, setIsNewThread, isMock } = useThreadChat();

  // useThreadChat returns null on the first render of /workspace/chats/new
  // while it generates the UUID. Skip rendering the heavy chat content until
  // we have a stable id so its hooks don't mount twice.
  if (!threadId) {
    return null;
  }

  return (
    <ChatPageContent
      key={threadId}
      isMock={isMock}
      isNewThread={isNewThread}
      setIsNewThread={setIsNewThread}
      threadId={threadId}
    />
  );
}

function ChatPageContent({
  threadId,
  isNewThread,
  setIsNewThread,
  isMock,
}: {
  threadId: string;
  isNewThread: boolean;
  setIsNewThread: (value: boolean) => void;
  isMock: boolean;
}) {
  const { t } = useI18n();
  const router = useRouter();
  const [settings, setSettings] = useLocalSettings();
  const asset = useThemeAssets();
  const selectedModelName =
    typeof settings.context.model_name === "string"
      ? settings.context.model_name
      : undefined;
  const { models } = useModels();
  const { state: contextTokenState, onCompaction, onContextTokens } = useContextTokens({
    modelName: selectedModelName,
    models,
  });
  const { notices: generationNotices, artifactPaths: generationArtifacts } =
    useGenerationCompletions(threadId, { enabled: !isNewThread });
  const { data: mountedFolder } = useMountedFolder(threadId, { enabled: !isNewThread });
  const { data: mountedFolderFiles } = useMountedFolderFiles(
    threadId,
    Boolean(mountedFolder) && !isNewThread,
  );
  const mountedArtifacts = (mountedFolderFiles?.files ?? []).map(
    (file) => file.virtual_path,
  );
  const combinedArtifacts = Array.from(
    new Set([...generationArtifacts, ...mountedArtifacts]),
  );

  // Probe for an in-flight run so we can label resume situations. The
  // langgraph-sdk `useStream` already auto-joins via reconnectOnMount, so this
  // is observation-only — it lets the UI distinguish "fresh open" from
  // "resuming a still-running answer that started in another tab/session."
  const { onFinish } = useThreadNotification();

  const [planCreatedEvent, setPlanCreatedEvent] = useState<PlanCreatedEvent | null>(null);
  const [adaptationEvent, setAdaptationEvent] = useState<PlanAdaptedEvent | null>(null);
  const [forkDraft, setForkDraft] = useState<ForkDraft | null>(null);
  const [uiNotices, setUiNotices] = useState<LiveGenerationNotice[]>([]);
  const [pendingExecutePlan, setPendingExecutePlan] = useState(false);
  const [isExecutingPlan, setIsExecutingPlan] = useState(false);
  const [workflowStatus, setWorkflowStatus] = useState<WorkflowStatusResponse | null>(null);
  const [isExecutingWorkflow, setIsExecutingWorkflow] = useState(false);
  const [isSavingWorkflow, setIsSavingWorkflow] = useState(false);
  const [hiddenWorkflowPath, setHiddenWorkflowPath] = useState<string | null>(null);
  const [isClarifyingPlan, setIsClarifyingPlan] = useState(false);
  const [clarificationPendingOverride, setClarificationPendingOverride] = useState<boolean | null>(null);
  const [runPollBump, setRunPollBump] = useState(0);
  const [isMountBootstrapRunning, setIsMountBootstrapRunning] = useState(false);
  const [hiddenPlanEventKey, setHiddenPlanEventKey] = useState<string | null>(null);
  const [pendingMountPath, setPendingMountPath] = useState<string | null>(null);
  const suppressedAutoExecutePlanKeyRef = useRef<string | null>(null);
  const executePlanRetryCountRef = useRef(0);
  const finalizedMountedTitleRef = useRef<string | null>(null);
  const finalizingMountedTitleRef = useRef<string | null>(null);
  const mountBootstrapSentRef = useRef<string | null>(null);
  const renameThread = useRenameThread();
  const mountStatusNoticeId = `mount-status-${threadId}`;
  const mountedNoticeId = `mount-ready-${threadId}`;
  const mountBootstrapStorageKey = useMemo(
    () => `mount.bootstrap.sent.${threadId}`,
    [threadId],
  );

  const isInFlightRunConflict = useCallback((statusCode: number, rawBody: string): boolean => {
    if (statusCode === 409 || statusCode === 423) {
      return true;
    }
    const normalized = rawBody.toLowerCase();
    return normalized.includes("in-flight runs") || normalized.includes("temporarily locked");
  }, []);

  const refreshWorkflowStatus = useCallback(async () => {
    if (isNewThread) {
      setWorkflowStatus(null);
      return null;
    }
    try {
      const response = await fetch(`${getBackendBaseURL()}${api.threads.workflowStatus(threadId)}`);
      if (!response.ok) {
        setWorkflowStatus(null);
        return null;
      }
      const payload = (await response.json()) as WorkflowStatusResponse;
      setWorkflowStatus(payload);
      return payload;
    } catch {
      setWorkflowStatus(null);
      return null;
    }
  }, [isNewThread, threadId]);

  const planEventKey = useCallback((event: PlanCreatedEvent | null) => {
    if (!event) {
      return null;
    }
    return event.plan_id ?? `${event.title}:${event.todo_count}:${event.plan_path ?? "none"}`;
  }, []);

  const [thread, sendMessage, liveThinkingContent, queueControls] = useThreadStream({
    threadId: isNewThread ? undefined : threadId,
    context: settings.context,
    isMock,
    onContextTokens: ({ tokenCount }) => onContextTokens(tokenCount),
    onCompaction: onCompaction,
    onStart: () => {
      setIsNewThread(false);
      // Use router.replace so Next.js Router's internal state is updated.
      // This ensures subsequent "New Chat" clicks are treated as a real
      // cross-route navigation (actual-id → "new") rather than a no-op
      // same-path navigation, which was causing stale content to persist.
      router.replace(`/workspace/chats/${threadId}`);
    },
    onFinish,
    onPlanCreated: (event) => {
      setPlanCreatedEvent(event);
      setClarificationPendingOverride(null);
    },
    onPlanAdapted: (event) => setAdaptationEvent(event),
  });

  const { runningRun } = useRejoinRunningRun(isNewThread ? null : threadId, thread, {
    pollBump: runPollBump,
  });
  const hasPendingToolResults = useMemo(
    () => hasPendingToolResultsInCurrentTurn(thread.messages),
    [thread.messages],
  );

  const handleStop = useCallback(async () => {
    if (isExecutingWorkflow) {
      try {
        await fetch(`${getBackendBaseURL()}${api.threads.workflowStop(threadId)}`, {
          method: "POST",
        });
        await refreshWorkflowStatus();
      } catch (error) {
        console.error("Failed to stop workflow:", error);
      }
    }
    await queueControls.stop();
  }, [isExecutingWorkflow, queueControls, refreshWorkflowStatus, threadId]);
  const handleContextChange = useCallback(
    (nextContext: Parameters<typeof setSettings>[1]) => {
      setSettings("context", nextContext);
    },
    [setSettings],
  );
  const clarificationPending =
    clarificationPendingOverride ?? (
      (thread.values as { clarification_pending?: unknown }).clarification_pending === true ||
      planCreatedEvent?.clarification_pending === true ||
      thread.values.plan?.clarification_pending === true
    );

  useEffect(() => {
    void refreshWorkflowStatus();
  }, [refreshWorkflowStatus, thread.isLoading, thread.messages.length]);

  useEffect(() => {
    const handler = (event: Event) => {
      const custom = event as CustomEvent<{ threadId?: string }>;
      if (custom.detail?.threadId !== threadId) {
        return;
      }
      setHiddenWorkflowPath(workflowStatus?.workflow?.runtime?.workflow_json ?? "/mnt/user-data/workspace/runtime/workflow.json");
    };
    window.addEventListener("workflow-exit", handler as EventListener);
    return () => window.removeEventListener("workflow-exit", handler as EventListener);
  }, [threadId, workflowStatus?.workflow?.runtime?.workflow_json]);

  useEffect(() => {
    const handler = (event: Event) => {
      const custom = event as CustomEvent<{ threadId?: string }>;
      if (custom.detail?.threadId !== threadId) {
        return;
      }
      const run = async () => {
        try {
          const response = await fetch(`${getBackendBaseURL()}${api.threads.workflowRecover(threadId)}`, {
            method: "POST",
          });
          if (!response.ok) {
            throw new Error(parseErrorDetail(await response.text()));
          }
          const payload = (await response.json()) as WorkflowStatusResponse;
          setWorkflowStatus(payload);
          setHiddenWorkflowPath(null);
          toast.success("Workflow recovery ready. Review workflow.json and execute when ready.");
        } catch (error) {
          const detail = error instanceof Error ? error.message : "Unknown error";
          toast.error(`Failed to recover workflow. ${detail}`);
        }
      };
      void run();
    };
    window.addEventListener("workflow-recover", handler as EventListener);
    return () => window.removeEventListener("workflow-recover", handler as EventListener);
  }, [threadId]);

  // NOTE: activeClarificationIndex was used by the legacy per-index submit
  // flow (POST /plan/clarify with clarification_index). The new batch flow
  // (POST /clarify) drives tab selection from local state in the popup, so
  // the index is no longer needed at this layer.

  const normalizedClarifications = useMemo(() => {
    // Source order: (1) top-level ThreadState.clarifications (canonical, post-v6),
    // (2) plan_created SSE event payload, (3) nested plan.clarifications (legacy
    // v5 thread state). Only PENDING entries flow through to the UI — answered
    // entries stay in state for audit but don't render as open tabs.
    const topLevel = Array.isArray((thread.values as { clarifications?: unknown }).clarifications)
      ? ((thread.values as { clarifications?: unknown[] }).clarifications ?? [])
      : null;
    const clarificationsFromEvent = Array.isArray(planCreatedEvent?.clarifications)
      ? planCreatedEvent?.clarifications
      : null;
    const clarificationsFromPlan = Array.isArray(thread.values.plan?.clarifications)
      ? thread.values.plan?.clarifications
      : null;
    const source = topLevel ?? clarificationsFromEvent ?? clarificationsFromPlan ?? [];
    return source
      .filter((entry) => Boolean(entry) && typeof entry === "object")
      .map((entry, idx) => {
        const raw = entry as {
          id?: unknown;
          question?: unknown;
          options?: unknown;
          status?: unknown;
          answer?: unknown;
        };
        const id = typeof raw.id === "string" && raw.id.trim().length > 0
          ? String(raw.id)
          : `legacy-${idx}`;
        const question = typeof raw.question === "string" ? raw.question : "";
        const status: "pending" | "answered" = raw.status === "answered" ? "answered" : "pending";
        const answer = typeof raw.answer === "string" ? raw.answer : null;
        const options = Array.isArray(raw.options)
          ? raw.options
              .filter(
                (option): option is { label: string; recommended?: unknown; description?: unknown } =>
                  Boolean(option) &&
                  typeof option === "object" &&
                  typeof (option as { label?: unknown }).label === "string" &&
                  ((option as { label: string }).label).trim().length > 0,
              )
              .map((option) => ({
                label: String(option.label),
                recommended: option.recommended === true,
                description:
                  typeof option.description === "string" ? option.description : null,
              }))
          : [];
        return { id, question, options, status, answer };
      })
      .filter((entry) => entry.status === "pending");
  }, [
    (thread.values as { clarifications?: unknown }).clarifications,
    planCreatedEvent?.clarifications,
    thread.values.plan?.clarifications,
  ]);


  const effectivePlanCreatedEvent = useMemo(() => {
    if (planCreatedEvent) {
      return planCreatedEvent;
    }
    const plan = thread.values.plan;
    if (!plan) {
      return null;
    }
    const planStatus = String(plan.status ?? "").toLowerCase();
    const awaitingApproval = plan.awaiting_execution_approval === true || planStatus === "draft";
    // Show the Execute Plan popup only while a plan still needs explicit user
    // approval. Previously we also showed for "approved but idle", which could
    // repeatedly resurface the popup after users had already approved/executed.
    if (!awaitingApproval) {
      return null;
    }
    const todos = Array.isArray(thread.values.todos) ? thread.values.todos : [];
    const firstTodos = todos
      .map((todo) => String(todo.content ?? "").trim())
      .filter(Boolean)
      .slice(0, 5);
    const todoCount = todos.length > 0 ? todos.length : (plan.todo_ids?.length ?? 0);
    return {
      type: "plan_created" as const,
      plan_id: plan.plan_id,
      status: plan.status,
      auto_approved: false,
      clarification_pending: plan.clarification_pending === true,
      clarification_index: typeof plan.clarification_index === "number" ? plan.clarification_index : 0,
      clarifications: Array.isArray(plan.clarifications) ? plan.clarifications : [],
      title: String(plan.title ?? "Approved Plan"),
      summary: String(plan.summary ?? ""),
      domain: String(plan.domain ?? "generic"),
      todo_count: todoCount,
      first_todos: firstTodos,
      plan_path: plan.plan_path ?? null,
    };
  }, [planCreatedEvent, thread.values.plan, thread.values.todos, thread.values.work_mode?.active]);
  const effectivePlanEventKey = useMemo(
    () => planEventKey(effectivePlanCreatedEvent),
    [effectivePlanCreatedEvent, planEventKey],
  );
  const planReviewPath = useMemo(
    () => effectivePlanCreatedEvent?.plan_path ?? "/mnt/user-data/workspace/plan.md",
    [effectivePlanCreatedEvent?.plan_path],
  );

  useEffect(() => {
    if (!effectivePlanEventKey) {
      return;
    }
    if (hiddenPlanEventKey && hiddenPlanEventKey !== effectivePlanEventKey) {
      setHiddenPlanEventKey(null);
      setIsExecutingPlan(false);
    }
  }, [effectivePlanEventKey, hiddenPlanEventKey]);

  const handleExecutePlan = useCallback(() => {
    const run = async () => {
      try {
        if (isExecutingPlan) {
          return;
        }
        const eventKey = effectivePlanEventKey;
        if (eventKey && suppressedAutoExecutePlanKeyRef.current === eventKey) {
          return;
        }
        setSettings("context", { ...settings.context, mode: "work" });
        setIsExecutingPlan(true);
        setHiddenPlanEventKey(eventKey);
        setPlanCreatedEvent(null);
        if (thread.isLoading) {
          if (!pendingExecutePlan) {
            toast.message("Plan execution is queued and will start after the current run finishes.");
          }
          setPendingExecutePlan(true);
          return;
        }
        const response = await fetch(`${getBackendBaseURL()}${api.threads.executePlan(threadId)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            plan_id: effectivePlanCreatedEvent?.plan_id,
            auto_mode: settings.context.auto_mode === true,
          }),
        });
        if (!response.ok) {
          const raw = await response.text();
          if (isInFlightRunConflict(response.status, raw)) {
            executePlanRetryCountRef.current += 1;
            if (executePlanRetryCountRef.current <= 6) {
              setPendingExecutePlan(true);
              return;
            }
            throw new Error("Thread still has an active run. Please wait and retry Execute Plan.");
          }
          throw new Error(parseErrorDetail(raw));
        }
        const result = await response.json() as ExecutePlanResponse;
        const failureMessage = getExecutePlanFailureMessage(result);
        if (failureMessage) {
          throw new Error(failureMessage);
        }
        executePlanRetryCountRef.current = 0;
        setPendingExecutePlan(false);
        setIsExecutingPlan(false);
        // The Work Mode run is now a server-registered LangGraph run.
        // Join its SSE stream immediately so the chat starts updating without
        // waiting up to 15s for `useRunningRun` polling to discover it.
        if (typeof result.run_id === "string" && result.run_id) {
          void thread.joinStream(result.run_id).catch((error) => {
            console.warn("Failed to join Work Mode run stream directly:", error);
          });
        }
        // Bump the poller as a backup discovery path in case the direct join
        // races the run's registration window.
        setRunPollBump((value) => value + 1);
        publishWorkspaceRefresh(["runs", "threads", `thread:${threadId}`], {
          source: "execute-plan",
        });
      } catch (error) {
        // Keep popup open so user can retry execute.
        console.error("Failed to execute plan:", error);
        const detail = error instanceof Error ? error.message : "Unknown error";
        toast.error(`Failed to execute plan. ${detail}`);
        setPendingExecutePlan(false);
        setIsExecutingPlan(false);
        setHiddenPlanEventKey(null);
      }
    };
    void run();
  }, [
    effectivePlanCreatedEvent?.plan_id,
    effectivePlanEventKey,
    isExecutingPlan,
    isInFlightRunConflict,
    pendingExecutePlan,
    setSettings,
    settings.context,
    settings.context.auto_mode,
    thread,
    threadId,
    thread.isLoading,
  ]);

  const workflow = workflowStatus?.workflow ?? null;
  const workflowExecution = workflow?.execution;
  const workflowRuntime = workflow?.runtime;
  const workflowStatusValue = String(workflowExecution?.status ?? "").toLowerCase();
  const workflowPath = workflowRuntime?.workflow_json ?? "/mnt/user-data/workspace/runtime/workflow.json";
  const workflowCanExecute = Boolean(
    workflowStatus?.exists &&
    workflow &&
    !["done", "stopped_failed_threshold"].includes(workflowStatusValue),
  );
  const workflowRowsCompleted = Number(workflowExecution?.completed_rows ?? 0);
  const workflowRowsTotal = Number(workflow?.source?.row_count ?? 0);
  const workflowJsonText = useMemo(
    () => (workflow ? `${JSON.stringify(workflow, null, 2)}\n` : ""),
    [workflow],
  );

  const handleExecuteWorkflow = useCallback(() => {
    const run = async () => {
      if (isExecutingWorkflow) {
        return;
      }
      setIsExecutingWorkflow(true);
      try {
        const response = await fetch(`${getBackendBaseURL()}${api.threads.workflowExecuteNext(threadId)}`, {
          method: "POST",
        });
        if (!response.ok) {
          throw new Error(parseErrorDetail(await response.text()));
        }
        const result = (await response.json()) as WorkflowExecuteResponse;
        if (result.workflow) {
          setWorkflowStatus({ exists: true, initialized: true, workflow: result.workflow });
        } else {
          await refreshWorkflowStatus();
        }
        if (result.status === "done") {
          toast.success("Workflow complete.");
        } else if (result.status === "stopped_failed_threshold") {
          toast.error("Workflow stopped after 5 consecutive failures.");
        } else if (result.status === "stopped") {
          toast.message("Workflow stopped.");
        } else {
          toast.success(`Workflow batch complete. Rows completed: ${result.completed_rows ?? 0}.`);
        }
        publishWorkspaceRefresh(["threads", `thread:${threadId}`], {
          source: "workflow-execute",
        });
      } catch (error) {
        const detail = error instanceof Error ? error.message : "Unknown error";
        toast.error(`Failed to execute workflow. ${detail}`);
      } finally {
        setIsExecutingWorkflow(false);
      }
    };
    void run();
  }, [isExecutingWorkflow, refreshWorkflowStatus, threadId]);

  const handleKeepEditingWorkflow = useCallback(() => {
    setHiddenWorkflowPath(workflowPath);
  }, [workflowPath]);

  const handleSaveWorkflow = useCallback(
    async (nextWorkflowText: string) => {
      const trimmed = nextWorkflowText.trim();
      if (!trimmed || isSavingWorkflow) {
        return;
      }
      let parsed: unknown;
      try {
        parsed = JSON.parse(trimmed);
      } catch (error) {
        const detail = error instanceof Error ? error.message : "Invalid JSON";
        toast.error(`workflow.json is invalid. ${detail}`);
        return;
      }
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        toast.error("workflow.json must be a JSON object.");
        return;
      }
      setIsSavingWorkflow(true);
      try {
        const response = await fetch(`${getBackendBaseURL()}${api.threads.workflow(threadId)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ workflow: parsed }),
        });
        if (!response.ok) {
          throw new Error(parseErrorDetail(await response.text()));
        }
        const payload = (await response.json()) as WorkflowStatusResponse;
        setWorkflowStatus(payload);
        setHiddenWorkflowPath(null);
        toast.success("workflow.json saved.");
      } catch (error) {
        const detail = error instanceof Error ? error.message : "Unknown error";
        toast.error(`Failed to save workflow.json. ${detail}`);
      } finally {
        setIsSavingWorkflow(false);
      }
    },
    [isSavingWorkflow, threadId],
  );

  const handleSubmitClarifications = useCallback(
    (answers: { clarification_id: string; answer: string }[]) => {
      const run = async () => {
        try {
          if (answers.length === 0 || isClarifyingPlan) {
            return;
          }
          setIsClarifyingPlan(true);
          const response = await fetch(`${getBackendBaseURL()}/api/threads/${threadId}/clarify`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ answers }),
          });
          if (!response.ok) {
            const raw = await response.text();
            throw new Error(parseErrorDetail(raw));
          }
          const result = (await response.json()) as {
            clarification_pending?: boolean;
            applied?: number;
            unresolved?: number;
          };
          const pending = result.clarification_pending === true;
          setClarificationPendingOverride(pending);
          setPlanCreatedEvent((prev) =>
            prev
              ? {
                  ...prev,
                  clarification_pending: pending,
                }
              : prev,
          );
          publishWorkspaceRefresh(["threads", `thread:${threadId}`], {
            source: "plan-clarify",
          });
        } catch (error) {
          const detail = error instanceof Error ? error.message : "Unknown error";
          toast.error(`Failed to save clarifications. ${detail}`);
        } finally {
          setIsClarifyingPlan(false);
        }
      };
      void run();
    },
    [isClarifyingPlan, threadId],
  );

  // Dismissing the clarification panel (the ✕) is treated as "go with the
  // recommended choice for every question" — the same behaviour as auto mode.
  // For each pending question we submit the recommended option (falling back to
  // the first option, then a best-judgement sentinel when a question has no
  // options) so the run resumes immediately with sensible assumptions instead
  // of stalling on unanswered clarifications.
  const handleDismissClarifications = useCallback(() => {
    if (isClarifyingPlan || normalizedClarifications.length === 0) {
      return;
    }
    const answers = normalizedClarifications.map((c) => {
      const recommended = c.options.find((option) => option.recommended);
      const fallback = recommended ?? c.options[0];
      const answer =
        (fallback?.label ?? "").trim() ||
        "No preference — proceed with your best-judgement default.";
      return { clarification_id: c.id, answer };
    });
    handleSubmitClarifications(answers);
  }, [isClarifyingPlan, normalizedClarifications, handleSubmitClarifications]);

  // Auto-trigger Execute Plan when a plan is created and auto_mode is on.
  useEffect(() => {
    const handler = (event: Event) => {
      const custom = event as CustomEvent<{ threadId?: string; content?: string }>;
      const content = custom.detail?.content;
      if (!content || custom.detail?.threadId !== threadId) {
        return;
      }
      setUiNotices((prev) =>
        upsertNotice(prev, {
          id: mountedNoticeId,
          content,
        }));
    };
    window.addEventListener("chat-mounted-notice", handler as EventListener);
    return () => {
      window.removeEventListener("chat-mounted-notice", handler as EventListener);
    };
  }, [mountedNoticeId, threadId]);

  useEffect(() => {
    const payload = getPendingChatLaunchPayload();
    if (payload?.source !== "mount" || payload.targetThreadId !== threadId) {
      return;
    }
    const normalizedMountedPath = payload.mountedPath?.trim();
    setPendingMountPath(normalizedMountedPath && normalizedMountedPath.length > 0 ? normalizedMountedPath : null);
    setUiNotices((prev) =>
      upsertNotice(prev, {
        id: mountStatusNoticeId,
        content: payload.mountedPath
          ? `Mounting files from ${payload.mountedPath}...`
          : "Mounting files...",
      }));
    clearPendingChatLaunchPayload();
  }, [mountStatusNoticeId, threadId]);

  useEffect(() => {
    if (!pendingMountPath) {
      return;
    }
    const normalizedPath = pendingMountPath.trim();
    if (!normalizedPath) {
      return;
    }

    // React Strict Mode can remount components in development, which resets
    // refs and may dispatch this verification twice. Persist a per-thread/path
    // marker so we only send once for the same mounted target.
    const marker = `${threadId}:${normalizedPath}`;
    if (typeof window !== "undefined") {
      const sentMarker = window.sessionStorage.getItem(mountBootstrapStorageKey);
      if (sentMarker === marker) {
        mountBootstrapSentRef.current = threadId;
        return;
      }
      window.sessionStorage.setItem(mountBootstrapStorageKey, marker);
    }

    if (mountBootstrapSentRef.current === threadId) {
      return;
    }
    mountBootstrapSentRef.current = threadId;
    setIsMountBootstrapRunning(true);
    void sendMessage(
      threadId,
      {
        text: "Check if drive is mounted. Reply yes or no.",
        files: [],
      },
      undefined,
      { queued: true },
    ).catch((error) => {
      // Mount bootstrap verification is best-effort. During short overlap
      // windows, thread-level lock conflicts are expected and already retried.
      if (isThreadLockError(error)) {
        return;
      }
      console.error("Mount bootstrap verification failed:", error);
    }).finally(() => {
      setIsMountBootstrapRunning(false);
    });
  }, [mountBootstrapStorageKey, pendingMountPath, sendMessage, threadId]);

  useEffect(() => {
    const currentTitle = String(thread.values.title ?? "").trim();
    const mountSourcePath = pendingMountPath ?? mountedFolder ?? null;

    if (!mountSourcePath) {
      return;
    }
    if (finalizedMountedTitleRef.current === threadId) {
      return;
    }
    if (finalizingMountedTitleRef.current === threadId) {
      return;
    }
    if (!mountedFolderFiles) {
      return;
    }
    if (pendingMountPath && thread.messages.length === 0) {
      return;
    }
    if (isMountBootstrapRunning || thread.isLoading || runningRun) {
      return;
    }

    const derivedTitle = getMountedFolderName(
      mountedFolderFiles.folder_path ?? mountSourcePath,
    );
    if (!derivedTitle) {
      return;
    }

    const formattedTitle = formatMountedThreadTitle(derivedTitle);
    if (currentTitle === formattedTitle) {
      finalizedMountedTitleRef.current = threadId;
      setPendingMountPath(null);
      setUiNotices((prev) =>
        prev.filter((notice) => notice.id !== mountStatusNoticeId),
      );
      return;
    }
    if (!pendingMountPath && !isMountPlaceholderTitle(currentTitle) && currentTitle !== derivedTitle) {
      return;
    }

    void (async () => {
      finalizingMountedTitleRef.current = threadId;
      try {
        await renameThread.mutateAsync({
          threadId,
          title: formattedTitle,
        });
        finalizedMountedTitleRef.current = threadId;
        setPendingMountPath(null);
        setUiNotices((prev) =>
          prev.filter((notice) => notice.id !== mountStatusNoticeId),
        );
        toast.success(`Mounted folder ready: ${formattedTitle}`, {
          id: mountedNoticeId,
        });
      } catch (error) {
        if (isThreadLockError(error)) {
          return;
        }
        console.error("Failed to finalize mounted thread title:", error);
      } finally {
        if (finalizingMountedTitleRef.current === threadId) {
          finalizingMountedTitleRef.current = null;
        }
      }
    })();
  }, [isMountBootstrapRunning, mountStatusNoticeId, mountedFolder, mountedFolderFiles, mountedNoticeId, pendingMountPath, renameThread, runningRun, thread.isLoading, thread.messages.length, thread.values.title, threadId]);

  useEffect(() => {
    if (!pendingExecutePlan || thread.isLoading) {
      return;
    }
    const delayMs = Math.min(1200 * Math.max(1, executePlanRetryCountRef.current), 8000);
    const timer = window.setTimeout(() => {
      void handleExecutePlan();
    }, delayMs);
    return () => window.clearTimeout(timer);
  }, [handleExecutePlan, pendingExecutePlan, planCreatedEvent, thread.isLoading]);

  useEffect(() => {
    if (!settings.context.auto_mode || isExecutingWorkflow || !workflowCanExecute || thread.isLoading || workflowStatusValue === "stopped") {
      return;
    }
    if (workflowPath === hiddenWorkflowPath) {
      return;
    }
    const timer = window.setTimeout(() => {
      handleExecuteWorkflow();
    }, 250);
    return () => window.clearTimeout(timer);
  }, [
    handleExecuteWorkflow,
    hiddenWorkflowPath,
    isExecutingWorkflow,
    settings.context.auto_mode,
    thread.isLoading,
    workflowCanExecute,
    workflowPath,
    workflowRowsCompleted,
    workflowStatusValue,
  ]);

  const handleKeepEditingPlan = useCallback(() => {
    const eventKey = planEventKey(effectivePlanCreatedEvent);
    if (eventKey) {
      suppressedAutoExecutePlanKeyRef.current = eventKey;
      setHiddenPlanEventKey(eventKey);
    }
    setSettings("context", { ...settings.context, mode: "plan" });
    setIsExecutingPlan(false);
    executePlanRetryCountRef.current = 0;
    setPendingExecutePlan(false);
    setPlanCreatedEvent(null);
  }, [effectivePlanCreatedEvent, planEventKey, setSettings, settings.context]);

  const handleEditPlanSuggestion = useCallback(
    async (suggestion: string) => {
      const trimmed = suggestion.trim();
      if (!trimmed) {
        return;
      }
      setSettings("context", { ...settings.context, mode: "plan" });
      await sendMessage(
        threadId,
        {
          text: [
            "Apply the following user edits to the current draft plan:",
            "",
            trimmed,
            "",
            "Keep the plan in draft status (do not execute yet) and rewrite plan.md to match.",
          ].join("\n"),
          files: [],
        },
        undefined,
        { mode: "plan" },
      );
    },
    [sendMessage, setSettings, settings.context, threadId],
  );

  const handleRevisePlan = useCallback(() => {
    const blockedIds = adaptationEvent?.blocked_ids ?? [];
    const blockedContext = blockedIds.length > 0
      ? ` The following todos are blocked: ${blockedIds.join(", ")}.`
      : "";
    setAdaptationEvent(null);
    setSettings("context", { ...settings.context, mode: "plan" });
    void sendMessage(
      threadId,
      { text: `Revise the plan.${blockedContext} Please resolve the dependency issues.`, files: [] },
      undefined,
      { mode: "plan" },
    );
  }, [adaptationEvent, sendMessage, setSettings, settings.context, threadId]);

  const handleSubmitPlanRevision = useCallback(async (markdown: string) => {
    const currentPlanTitle = String(thread.values.plan?.title ?? "Draft Plan");
    setSettings("context", { ...settings.context, mode: "plan" });
    await sendMessage(
      threadId,
      {
        text: [
          `Revise the current draft plan titled "${currentPlanTitle}" to match the edited markdown below.`,
          "Treat this as the user's explicit plan edits.",
          "Requirements:",
          "1. Update the draft plan state and todo graph to align with this markdown.",
          "2. Keep the plan in draft status (do not execute yet).",
          "3. Rewrite plan artifacts (including plan.md) so preview and state stay in sync.",
          "<edited_plan_markdown>",
          markdown,
          "</edited_plan_markdown>",
        ].join("\n"),
        files: [],
      },
      undefined,
      { mode: "plan" },
    );
  }, [sendMessage, setSettings, settings.context, thread.values.plan?.title, threadId]);

  const handleSubmit = useCallback(
    (message: PromptInputMessage, options?: InputBoxSubmitOptions) => {
      const maybeIntent = normalizeIntent(message.text ?? "");
      const planStatus = String(thread.values.plan?.status ?? "").toLowerCase();
      const hasPlanReadyForExecution = planStatus === "draft" || planStatus === "approved";
      if (
        !thread.isLoading &&
        hasPlanReadyForExecution &&
        (!message.files || message.files.length === 0) &&
        EXECUTE_PLAN_INTENTS.has(maybeIntent)
      ) {
        handleExecutePlan();
        return;
      }
      const { extraContext: submitExtraContext, ...submitOptions } = options ?? {};
      const normalizedSubmitOptions = options ? submitOptions : undefined;
      void sendMessage(threadId, message, submitExtraContext, normalizedSubmitOptions);
    },
    [handleExecutePlan, sendMessage, thread.isLoading, thread.values.plan?.status, threadId],
  );

  const latestPersistedContextTokens = useMemo(
    () => {
      const metrics = thread.values.context_metrics;
      if (!metrics || typeof metrics !== "object") {
        return null;
      }
      const tokenCount = (metrics as { token_count?: unknown }).token_count;
      if (typeof tokenCount !== "number" || !Number.isFinite(tokenCount)) {
        return null;
      }
      const messageCount = (metrics as { message_count?: unknown }).message_count;
      return {
        tokenCount,
        messageCount:
          typeof messageCount === "number" && Number.isFinite(messageCount)
            ? messageCount
            : undefined,
      };
    },
    [thread.values.context_metrics],
  );

  const handoffBanner = useMemo(() => {
    const meta = thread.values.handoff_meta;
    if (!meta || typeof meta !== "object") {
      return null;
    }
    const handoffRoot = typeof meta.handoff_root_virtual_path === "string"
      ? meta.handoff_root_virtual_path
      : "";
    if (!handoffRoot) {
      return null;
    }
    const normalizedRoot = handoffRoot.replace(/\/$/, "");
    const handoffIndexPath = `${normalizedRoot}/index.md`;
    const sourceThreadId = typeof meta.source_thread_id === "string" ? meta.source_thread_id : "";
    return {
      handoffRoot,
      handoffIndexPath,
      sourceThreadId,
      href: urlOfArtifact({ filepath: handoffIndexPath, threadId, isMock }),
    };
  }, [isMock, thread.values.handoff_meta, threadId]);

  useEffect(() => {
    if (!latestPersistedContextTokens) {
      return;
    }
    onContextTokens(latestPersistedContextTokens.tokenCount);
  }, [latestPersistedContextTokens, onContextTokens]);

  return (
    <ThreadContext.Provider value={{ thread, isMock, forkDraft, setForkDraft }}>
      <ChatBox
        threadId={threadId}
        isNewThread={isNewThread}
        extraDirectoryFiles={combinedArtifacts}
        onSubmitPlanRevision={handleSubmitPlanRevision}
      >
        <div className="relative flex size-full min-h-0 justify-between">
          <header
            className={cn(
              "absolute top-0 right-0 left-0 z-30 flex h-12 shrink-0 items-center pr-4 pl-12",
              isNewThread
                ? "bg-background/0 backdrop-blur-none"
                : "bg-background/80 shadow-xs backdrop-blur",
            )}
          >
            <div className="flex w-full items-center gap-2 text-sm font-medium">
              <ThreadTitle threadId={threadId} thread={thread} />
              {runningRun && (thread.isLoading || isExecutingPlan) && (
                <span
                  className="text-muted-foreground rounded bg-amber-500/10 px-2 py-0.5 text-xs font-normal"
                  title={`Resuming run ${runningRun.runId}`}
                >
                  resuming…
                </span>
              )}
              {queueControls.queueLength > 0 && (
                <span className="text-muted-foreground rounded bg-blue-500/10 px-2 py-0.5 text-xs font-normal">
                  {queueControls.queueLength} queued
                </span>
              )}
            </div>
          </header>
          <main className="flex min-h-0 max-w-full grow flex-col">
            <div className="flex size-full justify-center">
              <div
                className="flex size-full flex-col bg-center bg-no-repeat"
                style={
                  isNewThread
                    ? {
                        backgroundImage:
                          settings.context.mode === "plan"
                            ? `url('${asset("plan-mode-chat.webp")}')`
                            : `url('${asset("work-mode-chat.webp")}')`,
                        backgroundSize: "cover",
                        backgroundPosition:
                          settings.context.mode === "plan"
                            ? "center 68%"
                            : "center bottom",
                      }
                    : undefined
                }
              >
                {handoffBanner && !isNewThread && (
                  <div className="px-4 pt-14 pb-2">
                    <div className="bg-background/80 flex items-center justify-between gap-3 rounded-lg border px-3 py-2 backdrop-blur">
                      <div className="flex min-w-0 items-center gap-2">
                        <Badge variant="secondary" className="shrink-0">Handoff</Badge>
                        <div className="min-w-0 text-sm">
                          <div className="truncate font-medium">
                            This thread was created from a handoff package.
                          </div>
                          <div className="text-muted-foreground truncate text-xs">
                            {handoffBanner.sourceThreadId
                              ? `Source thread: ${handoffBanner.sourceThreadId} · ${handoffBanner.handoffRoot}`
                              : handoffBanner.handoffRoot}
                          </div>
                        </div>
                      </div>
                      <a
                        href={handoffBanner.href}
                        target="_blank"
                        rel="noreferrer"
                        className="text-sm font-medium whitespace-nowrap underline underline-offset-4"
                      >
                        <span className="inline-flex items-center gap-1">
                          Open handoff
                          <ArrowUpRightIcon className="size-3.5" />
                        </span>
                      </a>
                    </div>
                  </div>
                )}
                <MessageList
                  className={cn("size-full", !isNewThread && "pt-10", handoffBanner && !isNewThread && "pt-0")}
                  threadId={threadId}
                  thread={thread}
                  liveNotices={[...generationNotices, ...uiNotices]}
                  liveThinkingContent={liveThinkingContent}
                  paddingBottom={
                    effectivePlanCreatedEvent && !isNewThread && effectivePlanEventKey !== hiddenPlanEventKey
                      ? clarificationPending && normalizedClarifications.length > 0
                        ? 400
                        : 280
                      : 160
                  }
                />
              </div>
            </div>
            <div className="absolute right-0 bottom-0 left-0 z-30 flex justify-center px-4 pb-2.5">
              <div
                className={cn(
                  "relative w-full",
                  isNewThread && "-translate-y-[calc(50vh-40px)]",
                  isNewThread
                    ? "max-w-[50vw]"
                    : "max-w-(--container-width-md)",
                )}
              >
                <div className="mb-2 flex flex-col gap-2">
                  <QueuedMessageList
                    items={queueControls.queueItems}
                    onSteer={queueControls.steerQueued}
                    onDismiss={queueControls.dismissQueued}
                  />
                </div>
                  <div className="relative">
                  {adaptationEvent && !isNewThread && (
                    <AdaptationNotice
                      event={adaptationEvent}
                      onRevisePlan={handleRevisePlan}
                      onDismiss={() => setAdaptationEvent(null)}
                    />
                  )}
                  <PlanReviewBinding
                    planPath={planReviewPath}
                    planEventKey={effectivePlanEventKey}
                    active={Boolean(
                      effectivePlanCreatedEvent && !isNewThread && effectivePlanEventKey !== hiddenPlanEventKey,
                    )}
                  />
                  {/*
                    Mount the popup whenever clarifications are pending — no
                    longer gated on the plan-approval event. Work-mode runs
                    that surface ask_user_for_clarification mid-execution show
                    the same tabbed panel as plan-mode draft clarifications.
                  */}
                  {!isNewThread && clarificationPending && normalizedClarifications.length > 0 && (
                    <PlanClarificationPopup
                      clarifications={normalizedClarifications}
                      onSubmit={handleSubmitClarifications}
                      onDismiss={handleDismissClarifications}
                      isSubmitting={isClarifyingPlan}
                    />
                  )}
                  <InputBox
                    className={cn("bg-background/5 w-full")}
                    isNewThread={isNewThread}
                    threadId={threadId}
                    newChatHref="/workspace/chats/new"
                    autoFocus={isNewThread}
                    status={thread.isLoading || hasPendingToolResults || isExecutingWorkflow ? "streaming" : "ready"}
                    context={settings.context}
                    extraHeader={
                      isNewThread && (
                        <Welcome
                          mode={settings.context.mode}
                        />
                      )
                    }
                    overlay={
                      effectivePlanCreatedEvent &&
                      !isNewThread &&
                      effectivePlanEventKey !== hiddenPlanEventKey &&
                      !clarificationPending &&
                      !(effectivePlanCreatedEvent.auto_approved && settings.context.auto_mode === true) ? (
                        <PlanApprovalOverlay
                          planPath={planReviewPath}
                          onExecute={handleExecutePlan}
                          onCancel={handleKeepEditingPlan}
                          onSubmitEdit={handleEditPlanSuggestion}
                          isExecuting={isExecutingPlan}
                        />
                      ) : workflowCanExecute &&
                        !isNewThread &&
                        workflowPath !== hiddenWorkflowPath ? (
                        <WorkflowApprovalOverlay
                          workflowPath={workflowPath}
                          workflowText={workflowJsonText}
                          onExecute={handleExecuteWorkflow}
                          onCancel={handleKeepEditingWorkflow}
                          onSaveWorkflow={handleSaveWorkflow}
                          isExecuting={isExecutingWorkflow}
                          isSavingWorkflow={isSavingWorkflow}
                          completedRows={workflowRowsCompleted}
                          totalRows={workflowRowsTotal}
                        />
                      ) : null
                    }
                    disabled={env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true"}
                    contextTokenState={contextTokenState}
                    onContextChange={handleContextChange}
                    onCompaction={onCompaction}
                    onSubmit={handleSubmit}
                    onStop={handleStop}
                  />
                </div>
                {env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" && (
                  <div className="text-muted-foreground/67 w-full translate-y-12 text-center text-xs">
                    {t.common.notAvailableInDemoMode}
                  </div>
                )}
              </div>
            </div>
          </main>
        </div>
      </ChatBox>
    </ThreadContext.Provider>
  );
}

function PlanReviewBinding({
  planPath,
  planEventKey,
  active,
}: {
  planPath: string;
  planEventKey: string | null;
  active: boolean;
}) {
  const { select, setOpen } = useDirectory();
  const lastOpenedKeyRef = useRef<string | null>(null);

  useEffect(() => {
    if (!active || !planEventKey) {
      return;
    }
    if (lastOpenedKeyRef.current === planEventKey) {
      return;
    }
    lastOpenedKeyRef.current = planEventKey;
    select(planPath);
    setOpen(true);
  }, [active, planEventKey, planPath, select, setOpen]);

  return null;
}
