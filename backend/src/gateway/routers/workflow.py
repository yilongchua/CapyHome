"""Blocking workflow execution APIs.

The workflow runner keeps row state in the parent thread workspace and uses
short-lived child Work Mode threads only for per-row reasoning. Child threads
return JSON; durable writes are owned by this router.
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.config.app_config import get_app_config
from src.config.paths import get_paths

router = APIRouter(prefix="/api", tags=["workflow"])

WORKFLOW_JSON_VIRTUAL_PATH = "/mnt/user-data/workspace/runtime/workflow.json"
WORKFLOW_SQLITE_VIRTUAL_PATH = "/mnt/user-data/workspace/runtime/workflow.sqlite"
_ASSISTANT_ID = "work_agent"
_DEFAULT_CONSECUTIVE_FAILURES_LIMIT = 5
_TERMINAL_STATUSES = {"done", "stopped_failed_threshold"}


def _langgraph_url() -> str:
    return os.getenv("CAPYBARA_LANGGRAPH_URL") or os.getenv("LANGGRAPH_URL") or "http://localhost:2024"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_utc_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _round_seconds(value: float) -> float:
    return round(max(0.0, value), 2)


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def should_flush_completed_rows(previous_completed_rows: int, current_completed_rows: int, flush_every_completed_rows: int) -> bool:
    flush_every = max(1, int(flush_every_completed_rows or 1))
    previous = max(0, int(previous_completed_rows or 0))
    current = max(0, int(current_completed_rows or 0))
    return current > previous and current // flush_every > previous // flush_every


def _processed_row_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM workflow_rows WHERE status IN ('success', 'failed')").fetchone()[0])


class WorkflowPatchRequest(BaseModel):
    workflow: dict[str, Any] = Field(..., description="Full workflow.json payload to persist.")


class WorkflowExecuteResponse(BaseModel):
    thread_id: str
    status: Literal["accepted", "done", "stopped", "stopped_failed_threshold", "conflict", "failed"]
    completed_rows: int = 0
    claimed_rows: list[str] = Field(default_factory=list)
    failed_rows: list[str] = Field(default_factory=list)
    output_csv: str | None = None
    workflow: dict[str, Any] | None = None


class WorkflowStatusResponse(BaseModel):
    exists: bool
    initialized: bool = False
    workflow: dict[str, Any] | None = None


@dataclass
class _ActiveWorkflowRun:
    stop_requested: bool = False
    child_runs: dict[str, str] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_ACTIVE_RUNS: dict[str, _ActiveWorkflowRun] = {}
_ACTIVE_LOCK = asyncio.Lock()


def workflow_json_path(thread_id: str) -> Path:
    return get_paths().resolve_virtual_path(thread_id, WORKFLOW_JSON_VIRTUAL_PATH)


def workflow_sqlite_path(thread_id: str) -> Path:
    return get_paths().resolve_virtual_path(thread_id, WORKFLOW_SQLITE_VIRTUAL_PATH)


def resolve_thread_virtual_path(thread_id: str, virtual_path: str) -> Path:
    return get_paths().resolve_virtual_path(thread_id, virtual_path)


def output_virtual_path_for_source(source_virtual_path: str) -> str:
    path = Path(source_virtual_path)
    return str(path.with_name(f"{path.stem}_output{path.suffix}"))


def resolve_workflow_model_name(model_display_name: str | None) -> str | None:
    requested = str(model_display_name or "").strip()
    if not requested:
        return None
    app_config = get_app_config()
    lowered = requested.lower()
    for model in app_config.models:
        display_name = str(model.display_name or "").strip()
        if display_name and display_name.lower() == lowered:
            return model.name
    model_config = app_config.get_model_config(requested)
    return model_config.name if model_config is not None else None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        Path(tmp_name).replace(path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def read_workflow(thread_id: str) -> dict[str, Any]:
    path = workflow_json_path(thread_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="workflow.json does not exist.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to read workflow.json: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="workflow.json must contain an object.")
    return normalize_workflow(thread_id, payload)


def write_workflow(thread_id: str, workflow: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_workflow(thread_id, workflow)
    _atomic_write_json(workflow_json_path(thread_id), normalized)
    return normalized


def normalize_workflow(thread_id: str, workflow: dict[str, Any]) -> dict[str, Any]:
    next_workflow = dict(workflow)
    next_workflow["version"] = str(next_workflow.get("version") or "1")

    source = dict(next_workflow.get("source") or {})
    source_path = str(source.get("path") or "").strip()
    if not source_path:
        raise HTTPException(status_code=400, detail="workflow.source.path is required.")
    source.setdefault("type", "csv")

    runtime = dict(next_workflow.get("runtime") or {})
    runtime["workflow_json"] = WORKFLOW_JSON_VIRTUAL_PATH
    runtime["sqlite"] = WORKFLOW_SQLITE_VIRTUAL_PATH
    runtime["output_csv"] = str(runtime.get("output_csv") or output_virtual_path_for_source(source_path))

    row_task = dict(next_workflow.get("row_task") or {})
    row_task.setdefault("instruction", "")
    row_task.setdefault("input_fields", [])
    row_task.setdefault("output_schema", {})
    row_task.setdefault("failure_value", "failed run")
    row_task.setdefault("no_result_value", "")

    execution = dict(next_workflow.get("execution") or {})
    execution.setdefault("status", "ready")
    execution["max_parallel"] = max(1, int(execution.get("max_parallel") or 1))
    execution["flush_every_completed_rows"] = max(1, int(execution.get("flush_every_completed_rows") or 20))
    execution["flush_all"] = _as_bool(execution.get("flush_all"), default=False)
    execution["add_to_memory"] = _as_bool(execution.get("add_to_memory"), default=False)
    execution["compact_child_runs"] = _as_bool(execution.get("compact_child_runs"), default=True)
    execution["model_display_name"] = str(execution.get("model_display_name") or "").strip()
    execution["current_row_index"] = max(0, int(execution.get("current_row_index") or 0))
    execution["completed_rows"] = max(0, int(execution.get("completed_rows") or 0))
    execution["consecutive_failures"] = max(0, int(execution.get("consecutive_failures") or 0))
    execution["consecutive_failures_limit"] = max(1, int(execution.get("consecutive_failures_limit") or _DEFAULT_CONSECUTIVE_FAILURES_LIMIT))
    failure_rows = execution.get("failure_rows")
    execution["failure_rows"] = [str(row) for row in failure_rows] if isinstance(failure_rows, list) else []

    # Validate paths are inside the parent thread workspace now, so later
    # execution does not discover path errors mid-run.
    resolve_thread_virtual_path(thread_id, source_path)
    resolve_thread_virtual_path(thread_id, runtime["output_csv"])

    next_workflow["source"] = source
    next_workflow["runtime"] = runtime
    next_workflow["row_task"] = row_task
    next_workflow["execution"] = execution
    return next_workflow


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_rows (
            row_index INTEGER PRIMARY KEY,
            row_number TEXT NOT NULL,
            source_json TEXT NOT NULL,
            result_json TEXT,
            status TEXT NOT NULL,
            child_thread_id TEXT,
            child_run_id TEXT,
            started_at TEXT,
            completed_at TEXT,
            error TEXT
        )
        """
    )
    conn.commit()


