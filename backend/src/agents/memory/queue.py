"""Memory update queue with debounce mechanism."""

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.config.memory_config import get_memory_config

logger = logging.getLogger(__name__)


@dataclass
class ConversationContext:
    """Context for a conversation to be processed for memory update."""

    thread_id: str
    messages: list[Any]
    timestamp: datetime = field(default_factory=datetime.utcnow)
    agent_name: str | None = None
    workspace_id: str | None = None


class MemoryUpdateQueue:
    """Queue for memory updates with debounce mechanism.

    This queue collects conversation contexts and processes them after
    a configurable debounce period. Multiple conversations received within
    the debounce window are batched together.
    """

    def __init__(self):
        """Initialize the memory update queue."""
        self._queue: list[ConversationContext] = []
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._processing = False

    def add(self, thread_id: str, messages: list[Any], agent_name: str | None = None, workspace_id: str | None = None) -> None:
        """Add a conversation to the update queue.

        Args:
            thread_id: The thread ID.
            messages: The conversation messages.
            agent_name: If provided, memory is stored per-agent. If None, uses global memory.
        """
        config = get_memory_config()
        if not config.enabled:
            return

        context = ConversationContext(
            thread_id=thread_id,
            messages=messages,
            agent_name=agent_name,
            workspace_id=workspace_id,
        )

        with self._lock:
            # Check if this thread already has a pending update
            # If so, replace it with the newer one
            self._queue = [c for c in self._queue if c.thread_id != thread_id]
            self._queue.append(context)

            # Reset or start the debounce timer
            self._reset_timer()

        logger.debug("Memory update queued for thread %s, queue size: %d", thread_id, len(self._queue))

    def _reset_timer(self) -> None:
        """Reset the debounce timer."""
        config = get_memory_config()

        # Cancel existing timer if any
        if self._timer is not None:
            self._timer.cancel()

        # Start new timer
        self._timer = threading.Timer(
            config.debounce_seconds,
            self._process_queue,
        )
        self._timer.daemon = True
        self._timer.start()

        logger.debug("Memory update timer set for %ss", config.debounce_seconds)

    def _process_queue(self) -> None:
        """Process all queued conversation contexts."""
        with self._lock:
            if self._processing:
                # Already processing, reschedule
                self._reset_timer()
                return

            if not self._queue:
                return

            self._processing = True
            contexts_to_process = self._queue.copy()
            self._queue.clear()
            self._timer = None

        logger.info("Processing %d queued memory updates", len(contexts_to_process))

        try:
            for context in contexts_to_process:
                try:
                    logger.debug("Updating memory for thread %s", context.thread_id)
                    if self._update_context_memory(context):
                        logger.info("Memory updated for thread %s", context.thread_id)
                    else:
                        logger.warning("Memory update skipped/failed for thread %s", context.thread_id)
                except Exception:
                    logger.exception("Error updating memory for thread %s", context.thread_id)

                # Small delay between updates to avoid rate limiting
                if len(contexts_to_process) > 1:
                    time.sleep(0.5)

        finally:
            with self._lock:
                self._processing = False

    def _update_context_memory(self, context: ConversationContext) -> bool:
        """Hand one conversation to the storage backend.

        Scope fan-out and per-scope write locking are the backend's concern;
        the queue only owns batching and debounce.
        """
        # Imported here to avoid a circular dependency at module load.
        from src.agents.memory.backend import get_memory_backend

        return get_memory_backend().ingest(
            context.messages,
            thread_id=context.thread_id,
            agent_name=context.agent_name,
            workspace_id=context.workspace_id or context.thread_id,
        )

    def _cancel_pending(self, thread_id: str) -> None:
        self._queue = [c for c in self._queue if c.thread_id != thread_id]
        if not self._queue and self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def queue_immediate(self, thread_id: str, messages: list[Any], agent_name: str | None = None, workspace_id: str | None = None) -> None:
        """Process memory for *messages* immediately in a background thread, bypassing debounce.

        Called by the pre-summarization hook to ensure messages that are about to be
        compressed out of the context window are captured in long-term memory before
        they disappear from state.  Unlike ``add()``, this does not touch the debounce
        timer and does not replace any pending entry for the same thread.
        """
        config = get_memory_config()
        if not config.enabled:
            return

        context = ConversationContext(thread_id=thread_id, messages=messages, agent_name=agent_name, workspace_id=workspace_id)

        with self._lock:
            # Prevent race/double-processing with debounced updates for this thread.
            self._cancel_pending(thread_id)

        def _run() -> None:
            last_error: Exception | None = None
            for attempt in range(1, 4):
                try:
                    self._update_context_memory(context)
                    return
                except Exception as exc:
                    last_error = exc
                    time.sleep(0.2 * attempt)
            logger.error("queue_immediate memory update failed for thread %s after retries: %s", thread_id, last_error)

        t = threading.Thread(target=_run, daemon=True, name=f"memory-immediate-{thread_id[:8]}")
        t.start()

    def flush(self) -> None:
        """Force immediate processing of the queue.

        This is useful for testing or graceful shutdown.
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

        self._process_queue()

    def clear(self) -> None:
        """Clear the queue without processing.

        This is useful for testing.
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._queue.clear()
            self._processing = False

    @property
    def pending_count(self) -> int:
        """Get the number of pending updates."""
        with self._lock:
            return len(self._queue)

    @property
    def is_processing(self) -> bool:
        """Check if the queue is currently being processed."""
        with self._lock:
            return self._processing


# Global singleton instance
_memory_queue: MemoryUpdateQueue | None = None
_queue_lock = threading.Lock()


def get_memory_queue() -> MemoryUpdateQueue:
    """Get the global memory update queue singleton.

    Returns:
        The memory update queue instance.
    """
    global _memory_queue
    with _queue_lock:
        if _memory_queue is None:
            _memory_queue = MemoryUpdateQueue()
        return _memory_queue


def reset_memory_queue() -> None:
    """Reset the global memory queue.

    This is useful for testing.
    """
    global _memory_queue
    with _queue_lock:
        if _memory_queue is not None:
            _memory_queue.clear()
        _memory_queue = None
