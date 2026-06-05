"""Regression tests for MountFolderMiddleware.before_agent state handling.

Focus: unmount must clear the stale `mounted_path` that lives in checkpointed
`thread_data`. Previously the middleware returned None on the "not mounted"
paths, leaving the old path in state so the folder appeared mounted forever.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents.middlewares import mount_folder_middleware as mfm
from src.config.paths import Paths


@pytest.fixture()
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Paths:
    p = Paths(tmp_path)
    monkeypatch.setattr(mfm, "get_paths", lambda: p)
    return p


def _runtime(thread_id: str) -> SimpleNamespace:
    return SimpleNamespace(context={"thread_id": thread_id})


def _write_mount(paths: Paths, thread_id: str, folder: Path) -> None:
    user_data = paths.sandbox_user_data_dir(thread_id)
    user_data.mkdir(parents=True, exist_ok=True)
    (user_data / "dreamy_mount.json").write_text(f'{{"path": "{folder}"}}', encoding="utf-8")


def test_before_agent_sets_mounted_path(paths: Paths, tmp_path: Path):
    thread_id = "t_mount"
    folder = tmp_path / "mounted"
    folder.mkdir()
    _write_mount(paths, thread_id, folder)

    result = mfm.MountFolderMiddleware().before_agent({}, _runtime(thread_id))

    assert result == {"thread_data": {"mounted_path": str(folder)}}


def test_unmount_clears_stale_mounted_path(paths: Paths, tmp_path: Path):
    """Config file gone (unmounted) but state still carries the old path."""
    thread_id = "t_unmount"
    # No dreamy_mount.json written → simulates an unmount.
    state = {"thread_data": {"workspace_path": "/ws", "mounted_path": "/old/mount"}}

    result = mfm.MountFolderMiddleware().before_agent(state, _runtime(thread_id))

    assert result == {"thread_data": {"workspace_path": "/ws"}}
    assert "mounted_path" not in result["thread_data"]


def test_unmount_is_noop_when_nothing_to_clear(paths: Paths):
    """Never mounted → no stale key → return None (no spurious state write)."""
    thread_id = "t_never"
    state = {"thread_data": {"workspace_path": "/ws"}}

    result = mfm.MountFolderMiddleware().before_agent(state, _runtime(thread_id))

    assert result is None


def test_missing_folder_clears_mounted_path(paths: Paths, tmp_path: Path):
    """Config points at a folder that no longer exists → treat as unmounted."""
    thread_id = "t_gone"
    _write_mount(paths, thread_id, tmp_path / "does_not_exist")
    state = {"thread_data": {"mounted_path": "/old/mount"}}

    result = mfm.MountFolderMiddleware().before_agent(state, _runtime(thread_id))

    assert result == {"thread_data": {}}