def initialize_runtime(thread_id: str, workflow: dict[str, Any]) -> dict[str, Any]:
    workflow = normalize_workflow(thread_id, workflow)
    source_path = resolve_thread_virtual_path(thread_id, workflow["source"]["path"])
    if not source_path.exists():
        raise HTTPException(status_code=400, detail=f"Source CSV does not exist: {workflow['source']['path']}")
    if source_path.suffix.lower() != ".csv":
        raise HTTPException(status_code=400, detail="Only CSV workflow sources are supported in this implementation.")

    db_path = workflow_sqlite_path(thread_id)
    with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        if not columns:
            raise HTTPException(status_code=400, detail="Source CSV must include a header row.")
        rows = list(reader)

    with _connect(db_path) as conn:
        ensure_schema(conn)
        existing = conn.execute("SELECT COUNT(*) FROM workflow_rows").fetchone()[0]
        if existing == 0:
            conn.executemany(
                """
                INSERT INTO workflow_rows(row_index, row_number, source_json, status)
                VALUES (?, ?, ?, 'pending')
                """,
                [(idx, str(idx + 1), json.dumps(row, ensure_ascii=False)) for idx, row in enumerate(rows)],
            )
            conn.commit()

    workflow["source"]["columns"] = columns
    workflow["source"]["row_count"] = len(rows)
    workflow["runtime"]["sqlite"] = WORKFLOW_SQLITE_VIRTUAL_PATH
    workflow["runtime"]["output_csv"] = output_virtual_path_for_source(workflow["source"]["path"])
    workflow["execution"]["current_row_index"] = _lowest_pending_index(db_path)
    write_workflow(thread_id, workflow)
    return workflow


