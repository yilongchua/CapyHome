"""AsyncSqliteSaver subclass that satisfies the langgraph_api FullCheckpointerProtocol.

langgraph_api 0.7+ detects three optional methods at startup and warns when they
are absent on a custom checkpointer:

- adelete_for_runs  – required for multitask_strategy='rollback' cleanup
- aprune            – required for thread history pruning (keep_latest)
- acopy_thread      – optional; generic fallback is functional but slower

This subclass adds all three against the existing SQLite tables:

  checkpoints(thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,
              type, checkpoint, metadata)
  writes(thread_id, checkpoint_ns, checkpoint_id, task_id, idx,
         channel, type, value)
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Sequence

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

logger = logging.getLogger(__name__)

# Default SQLite busy timeout (ms). The base AsyncSqliteSaver.setup() enables WAL
# but leaves busy_timeout at SQLite's default of 0, so any momentary lock (a WAL
# checkpoint, a concurrent read/write on the shared checkpoints.db) makes a write
# fail *immediately* with "database is locked". That is what made deleting old /
# interrupted threads return a 500 and surface as "can't delete chat" in the UI.
# A non-zero busy_timeout makes writers wait for the lock instead of erroring.
_DEFAULT_BUSY_TIMEOUT_MS = 30_000


def _busy_timeout_ms() -> int:
    raw = os.getenv("CAPYHOME_SQLITE_BUSY_TIMEOUT_MS")
    if not raw:
        return _DEFAULT_BUSY_TIMEOUT_MS
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_BUSY_TIMEOUT_MS
    return value if value >= 0 else _DEFAULT_BUSY_TIMEOUT_MS


class ExtendedAsyncSqliteSaver(AsyncSqliteSaver):
    """AsyncSqliteSaver with the langgraph_api FullCheckpointerProtocol methods."""

    async def setup(self) -> None:
        """Run the base setup (tables + WAL), then apply a non-zero busy_timeout.

        busy_timeout is a per-connection PRAGMA, so it is set once on the saver's
        long-lived connection right after the connection/tables are established.
        """
        was_setup = self.is_setup
        await super().setup()
        if was_setup:
            return
        timeout_ms = _busy_timeout_ms()
        async with self.lock:
            await self.conn.execute(f"PRAGMA busy_timeout={timeout_ms}")
            await self.conn.commit()
        logger.info("SQLite checkpointer busy_timeout set to %dms", timeout_ms)

    async def adelete_for_runs(self, run_ids: Iterable[str]) -> None:
        """Delete all checkpoints and writes belonging to the given run IDs.

        run_id is stored in the JSON metadata column by the langgraph_api adapter
        (_enrich_metadata). SQLite's json_extract lets us filter without loading
        every row into Python.
        """
        ids = list(run_ids)
        if not ids:
            return
        await self.setup()
        async with self.lock:
            for run_id in ids:
                await self.conn.execute(
                    """DELETE FROM writes
                       WHERE (thread_id, checkpoint_ns, checkpoint_id) IN (
                           SELECT thread_id, checkpoint_ns, checkpoint_id
                           FROM checkpoints
                           WHERE json_extract(metadata, '$.run_id') = ?
                       )""",
                    (str(run_id),),
                )
                await self.conn.execute(
                    "DELETE FROM checkpoints WHERE json_extract(metadata, '$.run_id') = ?",
                    (str(run_id),),
                )
            await self.conn.commit()

    async def aprune(
        self,
        thread_ids: Sequence[str],
        *,
        strategy: str = "keep_latest",
    ) -> None:
        """Prune old checkpoints to prevent unbounded storage growth.

        strategy='keep_latest'  – retain only the most recent checkpoint per
                                  namespace; delete all earlier ones.
        strategy='delete_all'   – remove the thread entirely (delegates to
                                  adelete_thread).
        """
        if strategy == "delete_all":
            for thread_id in thread_ids:
                await self.adelete_thread(str(thread_id))
            return

        if not thread_ids:
            return

        if strategy == "keep_latest":
            await self.setup()
            async with self.lock:
                for thread_id in thread_ids:
                    tid = str(thread_id)
                    # Delete writes for non-latest checkpoints in each namespace.
                    await self.conn.execute(
                        """DELETE FROM writes
                           WHERE thread_id = ?
                             AND checkpoint_id NOT IN (
                                 SELECT checkpoint_id FROM checkpoints
                                 WHERE thread_id = ?
                                 GROUP BY checkpoint_ns
                                 HAVING checkpoint_id = MAX(checkpoint_id)
                             )""",
                        (tid, tid),
                    )
                    # Delete non-latest checkpoints in each namespace.
                    await self.conn.execute(
                        """DELETE FROM checkpoints
                           WHERE thread_id = ?
                             AND checkpoint_id NOT IN (
                                 SELECT checkpoint_id FROM checkpoints
                                 WHERE thread_id = ?
                                 GROUP BY checkpoint_ns
                                 HAVING checkpoint_id = MAX(checkpoint_id)
                             )""",
                        (tid, tid),
                    )
                await self.conn.commit()

    async def acopy_thread(
        self,
        source_thread_id: str,
        target_thread_id: str,
    ) -> None:
        """Bulk-copy all checkpoints and writes from one thread to another.

        Faster than the generic langgraph_api fallback (which replays via
        aput/aput_writes one checkpoint at a time) because it uses a single
        INSERT ... SELECT per table.
        """
        src = str(source_thread_id)
        dst = str(target_thread_id)
        await self.setup()
        async with self.lock:
            await self.conn.execute(
                """INSERT OR REPLACE INTO checkpoints
                       (thread_id, checkpoint_ns, checkpoint_id,
                        parent_checkpoint_id, type, checkpoint, metadata)
                   SELECT ?, checkpoint_ns, checkpoint_id,
                          parent_checkpoint_id, type, checkpoint, metadata
                   FROM checkpoints WHERE thread_id = ?""",
                (dst, src),
            )
            await self.conn.execute(
                """INSERT OR REPLACE INTO writes
                       (thread_id, checkpoint_ns, checkpoint_id,
                        task_id, idx, channel, type, value)
                   SELECT ?, checkpoint_ns, checkpoint_id,
                          task_id, idx, channel, type, value
                   FROM writes WHERE thread_id = ?""",
                (dst, src),
            )
            await self.conn.commit()
