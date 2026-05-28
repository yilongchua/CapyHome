"""Helpers for virtual <-> physical sandbox path mapping.

This module is intentionally dependency-light so middleware code can use it
without importing the heavier `src.sandbox.tools` module (which pulls in
tool/runtime plumbing and can participate in import cycles).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.config.paths import VIRTUAL_PATH_PREFIX

ThreadDataLike = Mapping[str, Any]


def replace_virtual_path(path: str, thread_data: ThreadDataLike | None) -> str:
    """Replace virtual /mnt/user-data paths with actual thread data paths."""
    if not path.startswith(VIRTUAL_PATH_PREFIX):
        return path
    if thread_data is None:
        return path

    # `uploads` is a legacy virtual subdir kept for backward compatibility with
    # checkpointed conversations that reference `/mnt/user-data/uploads/...`.
    # New paths use `/mnt/user-data/workspace/uploads/...`, which is resolved via
    # the `workspace` mapping (uploads physically lives at `{workspace}/uploads`).
    path_mapping = {
        "workspace": thread_data.get("workspace_path"),
        "uploads": thread_data.get("uploads_path"),
        "outputs": thread_data.get("outputs_path"),
        "mounted": thread_data.get("mounted_path"),
    }

    relative_path = path[len(VIRTUAL_PATH_PREFIX) :].lstrip("/")
    if not relative_path:
        return path

    parts = relative_path.split("/", 1)
    subdir = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    # Canonicalize legacy outputs virtual paths to workspace.
    # From this point forward, user-facing file operations are workspace-first.
    if subdir == "outputs":
        subdir = "workspace"

    actual_base = path_mapping.get(subdir)
    if actual_base is None:
        return path
    if rest:
        return f"{actual_base}/{rest}"
    return actual_base


def to_virtual_path(path: str | None, thread_data: ThreadDataLike | None) -> str | None:
    """Inverse of `replace_virtual_path` for artifact serialization."""
    if not path:
        return path
    if thread_data is None:
        return path
    if path.startswith(VIRTUAL_PATH_PREFIX):
        return path

    # `uploads_path` is intentionally omitted: it nests under `workspace_path`
    # ({workspace}/uploads), so a physical uploads file is reverse-mapped to
    # the canonical `/mnt/user-data/workspace/uploads/...` via the workspace
    # entry. Including `uploads` would emit the legacy `/mnt/user-data/uploads/...`
    # form, which is no longer canonical.
    candidates = [
        ("workspace", thread_data.get("workspace_path")),
        ("outputs", thread_data.get("outputs_path")),
        ("mounted", thread_data.get("mounted_path")),
    ]
    candidates_sorted = sorted(
        ((subdir, base) for subdir, base in candidates if base),
        key=lambda kv: -len(str(kv[1])),
    )
    for subdir, base in candidates_sorted:
        base_str = str(base)
        if path == base_str:
            normalized_subdir = "workspace" if subdir == "outputs" else subdir
            return f"{VIRTUAL_PATH_PREFIX}/{normalized_subdir}"
        if path.startswith(base_str + "/"):
            rest = path[len(base_str) + 1 :]
            normalized_subdir = "workspace" if subdir == "outputs" else subdir
            return f"{VIRTUAL_PATH_PREFIX}/{normalized_subdir}/{rest}"
    return path
