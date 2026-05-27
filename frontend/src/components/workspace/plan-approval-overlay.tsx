"use client";

import { PencilIcon, PlayIcon, SendIcon, XIcon } from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import { Button } from "@/components/ui/button";
import { useDirectory } from "@/components/workspace/artifacts/context";
import { cn } from "@/lib/utils";

type Mode = "choose" | "edit";

export type ClarificationOption = {
  label: string;
  recommended?: boolean;
  description?: string | null;
};

export type ClarificationItem = {
  question: string;
  options: ClarificationOption[];
};

export function PlanApprovalOverlay({
  planPath,
  onExecute,
  onCancel,
  onSubmitEdit,
  isExecuting = false,
  isSubmittingEdit = false,
  className,
}: {
  planPath?: string;
  onExecute: () => void;
  onCancel: () => void;
  onSubmitEdit: (suggestion: string) => Promise<void> | void;
  isExecuting?: boolean;
  isSubmittingEdit?: boolean;
  className?: string;
}) {
  const { select, setOpen } = useDirectory();
  const handleOpenPlan = useCallback(() => {
    if (!planPath) {
      return;
    }
    select(planPath);
    setOpen(true);
  }, [planPath, select, setOpen]);
  const [mode, setMode] = useState<Mode>("choose");
  const [editText, setEditText] = useState("");
  const editRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (mode === "edit") {
      editRef.current?.focus();
    }
  }, [mode]);

  const handleSend = useCallback(async () => {
    const text = editText.trim();
    if (!text || isSubmittingEdit) {
      return;
    }
    await onSubmitEdit(text);
    setEditText("");
    setMode("choose");
  }, [editText, isSubmittingEdit, onSubmitEdit]);

  const handleEditKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setMode("choose");
        setEditText("");
        return;
      }
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        void handleSend();
      }
    },
    [handleSend],
  );

  return (
    <div
      className={cn(
        "bg-background/95 absolute inset-0 z-20 flex -translate-y-0.5 flex-col rounded-2xl border border-dashed backdrop-blur-sm",
        className,
      )}
      role="dialog"
      aria-label="Plan approval"
    >
      <div className="flex h-8 items-center justify-between gap-2 border-b px-2">
        <p className="text-[14.4px] font-medium leading-none text-muted-foreground tracking-wide">
          Please Review{" "}
          <button
            type="button"
            onClick={handleOpenPlan}
            className="cursor-pointer text-foreground underline underline-offset-4"
          >
            plan.md
          </button>
        </p>
        <Button
          size="icon-sm"
          variant="ghost"
          className="text-muted-foreground shrink-0"
          onClick={onCancel}
          aria-label="Cancel plan"
          disabled={isExecuting || isSubmittingEdit}
        >
          <XIcon className="size-3.5" />
        </Button>
      </div>

      {mode === "choose" ? (
        <div className="flex flex-1 flex-col">
          <Button
            size="lg"
            className="h-auto w-full flex-1 justify-start gap-2 rounded-none border-0 text-[16.8px]"
            onClick={onExecute}
            disabled={isExecuting}
          >
            <PlayIcon className="size-5" />
            {isExecuting ? "Starting..." : "Execute Plan"}
          </Button>
          <Button
            size="lg"
            variant="outline"
            className="h-auto w-full flex-1 justify-start gap-2 rounded-t-none rounded-b-2xl border-0 text-[16.8px]"
            onClick={() => setMode("edit")}
            disabled={isExecuting}
          >
            <PencilIcon className="size-5" />
            Edit Plan
          </Button>
        </div>
      ) : (
        <div className="flex items-stretch gap-2 px-4 pb-3">
          <textarea
            ref={editRef}
            value={editText}
            onChange={(event) => setEditText(event.target.value)}
            onKeyDown={handleEditKeyDown}
            placeholder="Edit plan — describe what should change"
            className="bg-background placeholder:text-muted-foreground flex-1 resize-none rounded-md border px-3 py-2 text-sm outline-none focus:ring-1"
            disabled={isSubmittingEdit}
          />
          <div className="flex flex-col justify-between gap-1">
            <Button
              size="icon-sm"
              variant="ghost"
              onClick={() => {
                setMode("choose");
                setEditText("");
              }}
              aria-label="Cancel edit"
              disabled={isSubmittingEdit}
            >
              <XIcon className="size-3.5" />
            </Button>
            <Button
              size="icon-sm"
              onClick={() => void handleSend()}
              aria-label="Send edit"
              disabled={isSubmittingEdit || editText.trim().length === 0}
            >
              <SendIcon className="size-3.5" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

export function PlanClarificationPopup({
  clarifications,
  activeClarificationIndex = 0,
  onClarify,
  isClarifying = false,
  onDismiss,
  className,
}: {
  clarifications: ClarificationItem[];
  activeClarificationIndex?: number;
  onClarify: (clarificationIndex: number, selectedOptionLabel: string) => void;
  isClarifying?: boolean;
  onDismiss?: () => void;
  className?: string;
}) {
  const [selectedTab, setSelectedTab] = useState<number>(
    Math.min(Math.max(activeClarificationIndex, 0), Math.max(clarifications.length - 1, 0)),
  );

  useEffect(() => {
    if (clarifications.length === 0) {
      return;
    }
    setSelectedTab((current) => {
      const bounded = Math.min(Math.max(activeClarificationIndex, 0), clarifications.length - 1);
      return Number.isFinite(bounded) ? bounded : current;
    });
  }, [activeClarificationIndex, clarifications.length]);

  if (clarifications.length === 0) {
    return null;
  }

  const safeIndex = Math.min(Math.max(selectedTab, 0), clarifications.length - 1);
  const active = clarifications[safeIndex];
  if (!active) {
    return null;
  }

  return (
    <div
      className={cn(
        "bg-background/95 mb-2 rounded-2xl border border-dashed shadow-md backdrop-blur",
        className,
      )}
      role="dialog"
      aria-label="Plan clarification"
    >
      <div className="flex items-start justify-between gap-2 px-4 pt-3">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          Clarification needed
        </p>
        {onDismiss ? (
          <Button
            size="icon-sm"
            variant="ghost"
            className="text-muted-foreground shrink-0"
            onClick={onDismiss}
            aria-label="Dismiss clarification"
            disabled={isClarifying}
          >
            <XIcon className="size-3.5" />
          </Button>
        ) : null}
      </div>
      <div className="px-4 pb-3">
        <div
          role="tablist"
          aria-label="Clarification questions"
          className="mt-2 flex flex-wrap gap-1 border-b"
        >
          {clarifications.map((_, index) => (
            <button
              key={index}
              role="tab"
              type="button"
              aria-selected={index === safeIndex}
              onClick={() => setSelectedTab(index)}
              className={cn(
                "rounded-t-md border-b-2 px-3 py-1 text-xs font-medium transition-colors",
                index === safeIndex
                  ? "border-primary text-foreground"
                  : "text-muted-foreground border-transparent hover:text-foreground",
              )}
            >
              Q{index + 1}
            </button>
          ))}
        </div>
        <div className="mt-2 flex max-h-56 flex-col gap-2 overflow-y-auto">
          {active.question ? (
            <p className="text-sm">{active.question}</p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            {active.options.map((option) => (
              <Button
                key={option.label}
                size="sm"
                variant={option.recommended ? "default" : "outline"}
                onClick={() => onClarify(safeIndex, option.label)}
                disabled={isClarifying}
                title={option.description ?? undefined}
              >
                {option.label}
              </Button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
