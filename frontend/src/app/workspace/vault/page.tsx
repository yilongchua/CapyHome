"use client";

import {
  CheckIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ClockIcon,
  CopyIcon,
  DownloadIcon,
  FileTextIcon,
  FolderIcon,
  Loader2Icon,
  PanelRightCloseIcon,
  PanelRightOpenIcon,
  PlayIcon,
  RefreshCwIcon,
  SaveIcon,
  SparklesIcon,
  SquareIcon,
  Trash2Icon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { usePanelRef } from "react-resizable-panels";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { Textarea } from "@/components/ui/textarea";
import { MarkdownContent } from "@/components/workspace/messages/markdown-content";
import { VaultEntityBrowser } from "@/components/workspace/vault/vault-entity-browser";
import {
  WorkspaceBody,
  WorkspaceContainer,
  WorkspaceHeader,
} from "@/components/workspace/workspace-container";
import {
  useCancelVaultIngest,
  useCancelVaultLint,
  useDeleteVaultFile,
  useLintVault,
  useRefreshVaultExplorer,
  useSaveVaultFile,
  useStartVaultIngest,
  useVaultExplorer,
  useVaultExplorerChildren,
  useVaultFile,
  useVaultIngestStatus,
  useVaultLintStatus,
  useVaultStatus,
} from "@/core/control-plane";
import type { VaultExplorerFileNode, VaultExplorerResponse, VaultLintResponse } from "@/core/control-plane";
import { useI18n } from "@/core/i18n/hooks";
import { useModels } from "@/core/models/hooks";
import { streamdownPlugins } from "@/core/streamdown";

import { useIdleAutoRun } from "./use-idle-auto-run";

type TreeNode = {
  name: string;
  path: string;
  kind: "directory" | "file";
  children?: TreeNode[];
  hasChildren?: boolean;
  childCount?: number;
};

const TREE_CHILDREN_PAGE_SIZE = 150;

function toTreeNodes(nodes: VaultExplorerFileNode[] | undefined): TreeNode[] {
  return (nodes ?? []).map((node) => ({
    name: node.name,
    path: node.path,
    kind: node.kind === "directory" ? "directory" : "file",
    // Backend payloads are shallow (max_depth=1) so nested children never arrive;
    // each level is fetched on demand. The recursive mapping is dead — kept for reference.
    // children: node.children ? toTreeNodes(node.children) : undefined,
    children: undefined,
    hasChildren: node.has_children,
    childCount: node.child_count ?? undefined,
  }));
}

/**
 * Renders a single vault tree node. Directories lazy-load their children from
 * the backend on expand (the explorer payload is shallow), and large folders
 * render incrementally to keep the main thread responsive.
 */
function VaultTreeNode({
  node,
  depth,
  expandedPaths,
  onToggle,
  selectedPath,
  onSelectFile,
  refetchInterval,
}: {
  node: TreeNode;
  depth: number;
  expandedPaths: Record<string, boolean>;
  onToggle: (path: string) => void;
  selectedPath: string | null;
  onSelectFile: (path: string) => void;
  refetchInterval: number | false;
}) {
  const isDir = node.kind === "directory";
  const isOpen = isDir && Boolean(expandedPaths[node.path]);
  const hasChildren = isDir && (node.hasChildren ?? (node.children?.length ?? 0) > 0);

  const { children: lazyChildren, isLoading } = useVaultExplorerChildren(node.path, {
    enabled: isDir && isOpen,
    // Refresh expanded folders so background writes (e.g. autoresearch) surface.
    refetchInterval,
  });

  const children = useMemo<TreeNode[]>(() => {
    if (!isDir || !isOpen) return [];
    return toTreeNodes(lazyChildren ?? undefined);
    // Backend is shallow, so there are never inline children to fall back to while
    // the request is in flight — the loading spinner below covers that. (Dead fallback:)
    // return fetched.length > 0 ? fetched : (node.children ?? []);
  }, [isDir, isOpen, lazyChildren]);

  const [visibleCount, setVisibleCount] = useState(TREE_CHILDREN_PAGE_SIZE);
  const visibleChildren = children.slice(0, visibleCount);
  const remaining = children.length - visibleChildren.length;

  return (
    <div>
      <button
        type="button"
        className="flex w-full items-center gap-1 rounded px-2 py-1 text-left hover:bg-muted"
        style={{ paddingLeft: `${8 + depth * 14}px` }}
        onClick={() => {
          if (isDir) {
            onToggle(node.path);
            return;
          }
          onSelectFile(node.path);
        }}
      >
        {isDir ? (
          <>
            {hasChildren ? (
              isOpen ? <ChevronDownIcon className="size-3.5" /> : <ChevronRightIcon className="size-3.5" />
            ) : (
              <span className="inline-block size-3.5" />
            )}
            <FolderIcon className="size-3.5" />
          </>
        ) : (
          <>
            <span className="inline-block size-3.5" />
            <FileTextIcon className="size-3.5" />
          </>
        )}
        <span className={selectedPath === node.path ? "font-medium" : ""}>{node.name}</span>
        {isDir && typeof node.childCount === "number" && node.childCount > 0 ? (
          <span className="text-muted-foreground ml-auto pl-2 text-[10px] tabular-nums">{node.childCount}</span>
        ) : null}
      </button>
      {isDir && isOpen ? (
        <>
          {isLoading && children.length === 0 ? (
            <div
              className="text-muted-foreground flex items-center gap-1 px-2 py-1"
              style={{ paddingLeft: `${8 + (depth + 1) * 14}px` }}
            >
              <Loader2Icon className="size-3.5 animate-spin" />
              <span>Loading…</span>
            </div>
          ) : null}
          {visibleChildren.map((child) => (
            <VaultTreeNode
              key={child.path}
              node={child}
              depth={depth + 1}
              expandedPaths={expandedPaths}
              onToggle={onToggle}
              selectedPath={selectedPath}
              onSelectFile={onSelectFile}
              refetchInterval={refetchInterval}
            />
          ))}
          {remaining > 0 ? (
            <button
              type="button"
              className="text-muted-foreground hover:text-foreground px-2 py-1 text-left text-[11px]"
              style={{ paddingLeft: `${8 + (depth + 1) * 14}px` }}
              onClick={() => setVisibleCount((current) => current + TREE_CHILDREN_PAGE_SIZE)}
            >
              Show {Math.min(remaining, TREE_CHILDREN_PAGE_SIZE)} more ({remaining} hidden)
            </button>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function formatEtaLabel(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} secs`;
  const totalMinutes = Math.round(seconds / 60);
  if (totalMinutes < 60) return `${totalMinutes} min${totalMinutes === 1 ? "" : "s"}`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return minutes === 0 ? `${hours}h` : `${hours}h ${minutes}min${minutes === 1 ? "" : "s"}`;
}

// Dead: the backend returns every level already sorted (directories first, then
// name) for both the shallow root and each /explorer/children response, so the
// client-side re-sort is redundant. Kept commented in case backend ordering changes.
// function sortTree(nodes: TreeNode[]): TreeNode[] {
//   return [...nodes]
//     .sort((a, b) => {
//       if (a.kind !== b.kind) return a.kind === "directory" ? -1 : 1;
//       return a.name.localeCompare(b.name);
//     })
//     .map((item) => ({
//       ...item,
//       children: item.children ? sortTree(item.children) : undefined,
//     }));
// }

export default function VaultPage() {
  const { t } = useI18n();
  const { vaultStatus } = useVaultStatus({ refetchInterval: 20_000 });
  const { ingestStatus } = useVaultIngestStatus();
  // The big 3 MB explorer payload is gone (shallow + lazy), so a slow baseline poll
  // is cheap and keeps the tree live during background writes (autoresearch,
  // pipelines) that don't flip the manual ingest status; faster while ingesting.
  const ingesting = ingestStatus?.status === "running";
  const treeRefetchInterval = ingesting ? 10_000 : 30_000;
  const { explorer, isLoading: explorerLoading } = useVaultExplorer({
    refetchInterval: treeRefetchInterval,
    listenForRefreshEvents: true,
  });
  const refreshExplorer = useRefreshVaultExplorer();
  const saveVaultFile = useSaveVaultFile();
  const deleteVaultFile = useDeleteVaultFile();
  const startIngest = useStartVaultIngest();
  const cancelIngest = useCancelVaultIngest();
  const lintVaultMutation = useLintVault();
  const cancelLint = useCancelVaultLint();
  const [selectedWorkers, setSelectedWorkers] = useState<number>(1);
  const [selectedIngestModel, setSelectedIngestModel] = useState<string | null>(null);
  const [selectedLintWorkers, setSelectedLintWorkers] = useState<number>(3);
  const [selectedLintModel, setSelectedLintModel] = useState<string | null>(null);
  const { models: availableModels } = useModels();
  const lintModelLabel =
    availableModels.find((m) => m.name === selectedLintModel)?.display_name ??
    selectedLintModel ??
    "Default model";
  const [lintPreview, setLintPreview] = useState<VaultLintResponse | null>(null);
  const lintTotalToRemove =
    (lintPreview?.entities.flagged.length ?? 0) + (lintPreview?.concepts.flagged.length ?? 0);
  // Poll lint progress unconditionally so a job started by another tab, the
  // idle auto-run, or before a page reload is still surfaced — the server job
  // status is authoritative, not this tab's mutation state.
  const { lintStatus } = useVaultLintStatus();
  // Live label for an in-flight LLM judge run. "Running" is true when the
  // server reports a running job OR this tab's use_llm mutation is still
  // pending (covers the brief window before the first status poll lands).
  const lintJudgeRunning =
    lintStatus?.status === "running" ||
    (lintVaultMutation.isPending && Boolean(lintVaultMutation.variables?.useLlm));
  // Prefer this tab's mutation variables (most specific), then fall back to the
  // server job's reported workers/model so the label is accurate after reload.
  const lintRunWorkers =
    lintVaultMutation.variables?.workers ?? lintStatus?.workers ?? selectedLintWorkers;
  const lintRunModelName = lintVaultMutation.variables?.modelName ?? lintStatus?.model ?? null;
  const lintRunModelLabel =
    availableModels.find((m) => m.name === lintRunModelName)?.display_name ??
    lintRunModelName ??
    "Default model";
  const lintProgressLabel = (() => {
    if (!lintJudgeRunning) return "";
    const processed = lintStatus?.processed ?? 0;
    const total = lintStatus?.total ?? 0;
    if (total <= 0) return ""; // before the first batch reports
    const batchSize = lintStatus?.batch_size && lintStatus.batch_size > 0 ? lintStatus.batch_size : 20;
    const totalBatches = Math.max(1, Math.ceil(total / batchSize));
    const currentBatch = Math.min(totalBatches, Math.ceil(processed / batchSize));
    const startedAt = lintStatus?.started_at ? Date.parse(lintStatus.started_at) : NaN;
    let eta = "";
    if (!Number.isNaN(startedAt) && processed > 0 && processed < total) {
      const elapsedSec = Math.max(0, (Date.now() - startedAt) / 1000);
      const etaSec = (elapsedSec / processed) * (total - processed);
      eta = ` · ~${formatEtaLabel(etaSec)} remaining`;
    }
    return `batch ${currentBatch}/${totalBatches} · ${processed}/${total} pages${eta}`;
  })();
  const ingestRunning = ingestStatus?.status === "running" || startIngest.isPending;
  const cancelRequested = Boolean(ingestStatus?.cancel_requested) || cancelIngest.isPending;
  const activeWorkers = ingestStatus?.workers_active ?? ingestStatus?.workers_requested ?? 0;

  // The idle clock depends on client-only state (localStorage) and is a Radix
  // dropdown (consumes useId). Render it only after mount so SSR and the first
  // client render stay identical — otherwise its useId perturbs every dropdown
  // below it and trips a hydration mismatch.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  // Passive idle auto-run (clock icon). 0 = off; persisted in localStorage.
  // Auto-runs always pin 1 worker + default model; auto-lint prunes (2 passes).
  const [idleMinutes, setIdleMinutes] = useState<number>(0);
  useEffect(() => {
    const stored = Number(localStorage.getItem("vault-idle-autorun-min") ?? "0");
    if (!Number.isNaN(stored)) setIdleMinutes(stored);
  }, []);
  const updateIdleMinutes = (n: number) => {
    setIdleMinutes(n);
    try {
      localStorage.setItem("vault-idle-autorun-min", String(n));
    } catch {
      /* ignore quota / unavailable storage */
    }
  };
  useIdleAutoRun({
    idleMinutes,
    isBusy: ingestRunning || lintVaultMutation.isPending,
    ingestActive: ingestRunning,
    onAutoIngest: () => {
      if (ingestRunning) return;
      startIngest.mutate(
        { workers: 1, modelName: null },
        {
          onSuccess: (payload) => {
            if (payload.accepted !== false) {
              toast.message("Idle auto-run: ingest started (1 worker, default model).");
            }
          },
        },
      );
    },
    onAutoLint: () => {
      if (lintVaultMutation.isPending || ingestRunning) return;
      // Up to 2 prune passes: pass 1 removes flagged pages; pass 2 catches any
      // pages newly orphaned by pass 1's removals. Each pass is preview (LLM
      // judge) -> commit (apply the flagged slugs). Default model, 1 worker.
      // Fire-and-forget async IIFE so the callback stays () => void.
      void (async () => {
        try {
          for (let pass = 1; pass <= 2; pass++) {
          toast.message(`Idle auto-run: lint pass ${pass}/2 (judging, default model)…`);
          const preview = await lintVaultMutation.mutateAsync({
            dryRun: true,
            useLlm: true,
            workers: 1,
            modelName: null,
          });
          setLintPreview(preview);
          if (preview.cancelled) {
            toast.message("Idle auto-run: lint cancelled.");
            return;
          }
          const entitySlugs = preview.entities.flagged.map((f) => f.slug);
          const conceptSlugs = preview.concepts.flagged.map((f) => f.slug);
          if (entitySlugs.length === 0 && conceptSlugs.length === 0) {
            if (pass === 1) toast.message("Idle auto-run: nothing to prune.");
            break;
          }
          const result = await lintVaultMutation.mutateAsync({
            dryRun: false,
            entitySlugs,
            conceptSlugs,
          });
          const removed = (result.entities.removed ?? 0) + (result.concepts.removed ?? 0);
          toast.success(`Idle auto-run: lint pass ${pass}/2 pruned ${removed} page${removed === 1 ? "" : "s"}.`);
          }
        } catch (error) {
          toast.error(error instanceof Error ? error.message : "Idle auto-run lint failed.");
        }
      })();
    },
  });
  const ingestEtaLabel = (() => {
    if (ingestStatus?.status !== "running") return "";
    const total = ingestStatus.total || 0;
    const current = ingestStatus.current_index || 0;
    const startedAt = ingestStatus.started_at ? Date.parse(ingestStatus.started_at) : NaN;
    if (!total || current < 1 || Number.isNaN(startedAt)) return "";
    const elapsedSec = Math.max(0, (Date.now() - startedAt) / 1000);
    const remaining = Math.max(0, total - current);
    if (remaining === 0) return "";
    const etaSec = (elapsedSec / current) * remaining;
    return formatEtaLabel(etaSec);
  })();
  const ingestProgressLabel = (() => {
    if (!ingestStatus) return "";
    if (ingestStatus.status === "running") {
      const total = ingestStatus.total || 0;
      const current = ingestStatus.current_index || 0;
      const title = (ingestStatus.current_title || "").trim();
      const truncated = title.length > 48 ? `${title.slice(0, 48)}…` : title;
      const totalLabel = total > 0 ? String(total) : "?";
      const workersLabel = activeWorkers > 1 ? ` · ${activeWorkers} workers` : "";
      const base = `Source ${current}/${totalLabel} ingesting${truncated ? ` ${truncated}` : "..."}${workersLabel}`;
      if (cancelRequested) return `${base} · stopping (finishing current item, requeuing the rest)…`;
      return ingestEtaLabel ? `${base} · ~${ingestEtaLabel} remaining` : base;
    }
    if (ingestStatus.status === "success" && ingestStatus.processed > 0) {
      return `Last ingest: updated ${ingestStatus.updated} / ${ingestStatus.processed}`;
    }
    if (ingestStatus.status === "cancelled") {
      return `Ingest stopped at ${ingestStatus.current_index}/${ingestStatus.total}`;
    }
    if (ingestStatus.status === "failed" && ingestStatus.last_error) {
      return `Ingest failed: ${ingestStatus.last_error}`;
    }
    return "";
  })();
  const [rootCollapsed, setRootCollapsed] = useState(false);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [previewTab, setPreviewTab] = useState<"preview" | "entities">("entities");
  const [editorCollapsed, setEditorCollapsed] = useState(false);
  const editorPanelRef = usePanelRef();
  const [expandedPaths, setExpandedPaths] = useState<Record<string, boolean>>({});
  const { vaultFile, isLoading: vaultFileLoading, error: vaultFileError } = useVaultFile(selectedPath);
  const [editableContent, setEditableContent] = useState("");
  const effectiveExplorer: VaultExplorerResponse | null = explorer;

  const filesTree = useMemo(
    () => toTreeNodes(effectiveExplorer?.files),
    [effectiveExplorer?.files],
  );

  const togglePath = (path: string) => {
    setExpandedPaths((current) => ({ ...current, [path]: !current[path] }));
  };

  const handleSelectFile = (path: string) => {
    setSelectedPath(path);
    setPreviewTab("preview");
  };

  useEffect(() => {
    document.title = `${t.pages.vault} - ${t.pages.appName}`;
  }, [t.pages.appName, t.pages.vault]);

  useEffect(() => {
    setEditableContent(vaultFile?.content ?? "");
  }, [vaultFile?.content, vaultFile?.path]);

  return (
    <WorkspaceContainer>
      <WorkspaceHeader
        rightSlot={
          mounted ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  size="sm"
                  variant={idleMinutes > 0 ? "default" : "outline"}
                  className="px-2"
                  title={
                    idleMinutes > 0
                      ? `Auto Ingest & Lint: after ${idleMinutes} min idle → ingest, then prune (2 passes, applies removals)`
                      : "Auto Ingest & Lint: off"
                  }
                  aria-label="Auto Ingest & Lint settings"
                >
                  <ClockIcon className="size-4" />
                  {idleMinutes > 0 ? <span className="ml-1 text-xs">{idleMinutes}m</span> : null}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-52">
                <DropdownMenuLabel>Auto Ingest &amp; Lint</DropdownMenuLabel>
                <DropdownMenuSeparator />
                {[
                  { n: 0, label: "Off" },
                  { n: 15, label: "15 minutes" },
                  { n: 30, label: "30 minutes" },
                ].map((opt) => (
                  <DropdownMenuItem key={opt.n} onClick={() => updateIdleMinutes(opt.n)}>
                    <CheckIcon
                      className={`mr-2 size-3.5 ${idleMinutes === opt.n ? "opacity-100" : "opacity-0"}`}
                    />
                    {opt.label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          ) : null
        }
      />
      <WorkspaceBody>
        <div className="flex size-full flex-col overflow-hidden p-6">
          <div className="grid min-h-0 flex-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            <div className="flex min-h-0 flex-col md:col-span-2 xl:col-span-3">
              <div className="mb-3 flex shrink-0 flex-row items-center justify-between gap-3">
                <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
                  <h2 className="text-base font-semibold">
                    Knowledge Vault · Sources {Number(vaultStatus?.counts?.sources_total ?? 0)} · Queued{" "}
                    {Number(vaultStatus?.counts?.queued_search_results ?? 0)}
                  </h2>
                  {ingestProgressLabel ? (
                    <span
                      className={`flex items-center gap-1 truncate text-xs ${
                        ingestStatus?.status === "failed" ? "text-destructive" : "text-muted-foreground"
                      }`}
                      title={ingestStatus?.current_title ?? ingestProgressLabel}
                    >
                      {ingestRunning ? <Loader2Icon className="size-3.5 animate-spin" /> : null}
                      <span className="truncate">· {ingestProgressLabel}</span>
                    </span>
                  ) : null}
                  {lintJudgeRunning ? (
                    <span
                      className="flex items-center gap-1 truncate text-xs text-muted-foreground"
                      title={`LLM judge — ${lintRunWorkers} worker${
                        lintRunWorkers === 1 ? "" : "s"
                      } · ${lintRunModelLabel}${lintProgressLabel ? ` · ${lintProgressLabel}` : ""}`}
                    >
                      <Loader2Icon className="size-3.5 animate-spin" />
                      <span className="truncate">
                        · Linting…{lintProgressLabel ? ` ${lintProgressLabel}` : ""} · {lintRunModelLabel}
                      </span>
                    </span>
                  ) : null}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <div className="inline-flex items-stretch">
                    <Button
                      size="sm"
                      variant="outline"
                      className="rounded-r-none border-r-0"
                      onClick={() => {
                        if (ingestRunning) {
                          toast.message("Ingest already running.");
                          return;
                        }
                        startIngest.mutate(
                          { workers: selectedWorkers, modelName: selectedIngestModel },
                          {
                            onSuccess: (payload) => {
                              if (payload.accepted === false) {
                                toast.message(payload.message ?? "Vault ingest already running.");
                              } else {
                                toast.success(
                                  payload.message ??
                                    `Vault ingest started with ${selectedWorkers} worker${
                                      selectedWorkers === 1 ? "" : "s"
                                    }.`,
                                );
                              }
                            },
                            onError: (error) => toast.error(error.message),
                          },
                        );
                      }}
                      disabled={ingestRunning}
                    >
                      {ingestRunning ? (
                        <Loader2Icon className="mr-2 size-4 animate-spin" />
                      ) : (
                        <PlayIcon className="mr-2 size-4" />
                      )}
                      {ingestRunning
                        ? "Ingesting..."
                        : `Run Ingest${selectedWorkers > 1 ? ` (${selectedWorkers}×)` : ""}`}
                    </Button>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          size="sm"
                          variant="outline"
                          className="rounded-l-none px-2"
                          disabled={ingestRunning}
                          title="Choose workers and analysis model"
                          aria-label="Choose workers and analysis model"
                        >
                          <ChevronDownIcon className="size-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="max-h-80 overflow-y-auto">
                        <DropdownMenuLabel>Parallel workers</DropdownMenuLabel>
                        {[1, 2, 3].map((n) => (
                          <DropdownMenuItem
                            key={n}
                            onClick={() => setSelectedWorkers(n)}
                          >
                            <CheckIcon
                              className={`mr-2 size-3.5 ${
                                selectedWorkers === n ? "opacity-100" : "opacity-0"
                              }`}
                            />
                            {n} worker{n === 1 ? "" : "s"}
                            {n === 1 ? " (sequential)" : ""}
                          </DropdownMenuItem>
                        ))}
                        <DropdownMenuSeparator />
                        <DropdownMenuLabel>Analysis model</DropdownMenuLabel>
                        <DropdownMenuItem onClick={() => setSelectedIngestModel(null)}>
                          <CheckIcon
                            className={`mr-2 size-3.5 ${
                              selectedIngestModel === null ? "opacity-100" : "opacity-0"
                            }`}
                          />
                          Default model
                        </DropdownMenuItem>
                        {availableModels.map((m) => (
                          <DropdownMenuItem
                            key={`ingest-m-${m.name}`}
                            onClick={() => setSelectedIngestModel(m.name)}
                          >
                            <CheckIcon
                              className={`mr-2 size-3.5 ${
                                selectedIngestModel === m.name ? "opacity-100" : "opacity-0"
                              }`}
                            />
                            <span className="truncate">{m.display_name ?? m.name}</span>
                          </DropdownMenuItem>
                        ))}
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                  {ingestRunning ? (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        cancelIngest.mutate(undefined, {
                          onSuccess: (payload) => {
                            if (payload.accepted === false) {
                              toast.message(payload.message ?? "No vault ingest job is running.");
                            } else {
                              toast.success(payload.message ?? "Stopping ingest now; in-flight items are requeued.");
                            }
                          },
                          onError: (error) => toast.error(error.message),
                        });
                      }}
                      disabled={cancelRequested}
                      title="Stop now — abandons the in-flight batch and requeues its items"
                    >
                      <SquareIcon className="mr-2 size-4 fill-current" />
                      {cancelRequested ? "Stopping..." : "Stop"}
                    </Button>
                  ) : null}
                  <div className="inline-flex items-stretch">
                    <Button
                      size="sm"
                      variant="outline"
                      className="rounded-r-none border-r-0"
                      onClick={() => {
                        lintVaultMutation.mutate(
                          {
                            dryRun: true,
                            useLlm: true,
                            workers: selectedLintWorkers,
                            modelName: selectedLintModel,
                          },
                          {
                            onSuccess: (preview) => setLintPreview(preview),
                            onError: (error) => toast.error(error.message),
                          },
                        );
                      }}
                      disabled={lintVaultMutation.isPending || ingestRunning}
                      title={
                        ingestRunning
                          ? "Wait for ingest to finish before linting"
                          : `LLM-judged scan for low-value entities/concepts (judge: ${lintModelLabel}, ${selectedLintWorkers} worker${
                              selectedLintWorkers === 1 ? "" : "s"
                            })`
                      }
                    >
                      {lintVaultMutation.isPending && lintPreview === null ? (
                        <Loader2Icon className="mr-2 size-4 animate-spin" />
                      ) : (
                        <SparklesIcon className="mr-2 size-4" />
                      )}
                      {`Lint${selectedLintWorkers > 1 ? ` (${selectedLintWorkers}×)` : ""}`}
                    </Button>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          size="sm"
                          variant="outline"
                          className="rounded-l-none px-2"
                          disabled={lintVaultMutation.isPending || ingestRunning}
                          title="Choose judge workers and model"
                          aria-label="Choose judge workers and model"
                        >
                          <ChevronDownIcon className="size-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="max-h-80 overflow-y-auto">
                        <DropdownMenuLabel>Parallel workers</DropdownMenuLabel>
                        {[1, 2, 3].map((n) => (
                          <DropdownMenuItem
                            key={`lint-w-${n}`}
                            onClick={() => setSelectedLintWorkers(n)}
                          >
                            <CheckIcon
                              className={`mr-2 size-3.5 ${
                                selectedLintWorkers === n ? "opacity-100" : "opacity-0"
                              }`}
                            />
                            {n} worker{n === 1 ? "" : "s"}
                            {n === 1 ? " (sequential)" : ""}
                          </DropdownMenuItem>
                        ))}
                        <DropdownMenuSeparator />
                        <DropdownMenuLabel>Judge model</DropdownMenuLabel>
                        <DropdownMenuItem onClick={() => setSelectedLintModel(null)}>
                          <CheckIcon
                            className={`mr-2 size-3.5 ${
                              selectedLintModel === null ? "opacity-100" : "opacity-0"
                            }`}
                          />
                          Default model
                        </DropdownMenuItem>
                        {availableModels.map((m) => (
                          <DropdownMenuItem
                            key={`lint-m-${m.name}`}
                            onClick={() => setSelectedLintModel(m.name)}
                          >
                            <CheckIcon
                              className={`mr-2 size-3.5 ${
                                selectedLintModel === m.name ? "opacity-100" : "opacity-0"
                              }`}
                            />
                            <span className="truncate">{m.display_name ?? m.name}</span>
                          </DropdownMenuItem>
                        ))}
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                  {lintJudgeRunning ? (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        cancelLint.mutate(undefined, {
                          onSuccess: (payload) => {
                            if (payload.accepted === false) {
                              toast.message(payload.message ?? "No lint job is running.");
                            } else {
                              toast.success(payload.message ?? "Stopping lint at the next batch boundary.");
                            }
                          },
                          onError: (error) => toast.error(error.message),
                        });
                      }}
                      disabled={cancelLint.isPending}
                      title="Stop the lint judge at the next batch boundary"
                    >
                      <SquareIcon className="mr-2 size-4 fill-current" />
                      {cancelLint.isPending ? "Stopping..." : "Stop Lint"}
                    </Button>
                  ) : null}
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      toast.message("Vault refresh started. Cached snapshot will update when complete.");
                      refreshExplorer.mutate(undefined, {
                        onSuccess: () => toast.success("Vault cache refreshed."),
                        onError: (error) => toast.error(error.message),
                      });
                    }}
                    disabled={refreshExplorer.isPending}
                  >
                    <RefreshCwIcon className={`mr-2 size-4 ${refreshExplorer.isPending ? "animate-spin" : ""}`} />
                    {refreshExplorer.isPending ? "Refreshing..." : "Refresh Cache"}
                  </Button>
                </div>
              </div>
              <div className="min-h-0 flex-1">
                <ResizablePanelGroup
                  id="vault-main-panel-group"
                  orientation="horizontal"
                  className="h-full gap-1"
                >
                  <ResizablePanel
                    id="vault-left-panel"
                    defaultSize={25}
                    minSize={15}
                    className="flex h-full flex-col space-y-3 rounded-md border p-3"
                  >
                    <p className="text-xs text-muted-foreground">/backend/.capyhome/knowledge_vault/</p>
                    <div className="min-h-0 flex-1 overflow-y-auto space-y-1 text-xs">
                      <button
                        type="button"
                        className="flex w-full items-center gap-1 rounded px-2 py-1 text-left font-medium hover:bg-muted"
                        onClick={() => setRootCollapsed((current) => !current)}
                        aria-expanded={!rootCollapsed}
                        title={rootCollapsed ? "Expand root" : "Collapse root"}
                      >
                        {rootCollapsed ? (
                          <ChevronRightIcon className="size-3.5" />
                        ) : (
                          <ChevronDownIcon className="size-3.5" />
                        )}
                        <FolderIcon className="size-3.5" />
                        <span>vault</span>
                      </button>
                      {!rootCollapsed &&
                        filesTree.map((node) => (
                          <VaultTreeNode
                            key={node.path}
                            node={node}
                            depth={1}
                            expandedPaths={expandedPaths}
                            onToggle={togglePath}
                            selectedPath={selectedPath}
                            onSelectFile={handleSelectFile}
                            refetchInterval={treeRefetchInterval}
                          />
                        ))}
                      {!explorerLoading &&
                      (effectiveExplorer?.files?.length ?? 0) === 0 && (
                        <p className="text-muted-foreground px-1">No cached vault items yet.</p>
                      )}
                    </div>
                  </ResizablePanel>
                  <ResizableHandle id="vault-main-handle" withHandle className="mx-2 bg-transparent" />
                  <ResizablePanel
                    id="vault-right-panel"
                    defaultSize={75}
                    minSize={30}
                    className="flex h-full flex-col space-y-3 rounded-md border p-3"
                  >
                    <div className="flex gap-2">
                      <Button size="sm" variant={previewTab === "preview" ? "default" : "outline"} onClick={() => setPreviewTab("preview")}>Preview</Button>
                      <Button size="sm" variant={previewTab === "entities" ? "default" : "outline"} onClick={() => setPreviewTab("entities")}>Entity Browser</Button>
                    </div>
                    {previewTab === "entities" ? (
                      <VaultEntityBrowser
                        onSourceOpen={(sourceId) => {
                          // The explorer tree is loaded lazily, so resolve the compiled
                          // source by its deterministic path (02_compiled/sources/{id}.md)
                          // and let the preview pane report if it isn't compiled yet.
                          handleSelectFile(`02_compiled/sources/${sourceId}.md`);
                        }}
                      />
                    ) : previewTab === "preview" ? (
                      <div className="flex min-h-0 flex-1 flex-col space-y-2">
                        <div className="flex items-center justify-between gap-2">
                          <p className="text-muted-foreground truncate text-xs">{selectedPath ?? "Preview"}</p>
                          <div className="flex items-center gap-2">
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={async () => {
                                await navigator.clipboard.writeText(editableContent);
                                toast.success("Content copied.");
                              }}
                              disabled={!vaultFile?.path}
                            >
                              <CopyIcon className="mr-1 size-3.5" />
                              Copy
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => {
                                if (!vaultFile?.path) return;
                                const blob = new Blob([editableContent], { type: "text/markdown;charset=utf-8" });
                                const url = URL.createObjectURL(blob);
                                const anchor = document.createElement("a");
                                anchor.href = url;
                                anchor.download = vaultFile.path.split("/").pop() ?? "vault-source.md";
                                anchor.click();
                                URL.revokeObjectURL(url);
                              }}
                              disabled={!vaultFile?.path}
                            >
                              <DownloadIcon className="mr-1 size-3.5" />
                              Download
                            </Button>
                            {vaultFile?.editable && !editorCollapsed ? (
                              <Button
                                size="icon-sm"
                                variant="outline"
                                onClick={() => {
                                  if (!vaultFile?.path) return;
                                  saveVaultFile.mutate(
                                    { path: vaultFile.path, content: editableContent },
                                    {
                                      onSuccess: () => toast.success("Raw source updated."),
                                      onError: (error) => toast.error(error.message),
                                    },
                                  );
                                }}
                                disabled={saveVaultFile.isPending}
                                title="Save changes"
                                aria-label="Save changes"
                              >
                                {saveVaultFile.isPending ? (
                                  <Loader2Icon className="size-4 animate-spin" />
                                ) : (
                                  <SaveIcon className="size-4" />
                                )}
                              </Button>
                            ) : null}
                            {vaultFile?.editable ? (
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => {
                                  if (!vaultFile?.path) return;
                                  if (!window.confirm("Delete this raw source file? This cannot be undone.")) return;
                                  deleteVaultFile.mutate(vaultFile.path, {
                                    onSuccess: () => {
                                      toast.success("Raw source deleted.");
                                      setSelectedPath(null);
                                      setEditableContent("");
                                    },
                                    onError: (error) => toast.error(error.message),
                                  });
                                }}
                                disabled={deleteVaultFile.isPending}
                              >
                                <Trash2Icon className="mr-1 size-3.5" />
                                Delete Source
                              </Button>
                            ) : null}
                            <Button
                              size="icon-sm"
                              variant="ghost"
                              onClick={() => {
                                if (editorCollapsed) {
                                  editorPanelRef.current?.expand();
                                } else {
                                  editorPanelRef.current?.collapse();
                                }
                              }}
                              title={editorCollapsed ? "Show editor" : "Hide editor"}
                              aria-label={editorCollapsed ? "Show editor" : "Hide editor"}
                            >
                              {editorCollapsed ? (
                                <PanelRightOpenIcon className="size-4" />
                              ) : (
                                <PanelRightCloseIcon className="size-4" />
                              )}
                            </Button>
                          </div>
                        </div>
                        <ResizablePanelGroup
                          id="vault-preview-panel-group"
                          orientation="horizontal"
                          className="min-h-0 flex-1"
                        >
                          <ResizablePanel
                            id="vault-markdown-preview-panel"
                            defaultSize={60}
                            minSize={30}
                            className="min-h-0 overflow-y-auto rounded border p-3 text-sm"
                          >
                            {selectedPath && vaultFileError && !vaultFileLoading ? (
                              <p className="text-muted-foreground text-xs">
                                Couldn&apos;t open <span className="font-mono">{selectedPath}</span>. It may not be
                                compiled yet, or no longer exists.
                              </p>
                            ) : (
                              <MarkdownContent
                                content={editableContent}
                                isLoading={vaultFileLoading}
                                rehypePlugins={streamdownPlugins.rehypePlugins}
                                className="prose prose-sm max-w-none"
                              />
                            )}
                          </ResizablePanel>
                          <ResizableHandle
                            id="vault-preview-editor-handle"
                            withHandle
                            className={`mx-2 bg-transparent ${editorCollapsed ? "pointer-events-none opacity-0" : ""}`}
                          />
                          <ResizablePanel
                            id="vault-editor-panel"
                            panelRef={editorPanelRef}
                            defaultSize={40}
                            minSize={20}
                            collapsible
                            collapsedSize={0}
                            onResize={(size) => setEditorCollapsed(size.asPercentage < 1)}
                            className="min-h-0"
                          >
                            <Textarea
                              value={editableContent}
                              onChange={(event) => setEditableContent(event.target.value)}
                              className="size-full min-h-0 resize-none font-mono text-xs"
                              readOnly={!vaultFile?.editable}
                              placeholder={vaultFileLoading ? "Loading..." : "No file selected"}
                            />
                          </ResizablePanel>
                        </ResizablePanelGroup>
                      </div>
                    ) : null}
                  </ResizablePanel>
                </ResizablePanelGroup>
              </div>
            </div>
          </div>
        </div>
      </WorkspaceBody>
      <Dialog
        open={lintPreview !== null}
        onOpenChange={(open) => {
          if (!open) setLintPreview(null);
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Vault lint preview</DialogTitle>
            <DialogDescription>
              {lintPreview === null ? null : lintTotalToRemove === 0 ? (
                "Nothing to prune — the vault is clean."
              ) : (
                <>
                  About to prune <b>{lintPreview.entities.flagged.length}</b> entit
                  {lintPreview.entities.flagged.length === 1 ? "y" : "ies"} (of {lintPreview.entities.total_before})
                  {" and "}
                  <b>{lintPreview.concepts.flagged.length}</b> concept
                  {lintPreview.concepts.flagged.length === 1 ? "" : "s"} (of {lintPreview.concepts.total_before}).
                  Entities will also be added to the dismissal list so future ingests won&apos;t re-create them.
                </>
              )}
            </DialogDescription>
          </DialogHeader>
          {lintPreview !== null && lintTotalToRemove > 0 ? (
            <div className="max-h-80 space-y-3 overflow-y-auto text-xs">
              {lintPreview.entities.flagged.length > 0 ? (
                <div>
                  <p className="mb-1 font-medium">Entities ({lintPreview.entities.flagged.length})</p>
                  <ul className="space-y-0.5">
                    {lintPreview.entities.flagged.slice(0, 50).map((f) => (
                      <li key={`e-${f.slug}`} className="flex justify-between gap-2">
                        <span className="truncate">{f.label}</span>
                        <span className="shrink-0 text-muted-foreground">{f.reasons.join(", ")}</span>
                      </li>
                    ))}
                    {lintPreview.entities.flagged.length > 50 ? (
                      <li className="text-muted-foreground">
                        … and {lintPreview.entities.flagged.length - 50} more
                      </li>
                    ) : null}
                  </ul>
                </div>
              ) : null}
              {lintPreview.concepts.flagged.length > 0 ? (
                <div>
                  <p className="mb-1 font-medium">Concepts ({lintPreview.concepts.flagged.length})</p>
                  <ul className="space-y-0.5">
                    {lintPreview.concepts.flagged.slice(0, 50).map((f) => (
                      <li key={`c-${f.slug}`} className="flex justify-between gap-2">
                        <span className="truncate">{f.label}</span>
                        <span className="shrink-0 text-muted-foreground">{f.reasons.join(", ")}</span>
                      </li>
                    ))}
                    {lintPreview.concepts.flagged.length > 50 ? (
                      <li className="text-muted-foreground">
                        … and {lintPreview.concepts.flagged.length - 50} more
                      </li>
                    ) : null}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setLintPreview(null)}>
              {lintTotalToRemove === 0 ? "Close" : "Cancel"}
            </Button>
            {lintTotalToRemove > 0 ? (
              <Button
                onClick={() => {
                  if (!lintPreview) return;
                  const entitySlugs = lintPreview.entities.flagged.map((f) => f.slug);
                  const conceptSlugs = lintPreview.concepts.flagged.map((f) => f.slug);
                  lintVaultMutation.mutate(
                    {
                      dryRun: false,
                      entitySlugs,
                      conceptSlugs,
                    },
                    {
                      onSuccess: (result) => {
                        const removed =
                          (result.entities.removed ?? 0) + (result.concepts.removed ?? 0);
                        toast.success(
                          removed > 0
                            ? `Pruned ${result.entities.removed} entit${result.entities.removed === 1 ? "y" : "ies"} and ${result.concepts.removed} concept${result.concepts.removed === 1 ? "" : "s"}.`
                            : "Nothing was pruned.",
                        );
                        setLintPreview(null);
                      },
                      onError: (error) => toast.error(error.message),
                    },
                  );
                }}
                disabled={lintVaultMutation.isPending}
              >
                {lintVaultMutation.isPending ? (
                  <Loader2Icon className="mr-2 size-4 animate-spin" />
                ) : (
                  <Trash2Icon className="mr-2 size-4" />
                )}
                Prune {lintTotalToRemove}
              </Button>
            ) : null}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </WorkspaceContainer>
  );
}
