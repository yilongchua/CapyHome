import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/system", tags=["system"])

_DEFAULT_WORKERS = 5
_MIN_WORKERS = 1
_MAX_WORKERS = 20


def _repo_root() -> Path:
    """Walk up from CWD until we find the Makefile (repo root)."""
    for candidate in (Path.cwd(), Path.cwd().parent):
        if (candidate / "Makefile").is_file():
            return candidate
    return Path.cwd().parent


def _env_path() -> Path:
    """Find the root .env file. Gateway CWD is backend/, so parent is repo root."""
    for candidate in (Path.cwd() / ".env", Path.cwd().parent / ".env"):
        if candidate.is_file():
            return candidate
    return Path.cwd().parent / ".env"


def _read_worker_count() -> int:
    path = _env_path()
    if not path.is_file():
        return _DEFAULT_WORKERS
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("LANGGRAPH_WORKERS="):
            value = stripped.split("=", 1)[1].strip().strip("\"'")
            try:
                return int(value)
            except ValueError:
                pass
    return _DEFAULT_WORKERS


def _write_worker_count(count: int) -> None:
    path = _env_path()
    pattern = re.compile(r"^LANGGRAPH_WORKERS\s*=.*$")
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()
        new_lines, found = [], False
        for line in lines:
            if pattern.match(line.strip()):
                new_lines.append(f"LANGGRAPH_WORKERS={count}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"LANGGRAPH_WORKERS={count}")
        path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    else:
        path.write_text(f"LANGGRAPH_WORKERS={count}\n", encoding="utf-8")


class WorkerConfigResponse(BaseModel):
    workers: int
    restart_required: bool = False


class WorkerConfigUpdateRequest(BaseModel):
    workers: int = Field(..., ge=_MIN_WORKERS, le=_MAX_WORKERS)


@router.get("/workers", response_model=WorkerConfigResponse)
def get_worker_config() -> WorkerConfigResponse:
    """Return the saved LANGGRAPH_WORKERS value from .env."""
    return WorkerConfigResponse(workers=_read_worker_count())


@router.put("/workers", response_model=WorkerConfigResponse)
def update_worker_config(request: WorkerConfigUpdateRequest) -> WorkerConfigResponse:
    """Persist LANGGRAPH_WORKERS to .env. Requires server restart to apply."""
    try:
        _write_worker_count(request.workers)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to write .env: {e}") from e
    return WorkerConfigResponse(workers=request.workers, restart_required=True)


class RestartRequest(BaseModel):
    mode: Literal["dev", "start"] = "dev"


class RestartResponse(BaseModel):
    status: str
    mode: str


@router.post("/restart", response_model=RestartResponse)
def restart_services(request: RestartRequest) -> RestartResponse:
    """Trigger a full service restart. Returns immediately; restart happens 2 s later."""
    root = _repo_root()
    cmd = f"sleep 2 && make stop && make {request.mode}"

    def _fire() -> None:
        time.sleep(0)  # yield before spawning
        subprocess.Popen(
            ["bash", "-c", cmd],
            cwd=str(root),
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    threading.Thread(target=_fire, daemon=True).start()
    return RestartResponse(status="restarting", mode=request.mode)
