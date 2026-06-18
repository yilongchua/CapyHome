"use client";

import { useCallback } from "react";

import { getBackendBaseURL } from "@/core/config";
import { useNotification } from "@/core/notification/hooks";
import { textOfMessage } from "@/core/threads/utils";

import type { AgentThreadState } from "./types";

const NOTIFICATION_PREVIEW_LENGTH = 200;

/**
 * Ask the backend to ping the user over an IM channel (e.g. Telegram) about a
 * finished reply. Fire-and-forget: the backend no-ops when notifications or the
 * on_chat_complete scope are disabled, or when no target chat is registered.
 */
function postChatCompleteNotification(
  threadId: string,
  title: string,
  preview: string,
) {
  void fetch(`${getBackendBaseURL()}/api/notifications/thread-complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ thread_id: threadId, title, preview }),
  }).catch(() => {
    /* best-effort; ignore network errors */
  });
}

export function useThreadNotification(threadId?: string) {
  const { showNotification } = useNotification();

  const onFinish = useCallback(
    (state: AgentThreadState) => {
      if (!document.hidden && document.hasFocus()) return;

      let body = "Conversation finished";
      const lastMessage = state.messages.at(-1);
      if (lastMessage) {
        const textContent = textOfMessage(lastMessage);
        if (textContent) {
          body =
            textContent.length > NOTIFICATION_PREVIEW_LENGTH
              ? textContent.substring(0, NOTIFICATION_PREVIEW_LENGTH) + "..."
              : textContent;
        }
      }
      showNotification(state.title, { body });

      // Only when the tab is hidden/unfocused (same gate as the browser
      // notification) — mirror it to the backend so an IM channel can deliver
      // it even if the browser is closed shortly after.
      if (threadId) {
        postChatCompleteNotification(threadId, state.title ?? "CapyHome", body);
      }
    },
    [showNotification, threadId],
  );

  return { onFinish };
}