def _lowest_pending_index(db_path: Path) -> int:
    with _connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute("SELECT MIN(row_index) AS idx FROM workflow_rows WHERE status = 'pending'").fetchone()
        return int(row["idx"]) if row and row["idx"] is not None else 0


def update_execution_timing(thread_id: str, workflow: dict[str, Any]) -> dict[str, Any]:
    db_path = workflow_sqlite_path(thread_id)
    if not db_path.exists():
        return workflow

    durations: list[float] = []
    last_duration: float | None = None
    with _connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT started_at, completed_at
            FROM workflow_rows
            WHERE started_at IS NOT NULL
              AND completed_at IS NOT NULL
              AND status IN ('success', 'failed')
            ORDER BY completed_at
            """
        ).fetchall()
        pending_count = conn.execute("SELECT COUNT(*) FROM workflow_rows WHERE status = 'pending'").fetchone()[0]

    for row in rows:
        started = _parse_utc_iso(row["started_at"])
        completed_at = _parse_utc_iso(row["completed_at"])
        if not started or not completed_at:
            continue
        duration = (completed_at - started).total_seconds()
        if duration >= 0:
            durations.append(duration)
            last_duration = duration

    execution = dict(workflow["execution"])
    if last_duration is not None:
        execution["last_run_seconds"] = _round_seconds(last_duration)
    if durations:
        average = sum(durations) / len(durations)
        execution["average_run_seconds"] = _round_seconds(average)
        max_parallel = max(1, int(execution.get("max_parallel") or 1))
        execution["estimated_remaining_seconds"] = _round_seconds((int(pending_count) * average) / max_parallel)
    workflow["execution"] = execution
    return workflow


def claim_rows(thread_id: str, workflow: dict[str, Any]) -> list[dict[str, Any]]:
    db_path = workflow_sqlite_path(thread_id)
    limit = max(1, int(workflow["execution"].get("max_parallel") or 1))
    now = _utc_now_iso()
    with _connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT row_index, row_number, source_json
            FROM workflow_rows
            WHERE status = 'pending'
            ORDER BY row_index
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE workflow_rows SET status = 'running', started_at = ?, error = NULL WHERE row_index = ?",
                (now, row["row_index"]),
            )
        conn.commit()
    return [
        {
            "row_index": int(row["row_index"]),
            "row_number": str(row["row_number"]),
            "source": json.loads(row["source_json"]),
        }
        for row in rows
    ]


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif isinstance(block.get("content"), str):
                    parts.append(block["content"])
        return "\n".join(parts)
    return str(content)


def extract_final_ai_text(state: Any) -> str:
    values = state.get("values") if isinstance(state, dict) and isinstance(state.get("values"), dict) else state
    messages = values.get("messages") if isinstance(values, dict) else None
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if isinstance(message, dict):
            msg_type = str(message.get("type") or message.get("role") or "").lower()
            if msg_type in {"ai", "assistant"}:
                return _extract_text(message.get("content"))
        else:
            msg_type = str(getattr(message, "type", "") or "").lower()
            if msg_type in {"ai", "assistant"}:
                return _extract_text(getattr(message, "content", ""))
    return ""


def parse_child_result(raw_text: str, workflow: dict[str, Any]) -> tuple[str, dict[str, Any] | None, str | None]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        parsed = json.loads(text)
    except Exception as exc:
        return "failed", None, f"invalid_json: {exc}"
    if not isinstance(parsed, dict):
        return "failed", None, "result_json_not_object"

    output_schema = workflow["row_task"].get("output_schema")
    required_fields = list(output_schema.keys()) if isinstance(output_schema, dict) else []
    missing = [field for field in required_fields if field not in parsed]
    if missing:
        return "failed", parsed, f"missing_required_fields: {', '.join(missing)}"

    failure_value = str(workflow["row_task"].get("failure_value", "failed run"))
    if any(str(parsed.get(field)) == failure_value for field in required_fields):
        return "failed", parsed, "failed_run"
    return "success", parsed, None


def record_row_result(
    thread_id: str,
    workflow: dict[str, Any],
    row_index: int,
    *,
    status: str,
    result: dict[str, Any] | None,
    child_thread_id: str | None,
    child_run_id: str | None,
    error: str | None,
) -> dict[str, Any]:
    db_path = workflow_sqlite_path(thread_id)
    with _connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            """
            UPDATE workflow_rows
            SET status = ?, result_json = ?, child_thread_id = ?, child_run_id = ?,
                completed_at = ?, error = ?
            WHERE row_index = ?
            """,
            (
                status,
                json.dumps(result, ensure_ascii=False) if result is not None else None,
                child_thread_id,
                child_run_id,
                _utc_now_iso(),
                error,
                row_index,
            ),
        )
        row = conn.execute("SELECT row_number FROM workflow_rows WHERE row_index = ?", (row_index,)).fetchone()
        completed = conn.execute("SELECT COUNT(*) FROM workflow_rows WHERE status = 'success'").fetchone()[0]
        pending = conn.execute("SELECT MIN(row_index) AS idx FROM workflow_rows WHERE status = 'pending'").fetchone()
        conn.commit()

    execution = dict(workflow["execution"])
    row_number = str(row["row_number"] if row else row_index + 1)
    if status == "success":
        execution["consecutive_failures"] = 0
    elif status == "failed":
        execution["consecutive_failures"] = int(execution.get("consecutive_failures") or 0) + 1
        failure_rows = [str(item) for item in execution.get("failure_rows", [])]
        if row_number not in failure_rows:
            failure_rows.append(row_number)
        execution["failure_rows"] = failure_rows
    execution["completed_rows"] = int(completed)
    execution["current_row_index"] = int(pending["idx"]) if pending and pending["idx"] is not None else int(workflow["source"].get("row_count") or completed)
    consecutive_failures_limit = max(1, int(execution.get("consecutive_failures_limit") or _DEFAULT_CONSECUTIVE_FAILURES_LIMIT))
    if int(execution.get("consecutive_failures") or 0) >= consecutive_failures_limit:
        execution["status"] = "stopped_failed_threshold"
    elif execution["completed_rows"] >= int(workflow["source"].get("row_count") or 0):
        execution["status"] = "done"
    else:
        execution["status"] = "ready"
    workflow["execution"] = execution
    workflow = update_execution_timing(thread_id, workflow)
    return write_workflow(thread_id, workflow)


def reset_running_rows(thread_id: str) -> None:
    db_path = workflow_sqlite_path(thread_id)
    if not db_path.exists():
        return
    with _connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            "UPDATE workflow_rows SET status = 'pending', completed_at = ?, error = 'stopped' WHERE status = 'running'",
            (_utc_now_iso(),),
        )
        conn.commit()


def recover_workflow(thread_id: str) -> dict[str, Any]:
    workflow = read_workflow(thread_id)
    db_path = workflow_sqlite_path(thread_id)
    if db_path.exists():
        with _connect(db_path) as conn:
            ensure_schema(conn)
            conn.execute(
                """
                UPDATE workflow_rows
                SET status = 'pending',
                    result_json = NULL,
                    child_thread_id = NULL,
                    child_run_id = NULL,
                    started_at = NULL,
                    completed_at = NULL,
                    error = NULL
                WHERE status IN ('failed', 'running')
                """
            )
            completed = conn.execute("SELECT COUNT(*) FROM workflow_rows WHERE status = 'success'").fetchone()[0]
            pending = conn.execute("SELECT MIN(row_index) AS idx FROM workflow_rows WHERE status = 'pending'").fetchone()
            conn.commit()
        workflow["execution"]["completed_rows"] = int(completed)
        workflow["execution"]["current_row_index"] = (
            int(pending["idx"])
            if pending and pending["idx"] is not None
            else int(workflow["source"].get("row_count") or completed)
        )

    workflow["execution"]["consecutive_failures"] = 0
    workflow["execution"]["failure_rows"] = []
    workflow["execution"]["status"] = "ready"
    workflow = update_execution_timing(thread_id, workflow)
    return write_workflow(thread_id, workflow)


def export_output_csv(thread_id: str, workflow: dict[str, Any]) -> str:
    db_path = workflow_sqlite_path(thread_id)
    output_path = resolve_thread_virtual_path(thread_id, workflow["runtime"]["output_csv"])
    source_columns = list(workflow["source"].get("columns") or [])
    output_schema = workflow["row_task"].get("output_schema")
    output_columns = list(output_schema.keys()) if isinstance(output_schema, dict) else []
    columns = [*source_columns, *[col for col in output_columns if col not in source_columns]]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with _connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT source_json, result_json FROM workflow_rows ORDER BY row_index"
        ).fetchall()

    fd, tmp_name = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                source = json.loads(row["source_json"])
                result = json.loads(row["result_json"]) if row["result_json"] else {}
                writer.writerow({column: {**source, **result}.get(column, "") for column in columns})
        Path(tmp_name).replace(output_path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return workflow["runtime"]["output_csv"]


def _build_child_prompt(workflow: dict[str, Any], row: dict[str, Any]) -> str:
    output_schema = workflow["row_task"].get("output_schema")
    schema = output_schema if isinstance(output_schema, dict) and output_schema else {"result": "string"}
    return "\n".join(
        [
            "You are executing one row of a workflow.",
            "",
            "Workflow instruction:",
            str(workflow["row_task"].get("instruction") or "").strip(),
            "",
            "Rules:",
            '1. If websearch times out, return "failed run" for the requested output field.',
            '2. If no websearch results exist, return "" for the requested output field.',
            "3. Return only valid JSON.",
            "4. Do not write files.",
            "",
            "Row:",
            json.dumps(row["source"], ensure_ascii=False, indent=2),
            "",
            "Return only valid JSON matching this schema:",
            json.dumps(schema, ensure_ascii=False, indent=2),
        ]
    )


async def _execute_child_row(client: Any, thread_id: str, workflow: dict[str, Any], row: dict[str, Any], active: _ActiveWorkflowRun) -> tuple[int, str, dict[str, Any] | None, str | None, str | None, str | None]:
    async with active.lock:
        if active.stop_requested:
            return row["row_index"], "cancelled", None, None, None, "stopped"
    child_thread = await client.threads.create()
    child_thread_id = str(child_thread["thread_id"])
    prompt = _build_child_prompt(workflow, row)
    child_title = f"wf r{row['row_number']}"
    add_to_memory = _as_bool(workflow["execution"].get("add_to_memory"), default=False)
    compact_child_runs = _as_bool(workflow["execution"].get("compact_child_runs"), default=True)
    model_name = resolve_workflow_model_name(workflow["execution"].get("model_display_name"))
    context = {
        "thread_id": child_thread_id,
        "current_mode": "work",
        "mode": "work",
        "is_plan_mode": False,
        "background_followup": False,
        "plan_behavior": "work_interactive",
        "subagent_enabled": True,
        "thinking_enabled": True,
        "auto_mode": False,
        "add_to_memory": add_to_memory,
        "skip_title_generation": compact_child_runs,
        "workflow_child": True,
        "workflow_parent_thread_id": thread_id,
        "workflow_row_number": row["row_number"],
        "current_turn_text": prompt,
        "original_user_request": prompt,
    }
    metadata = {"trigger": "workflow_row", "parent_thread_id": thread_id, "row_number": row["row_number"]}
    if model_name:
        context["model_name"] = model_name
        metadata["model_display_name"] = workflow["execution"].get("model_display_name")
        metadata["model_name"] = model_name
    if compact_child_runs:
        context["compact_title"] = child_title
        metadata["title"] = child_title
    created = await client.runs.create(
        child_thread_id,
        _ASSISTANT_ID,
        input={"messages": [{"type": "human", "content": prompt}]},
        config=get_app_config().get_default_run_config(),
        context=context,
        metadata=metadata,
    )
    child_run_id = str(created.get("run_id") if isinstance(created, dict) else created)
    async with active.lock:
        should_cancel = active.stop_requested
        if not should_cancel:
            active.child_runs[child_thread_id] = child_run_id
    if should_cancel:
        try:
            await client.runs.cancel(child_thread_id, child_run_id, wait=False, action="interrupt")
        except Exception:
            pass
        return row["row_index"], "cancelled", None, child_thread_id, child_run_id, "stopped"
    try:
        await client.runs.join(child_thread_id, child_run_id)
        async with active.lock:
            if active.stop_requested:
                return row["row_index"], "cancelled", None, child_thread_id, child_run_id, "stopped"
        state = await client.threads.get_state(child_thread_id)
        raw_text = extract_final_ai_text(state)
        result_status, result, error = parse_child_result(raw_text, workflow)
        return row["row_index"], result_status, result, child_thread_id, child_run_id, error
    except Exception as exc:
        async with active.lock:
            if active.stop_requested:
                return row["row_index"], "cancelled", None, child_thread_id, child_run_id, "stopped"
        return row["row_index"], "failed", None, child_thread_id, child_run_id, str(exc)
    finally:
        async with active.lock:
            active.child_runs.pop(child_thread_id, None)


async def _delete_flushed_children(client: Any, thread_id: str, *, limit: int, flush_all: bool) -> None:
    db_path = workflow_sqlite_path(thread_id)
    cleanup_limit = max(1, int(limit or 1))
    statuses = ("success", "failed") if flush_all else ("success",)
    placeholders = ", ".join("?" for _ in statuses)
    with _connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            f"""
            SELECT row_index, status, child_thread_id
            FROM workflow_rows
            WHERE status IN ({placeholders})
              AND child_thread_id IS NOT NULL
              AND (error IS NULL OR error != 'child_thread_deleted')
            ORDER BY row_index
            LIMIT ?
            """,
            (*statuses, cleanup_limit),
        ).fetchall()
    for row in rows:
        child_thread_id = str(row["child_thread_id"])
        try:
            await client.threads.delete(child_thread_id)
            with _connect(db_path) as conn:
                conn.execute(
                    """
                    UPDATE workflow_rows
                    SET child_thread_id = NULL,
                        child_run_id = NULL,
                        error = CASE
                            WHEN status = 'success' AND error IS NULL THEN 'child_thread_deleted'
                            ELSE error
                        END
                    WHERE row_index = ?
                    """,
                    (row["row_index"],),
                )
                conn.commit()
        except Exception:
            continue


async def _cancel_active(thread_id: str, active: _ActiveWorkflowRun) -> None:
    async with active.lock:
        active.stop_requested = True
        child_runs = list(active.child_runs.items())
    try:
        from langgraph_sdk import get_client

        client = get_client(url=_langgraph_url())
        for child_thread_id, child_run_id in child_runs:
            try:
                await client.runs.cancel(child_thread_id, child_run_id, wait=False, action="interrupt")
            except Exception:
                continue
    finally:
        try:
            workflow = read_workflow(thread_id)
            reset_running_rows(thread_id)
            workflow["execution"]["status"] = "stopped"
            write_workflow(thread_id, workflow)
            if workflow_sqlite_path(thread_id).exists():
                export_output_csv(thread_id, workflow)
        except Exception:
            pass


@router.get("/threads/{thread_id}/workflow", response_model=WorkflowStatusResponse)
async def get_workflow(thread_id: str) -> WorkflowStatusResponse:
    path = workflow_json_path(thread_id)
    if not path.exists():
        return WorkflowStatusResponse(exists=False, initialized=False, workflow=None)
    workflow = read_workflow(thread_id)
    return WorkflowStatusResponse(exists=True, initialized=workflow_sqlite_path(thread_id).exists(), workflow=workflow)


@router.patch("/threads/{thread_id}/workflow", response_model=WorkflowStatusResponse)
async def patch_workflow(thread_id: str, request: WorkflowPatchRequest) -> WorkflowStatusResponse:
    workflow = write_workflow(thread_id, request.workflow)
    return WorkflowStatusResponse(exists=True, initialized=workflow_sqlite_path(thread_id).exists(), workflow=workflow)


@router.post("/threads/{thread_id}/workflow/initialize", response_model=WorkflowStatusResponse)
async def initialize_workflow(thread_id: str) -> WorkflowStatusResponse:
    workflow = initialize_runtime(thread_id, read_workflow(thread_id))
    return WorkflowStatusResponse(exists=True, initialized=True, workflow=workflow)


@router.post("/threads/{thread_id}/workflow/export", response_model=WorkflowExecuteResponse)
async def export_workflow(thread_id: str) -> WorkflowExecuteResponse:
    workflow = read_workflow(thread_id)
    if not workflow_sqlite_path(thread_id).exists():
        workflow = initialize_runtime(thread_id, workflow)
    output = export_output_csv(thread_id, workflow)
    return WorkflowExecuteResponse(thread_id=thread_id, status="accepted", output_csv=output, workflow=workflow)


@router.post("/threads/{thread_id}/workflow/stop", response_model=WorkflowExecuteResponse)
async def stop_workflow(thread_id: str) -> WorkflowExecuteResponse:
    async with _ACTIVE_LOCK:
        active = _ACTIVE_RUNS.get(thread_id)
    if active:
        await _cancel_active(thread_id, active)
    workflow = read_workflow(thread_id)
    reset_running_rows(thread_id)
    workflow["execution"]["status"] = "stopped"
    workflow = write_workflow(thread_id, workflow)
    output = export_output_csv(thread_id, workflow) if workflow_sqlite_path(thread_id).exists() else workflow["runtime"]["output_csv"]
    return WorkflowExecuteResponse(thread_id=thread_id, status="stopped", output_csv=output, workflow=workflow)


@router.get("/threads/{thread_id}/workflow/status", response_model=WorkflowStatusResponse)
async def workflow_status(thread_id: str) -> WorkflowStatusResponse:
    return await get_workflow(thread_id)


@router.post("/threads/{thread_id}/workflow/recover", response_model=WorkflowStatusResponse)
async def recover_workflow_route(thread_id: str) -> WorkflowStatusResponse:
    workflow = recover_workflow(thread_id)
    return WorkflowStatusResponse(exists=True, initialized=workflow_sqlite_path(thread_id).exists(), workflow=workflow)


@router.post("/threads/{thread_id}/workflow/execute-next", response_model=WorkflowExecuteResponse)
async def execute_next(thread_id: str) -> WorkflowExecuteResponse:
    async with _ACTIVE_LOCK:
        if thread_id in _ACTIVE_RUNS:
            raise HTTPException(status_code=409, detail="Workflow execution is already running for this thread.")
        active = _ActiveWorkflowRun()
        _ACTIVE_RUNS[thread_id] = active

    try:
        from langgraph_sdk import get_client

        workflow = read_workflow(thread_id)
        if workflow["execution"].get("status") in _TERMINAL_STATUSES:
            return WorkflowExecuteResponse(thread_id=thread_id, status=workflow["execution"]["status"], workflow=workflow, output_csv=workflow["runtime"]["output_csv"])
        if not workflow_sqlite_path(thread_id).exists():
            workflow = initialize_runtime(thread_id, workflow)

        rows = claim_rows(thread_id, workflow)
        if not rows:
            workflow["execution"]["status"] = "done"
            workflow = write_workflow(thread_id, workflow)
            output = export_output_csv(thread_id, workflow)
            return WorkflowExecuteResponse(thread_id=thread_id, status="done", output_csv=output, workflow=workflow)

        client = get_client(url=_langgraph_url())
        results = await asyncio.gather(*[_execute_child_row(client, thread_id, workflow, row, active) for row in rows])

        claimed_rows: list[str] = []
        failed_rows: list[str] = []
        with _connect(workflow_sqlite_path(thread_id)) as conn:
            ensure_schema(conn)
            previous_processed_rows = _processed_row_count(conn)
        for row_index, status, result, child_thread_id, child_run_id, error in results:
            claimed_rows.append(str(row_index + 1))
            db_status = "failed" if status == "failed" else ("pending" if status == "cancelled" else "success")
            workflow = record_row_result(
                thread_id,
                workflow,
                row_index,
                status=db_status,
                result=result,
                child_thread_id=child_thread_id,
                child_run_id=child_run_id,
                error=error,
            )
            if db_status == "failed":
                failed_rows.append(str(row_index + 1))

        with _connect(workflow_sqlite_path(thread_id)) as conn:
            ensure_schema(conn)
            current_processed_rows = _processed_row_count(conn)
        flush_every_completed_rows = int(workflow["execution"].get("flush_every_completed_rows") or 20)
        flush_all = bool(workflow["execution"].get("flush_all") is True)
        should_export = (
            active.stop_requested
            or workflow["execution"].get("status") in _TERMINAL_STATUSES
            or should_flush_completed_rows(previous_processed_rows, current_processed_rows, flush_every_completed_rows)
        )
        output = export_output_csv(thread_id, workflow) if should_export else workflow["runtime"]["output_csv"]
        if should_export:
            await _delete_flushed_children(client, thread_id, limit=flush_every_completed_rows, flush_all=flush_all)
        if active.stop_requested:
            reset_running_rows(thread_id)
            workflow["execution"]["status"] = "stopped"
            workflow = write_workflow(thread_id, workflow)
        response_status = workflow["execution"].get("status")
        if response_status not in {"done", "stopped", "stopped_failed_threshold"}:
            response_status = "accepted"
        return WorkflowExecuteResponse(
            thread_id=thread_id,
            status=response_status,
            completed_rows=int(workflow["execution"].get("completed_rows") or 0),
            claimed_rows=claimed_rows,
            failed_rows=failed_rows,
            output_csv=output,
            workflow=workflow,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to execute workflow: {exc}") from exc
    finally:
        async with _ACTIVE_LOCK:
            _ACTIVE_RUNS.pop(thread_id, None)
