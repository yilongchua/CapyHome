"use client";

import { SearchIcon, Trash2Icon } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/core/i18n/hooks";
import { useDeleteSelectedThreads, useThreads } from "@/core/threads/hooks";
import { titleOfThread } from "@/core/threads/utils";
import { formatTimeAgo } from "@/core/utils/datetime";
import { cn } from "@/lib/utils";

import { SettingsSection } from "./settings-section";

function useCurrentThreadId() {
  const pathname = usePathname();
  return useMemo(() => {
    const match = /\/workspace\/chats\/([^/]+)/.exec(pathname);
    return match?.[1] ?? null;
  }, [pathname]);
}

function formatThreadUpdatedAt(updatedAt: string | undefined) {
  if (!updatedAt) {
    return null;
  }
  const timestamp = Date.parse(updatedAt);
  if (Number.isNaN(timestamp)) {
    return updatedAt;
  }
  return formatTimeAgo(timestamp);
}

export function ChatManagementSettingsPage() {
  const { t } = useI18n();
  const router = useRouter();
  const currentThreadId = useCurrentThreadId();
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedThreadIds, setSelectedThreadIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [confirmOpen, setConfirmOpen] = useState(false);
  const {
    data: threads = [],
    isLoading,
    error,
  } = useThreads({
    limit: 200,
    sortBy: "updated_at",
    sortOrder: "desc",
    select: ["thread_id", "updated_at", "values"],
  });
  const deleteSelectedThreads = useDeleteSelectedThreads();

  const filteredThreads = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) {
      return threads;
    }
    return threads.filter((thread) => {
      const title = titleOfThread(thread).toLowerCase();
      const threadId = thread.thread_id.toLowerCase();
      return title.includes(query) || threadId.includes(query);
    });
  }, [searchQuery, threads]);

  const visibleThreadIds = useMemo(
    () => filteredThreads.map((thread) => thread.thread_id),
    [filteredThreads],
  );
  const selectedCount = selectedThreadIds.size;
  const allVisibleSelected =
    visibleThreadIds.length > 0 &&
    visibleThreadIds.every((threadId) => selectedThreadIds.has(threadId));

  const toggleThread = (threadId: string) => {
    setSelectedThreadIds((current) => {
      const next = new Set(current);
      if (next.has(threadId)) {
        next.delete(threadId);
      } else {
        next.add(threadId);
      }
      return next;
    });
  };

  const toggleAllVisible = () => {
    setSelectedThreadIds((current) => {
      const next = new Set(current);
      if (allVisibleSelected) {
        for (const threadId of visibleThreadIds) {
          next.delete(threadId);
        }
      } else {
        for (const threadId of visibleThreadIds) {
          next.add(threadId);
        }
      }
      return next;
    });
  };

  const deleteSelected = () => {
    const threadIds = Array.from(selectedThreadIds);
    const deletesCurrentThread =
      currentThreadId !== null && selectedThreadIds.has(currentThreadId);

    deleteSelectedThreads.mutate(
      { threadIds },
      {
        onSuccess: ({ deletedThreadIds }) => {
          setSelectedThreadIds((current) => {
            const next = new Set(current);
            for (const threadId of deletedThreadIds) {
              next.delete(threadId);
            }
            return next;
          });
          setConfirmOpen(false);
          if (deletesCurrentThread && deletedThreadIds.includes(currentThreadId)) {
            router.push("/workspace/chats/new");
          }
        },
      },
    );
  };

  return (
    <SettingsSection
      title={t.chats.manageChats}
      description={t.chats.manageChatsDescription}
    >
      <div className="space-y-4">
        <div className="flex flex-col gap-3 rounded-lg border bg-muted/20 p-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="relative flex-1">
            <SearchIcon className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2" />
            <Input
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder={t.chats.searchChats}
              className="pl-9"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={toggleAllVisible}
              disabled={visibleThreadIds.length === 0 || deleteSelectedThreads.isPending}
            >
              {t.chats.selectAllVisible}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setSelectedThreadIds(new Set())}
              disabled={selectedCount === 0 || deleteSelectedThreads.isPending}
            >
              {t.chats.clearSelection}
            </Button>
          </div>
        </div>

        <div className="flex flex-col gap-3 rounded-lg border p-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="text-sm text-muted-foreground">
            {t.chats.selectedChats(selectedCount)}
          </div>
          <Button
            type="button"
            variant="destructive"
            size="sm"
            onClick={() => setConfirmOpen(true)}
            disabled={selectedCount === 0 || deleteSelectedThreads.isPending}
          >
            <Trash2Icon className="size-4" />
            {t.chats.deleteSelectedChats}
          </Button>
        </div>

        {isLoading ? (
          <div className="text-muted-foreground rounded-lg border p-4 text-sm">
            {t.common.loading}
          </div>
        ) : null}

        {error ? (
          <div className="text-destructive rounded-lg border p-4 text-sm">
            Failed to load chats.
          </div>
        ) : null}

        {!isLoading && !error && threads.length === 0 ? (
          <div className="text-muted-foreground rounded-lg border p-4 text-sm">
            {t.chats.noChatsToManage}
          </div>
        ) : null}

        {!isLoading && !error && threads.length > 0 ? (
          <div className="overflow-hidden rounded-lg border">
            {filteredThreads.length === 0 ? (
              <div className="text-muted-foreground p-4 text-sm">
                {t.chats.noMatchingChats}
              </div>
            ) : (
              <div className="divide-y">
                {filteredThreads.map((thread) => {
                  const checked = selectedThreadIds.has(thread.thread_id);
                  const updatedAt = formatThreadUpdatedAt(thread.updated_at);

                  return (
                    <label
                      key={thread.thread_id}
                      className={cn(
                        "flex cursor-pointer items-start gap-3 p-3 transition-colors hover:bg-muted/50",
                        checked && "bg-muted/60",
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleThread(thread.thread_id)}
                        disabled={deleteSelectedThreads.isPending}
                        className="mt-1 size-4 rounded border-border accent-primary"
                      />
                      <span className="min-w-0 flex-1 space-y-1">
                        <span className="block truncate text-sm font-medium">
                          {titleOfThread(thread)}
                        </span>
                        <span className="text-muted-foreground block truncate text-xs">
                          {thread.thread_id}
                        </span>
                        {updatedAt ? (
                          <span className="text-muted-foreground block text-xs">
                            {t.chats.updated}: {updatedAt}
                          </span>
                        ) : null}
                      </span>
                    </label>
                  );
                })}
              </div>
            )}
          </div>
        ) : null}
      </div>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t.chats.deleteSelectedChats}</DialogTitle>
          </DialogHeader>
          <div className="space-y-2 py-1">
            <p className="text-muted-foreground text-sm">
              {deleteSelectedThreads.progress
                ? t.chats.deleteSelectedChatsProgress(
                    deleteSelectedThreads.progress.current,
                    deleteSelectedThreads.progress.total,
                  )
                : t.chats.deleteSelectedChatsConfirm(selectedCount)}
            </p>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setConfirmOpen(false)}
              disabled={deleteSelectedThreads.isPending}
            >
              {t.common.cancel}
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={deleteSelected}
              disabled={selectedCount === 0 || deleteSelectedThreads.isPending}
            >
              {deleteSelectedThreads.isPending
                ? deleteSelectedThreads.progress
                  ? t.chats.deleteSelectedChatsProgress(
                      deleteSelectedThreads.progress.current,
                      deleteSelectedThreads.progress.total,
                    )
                  : t.chats.deleteSelectedChats
                : t.common.delete}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </SettingsSection>
  );
}
