"use client";

import { usePathname } from "next/navigation";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { deleteVaultKnowledgeGraph } from "@/core/control-plane/api";
import { useCleanupAutoresearch, useCleanupPipelineRuns } from "@/core/control-plane/hooks";
import { useI18n } from "@/core/i18n/hooks";
import { useMemoryMutations } from "@/core/memory/hooks";
import { useDeleteAllThreads } from "@/core/threads/hooks";

import { SettingsSection } from "./settings-section";

function useCurrentThreadId() {
  const pathname = usePathname();
  return useMemo(() => {
    const match = /\/workspace\/chats\/([^/]+)/.exec(pathname);
    return match?.[1] ?? null;
  }, [pathname]);
}

export function CleanUpSettingsPage() {
  const { t } = useI18n();
  const threadId = useCurrentThreadId();

  // Memory
  const globalMutations = useMemoryMutations("global");
  const workspaceMutations = useMemoryMutations("workspace", threadId);
  const deleteAllChats = useDeleteAllThreads();

  // Knowledge Graph
  const [graphDeleting, setGraphDeleting] = useState(false);

  // Pipeline
  const cleanupRuns = useCleanupPipelineRuns();
  const [days, setDays] = useState("14");

  // Autoresearch
  const cleanup = useCleanupAutoresearch();

  return (
    <SettingsSection
      title="Clean Up"
      description="Permanently delete data in bulk. All actions are irreversible."
    >
      {/* Memory */}
      <div className="rounded-lg border border-destructive/30 p-4 space-y-3">
        <div className="font-medium">Memory</div>
        <div className="text-sm text-muted-foreground">
          Permanently clears stored facts, rules, and conversation summaries.
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="destructive"
            onClick={() => {
              if (!window.confirm("Delete all global memory? This cannot be undone.")) return;
              globalMutations.clear.mutate();
            }}
            disabled={globalMutations.clear.isPending}
          >
            Delete Global Memory
          </Button>
          <Button
            variant="destructive"
            onClick={() => {
              if (!threadId) return;
              if (!window.confirm("Delete all workspace memory for this thread? This cannot be undone.")) return;
              workspaceMutations.clear.mutate();
            }}
            disabled={!threadId || workspaceMutations.clear.isPending}
          >
            Delete Workspace Memory
          </Button>
          <Button
            variant="destructive"
            onClick={() => {
              if (!window.confirm(t.chats.deleteAllChatsConfirm)) return;
              deleteAllChats.mutate();
            }}
            disabled={deleteAllChats.isPending}
          >
            {deleteAllChats.isPending ? "Deleting…" : t.chats.deleteAllChats}
          </Button>
        </div>
      </div>

      {/* Knowledge Graph */}
      <div className="rounded-lg border border-destructive/30 p-4 space-y-3">
        <div className="font-medium">{t.settings.knowledgeVault.deleteGraphTitle}</div>
        <p className="text-sm text-muted-foreground">
          {t.settings.knowledgeVault.deleteGraphDescription}
        </p>
        <Button
          variant="destructive"
          onClick={async () => {
            if (!window.confirm(t.settings.knowledgeVault.deleteGraphConfirm)) return;
            setGraphDeleting(true);
            try {
              await deleteVaultKnowledgeGraph();
            } catch (err) {
              window.alert(
                err instanceof Error ? err.message : "Failed to delete knowledge graph.",
              );
            } finally {
              setGraphDeleting(false);
            }
          }}
          disabled={graphDeleting}
        >
          {graphDeleting
            ? t.settings.knowledgeVault.deleteGraphPending
            : t.settings.knowledgeVault.deleteGraphButton}
        </Button>
      </div>

      {/* Pipeline Runs */}
      <div className="rounded-lg border border-destructive/30 p-4 space-y-3">
        <div className="font-medium">Pipeline Runs</div>
        <div className="text-sm text-muted-foreground">
          Remove old terminal scheduled runs (completed / failed / cancelled / rejected).
        </div>
        <div className="flex items-center gap-2 max-w-md">
          <Input
            value={days}
            onChange={(e) => setDays(e.target.value)}
            placeholder="14"
            inputMode="numeric"
          />
          <Button
            variant="destructive"
            disabled={cleanupRuns.isPending}
            onClick={() => {
              const parsed = Number.parseInt(days, 10);
              const olderThanDays = Number.isFinite(parsed) && parsed > 0 ? parsed : 14;
              if (!window.confirm(`Delete scheduled runs older than ${olderThanDays} days?`)) return;
              cleanupRuns.mutate({ older_than_days: olderThanDays });
            }}
          >
            Delete Old Runs
          </Button>
        </div>
        {cleanupRuns.data ? (
          <div className="text-sm text-muted-foreground">
            Deleted {cleanupRuns.data.deleted} run(s).
          </div>
        ) : null}
      </div>

      {/* Autoresearch */}
      <div className="rounded-lg border border-destructive/30 p-4 space-y-3">
        <div className="font-medium">Autoresearch</div>
        <div className="text-sm text-muted-foreground">
          Clear autoresearch objectives, schedules, and their vault-tracking files.
        </div>
        <div className="flex gap-2">
          <Button
            variant="destructive"
            disabled={cleanup.isPending}
            onClick={() => {
              if (!window.confirm("Delete all autoresearch objectives and related runs?")) return;
              cleanup.mutate(true);
            }}
          >
            Delete Objectives + Runs
          </Button>
          <Button
            variant="outline"
            disabled={cleanup.isPending}
            onClick={() => {
              if (!window.confirm("Delete all autoresearch objectives but keep runs?")) return;
              cleanup.mutate(false);
            }}
          >
            Delete Objectives Only
          </Button>
        </div>
        {cleanup.data ? (
          <div className="text-sm text-muted-foreground">
            Deleted {cleanup.data.deleted_objectives} objective(s).
          </div>
        ) : null}
      </div>
    </SettingsSection>
  );
}
