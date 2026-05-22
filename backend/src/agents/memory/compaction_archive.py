"""Compaction archive persistence helpers + audit markdown reports."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AnyMessage

from src.config.paths import get_paths

_RUNTIME_RETENTION_DAYS = 7


def _utc_now_iso_z() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _compaction_log_path(thread_id: str) -> Path:
    return get_paths().thread_dir(thread_id) / "compaction_log.jsonl"


def append_compaction_entry(thread_id: str, payload: dict[str, Any]) -> Path:
    """Append one compaction event entry to the thread archive."""
    path = _compaction_log_path(thread_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": _utc_now_iso_z(), **payload}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def read_compaction_entries(thread_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Read recent compaction archive entries for a thread."""
    path = _compaction_log_path(thread_id)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-max(1, limit) :]


# ---------------------------------------------------------------------------
# Phase D/E — Markdown audit reports written into the sandbox-visible workspace
# ---------------------------------------------------------------------------


def _message_preview(msg: AnyMessage, max_len: int = 200) -> str:
    content = str(getattr(msg, "content", "") or "").strip()
    msg_type = getattr(msg, "type", "?")
    name = getattr(msg, "name", "") or ""
    if msg_type == "human":
        prefix = f"[{name}] " if name else ""
        return f"{prefix}{content[:max_len]}"
    elif msg_type == "ai":
        return content[:max_len]
    elif msg_type == "tool":
        return f"[{name} tool] {content[:max_len]}"
    return f"[{name}] {content[:max_len]}" if name else content[:max_len]


def _cleanup_expired_runtime_files(runtime_dir: Path, retention_days: int = _RUNTIME_RETENTION_DAYS) -> None:
    """Remove .runtime/ compaction files older than retention_days."""
    cutoff = time.time() - retention_days * 86400
    if not runtime_dir.exists():
        return
    for f in runtime_dir.iterdir():
        if f.is_file() and f.name.startswith("compaction_"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass


def write_compaction_markdown(
    thread_id: str,
    trigger: str,
    compressed_count: int,
    kept_count: int,
    summary_text: str,
    to_summarize: list[AnyMessage] | None = None,
    preserved: list[AnyMessage] | None = None,
    state: dict | None = None,
) -> Path:
    """Write a human-readable compaction audit report to ``.runtime/compaction_{ts}.md``.

    The file lands inside the sandbox-visible workspace (``/mnt/user-data/workspace/.runtime/``)
    so the user can browse it in the Directories tab. Old reports are cleaned up
    after ``_RUNTIME_RETENTION_DAYS``.
    """
    paths = get_paths()
    runtime_dir = paths.sandbox_work_dir(thread_id) / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    filepath = runtime_dir / f"compaction_{ts}.md"

    lines: list[str] = [
        f"# Compaction Report — {ts}",
        "",
        "## Trigger",
        f"- Type: `{trigger}`",
        f"- Messages compressed: {compressed_count}",
        f"- Messages kept: {kept_count}",
        "",
        "## Summary",
        (summary_text.strip() or "_(no summary text)_"),
        "",
    ]

    lines.append("## Compressed Messages")
    if to_summarize:
        lines.append(f"_{len(to_summarize)} messages compressed_\n")
        for i, m in enumerate(to_summarize):
            lines.append(f"1. **{getattr(m, 'type', '?')}**: {_message_preview(m)}")
    else:
        lines.append("_(none)_")
    lines.append("")

    lines.append("## Preserved Messages")
    if preserved:
        lines.append(f"_{len(preserved)} messages kept_\n")
        for i, m in enumerate(preserved):
            lines.append(f"1. **{getattr(m, 'type', '?')}**: {_message_preview(m)}")
    else:
        lines.append("_(none)_")
    lines.append("")

    if isinstance(state, dict):
        todo_graph = state.get("todo_graph")
        if isinstance(todo_graph, dict):
            nodes = todo_graph.get("nodes")
            if isinstance(nodes, list) and nodes:
                lines.append("## Todo State")
                for node in nodes[:10]:
                    if isinstance(node, dict):
                        sid = node.get("id", "?")
                        status = node.get("status", "?")
                        content = str(node.get("content", ""))[:100]
                        lines.append(f"- [{status}] {sid}: {content}")
                lines.append("")

        artifacts = state.get("artifacts")
        if isinstance(artifacts, list) and artifacts:
            lines.append("## Artifacts")
            for p in artifacts[-8:]:
                if isinstance(p, str):
                    lines.append(f"- {p}")
            lines.append("")

    content = "\n".join(lines)
    filepath.write_text(content, encoding="utf-8")

    _cleanup_expired_runtime_files(runtime_dir)

    return filepath
