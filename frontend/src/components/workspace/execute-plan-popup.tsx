"use client";

import { PlayIcon, XIcon } from "lucide-react";
import { useCallback } from "react";

import { Button } from "@/components/ui/button";
import { useDirectory } from "@/components/workspace/artifacts/context";
import type { PlanCreatedEvent } from "@/core/threads/hooks";

export function ExecutePlanPopup({
  event,
  planPath,
  onExecute,
  onDismiss,
  isExecuting = false,
}: {
  event: PlanCreatedEvent;
  planPath: string;
  onExecute: () => void;
  onDismiss: () => void;
  isExecuting?: boolean;
}) {
  const { select, setOpen } = useDirectory();

  const handleOpenPlan = useCallback(() => {
    select(planPath);
    setOpen(true);
  }, [planPath, select, setOpen]);

  return (
    <div className="pointer-events-none absolute right-0 bottom-full left-0 z-20 mb-3 flex items-end justify-center">
      <div className="pointer-events-auto w-full max-w-(--container-width-md) rounded-2xl border bg-background/90 p-4 shadow-lg backdrop-blur-md">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold leading-tight">
              Plan ready: {event.title}
            </p>
            <h3 className="mt-2 text-sm font-semibold leading-tight">
              Please review{" "}
              <button
                type="button"
                onClick={handleOpenPlan}
                className="cursor-pointer underline underline-offset-4"
              >
                plan.md
              </button>
              .
            </h3>
          </div>
          <Button
            size="icon-sm"
            variant="ghost"
            className="text-muted-foreground shrink-0"
            onClick={onDismiss}
          >
            <XIcon className="size-3.5" />
          </Button>
        </div>
        <div className="mt-3 flex gap-2">
          <Button size="sm" className="gap-1.5" onClick={onExecute} disabled={isExecuting}>
            <PlayIcon className="size-3.5" />
            {isExecuting ? "Starting..." : "Execute Plan"}
          </Button>
          <Button size="sm" variant="outline" onClick={onDismiss} disabled={isExecuting}>
            Keep editing
          </Button>
        </div>
      </div>
    </div>
  );
}
