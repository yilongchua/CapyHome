"use client";

import { useParams, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { uuid } from "@/core/utils/uuid";

export function useThreadChat() {
  const { thread_id: threadIdFromPath } = useParams<{ thread_id: string }>();
  const searchParams = useSearchParams();
  const newChatKey = searchParams.get("new") ?? "";

  // For an existing thread, the path id is authoritative. For "new", we defer
  // returning a thread id until the post-mount effect generates a UUID. The
  // page is expected to short-circuit rendering while threadId is null so the
  // `<ChatPageContent key={threadId}>` boundary only mounts once with the
  // final UUID — otherwise every initial fetch (generation jobs, mounted
  // folder, etc.) runs twice on /workspace/chats/new.
  const [threadId, setThreadId] = useState<string | null>(() =>
    threadIdFromPath === "new" ? null : threadIdFromPath,
  );

  const [isNewThread, setIsNewThread] = useState(
    () => threadIdFromPath === "new",
  );

  useEffect(() => {
    if (threadIdFromPath === "new") {
      setIsNewThread(true);
      setThreadId(uuid());
      return;
    }
    setIsNewThread(false);
    setThreadId(threadIdFromPath);
  }, [newChatKey, threadIdFromPath]);

  const isMock = searchParams.get("mock") === "true";
  return { threadId, isNewThread, setIsNewThread, isMock };
}
