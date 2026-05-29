"""Execution trace schema + helpers.

This module defines a persisted thread-state structure for run-level trace data
and utilities to build + stream events in a stable wire format.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Literal

from langgraph.config import get_stream_writer
from pydantic import ConfigDict, Field, model_validator

from src.schema import CapyBaseModel, CapyEvent

TRACE_SCHEMA_VERSION = "v1"
TRACE_STREAM_EVENT_TYPE = "trace_event.v1"
TRACE_MAX_PAYLOAD_CHARS = 4000
TRACE_RUN_ID_KEY = "_execution_trace_run_id"
TRACE_SEQ_KEY = "_execution_trace_seq"
TRACE_RUN_STARTED_KEY = "_execution_trace_run_started"
TRACE_MAX_RUNS_RETAINED = 24
TRACE_MAX_EVENTS_PER_RUN = 320

TraceStage = Literal["lead", "planner", "evaluator", "subagent", "harness"]
TraceThinkingSource = Literal["raw", "summary"]
TraceThinkingPayload = dict[str, Any]
TraceTokenUsagePayload = dict[str, Any]
ExecutionTraceEventPayload = dict[str, Any]
ExecutionTraceRunPayload = dict[str, Any]
ExecutionTraceStatePayload = dict[str, Any]


class TraceThinking(CapyBaseModel):
    source: TraceThinkingSource = Field(..., description="Whether thinking is raw provider output or generated summary")
    content: str = Field(..., description="Thinking text shown in the trace panel")

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class TraceTokenUsage(CapyBaseModel):
    input_tokens: int | None = Field(default=None, ge=0, description="Input token count")
    output_tokens: int | None = Field(default=None, ge=0, description="Output token count")
    total_tokens: int | None = Field(default=None, ge=0, description="Total token count")

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ExecutionTraceEvent(CapyEvent):
    id: str | None = Field(default=None, description="Stable event id, usually run_id:seq")
    schema_: str = Field(default=TRACE_SCHEMA_VERSION, alias="schema", description="Execution trace schema version")
    run_id: str = Field(..., description="Run identifier shared by events from one agent run")
    turn_id: str | None = Field(default=None, description="Optional turn or tool-call id")
    stage: TraceStage = Field(..., description="Trace stage that emitted the event")
    event_type: str = Field(..., description="Machine-readable event type")
    timestamp: float = Field(..., description="Unix timestamp in seconds")
    seq: int | None = Field(default=None, ge=0, description="Monotonic sequence number within the run")
    status: str = Field(..., description="Event status")
    payload: dict[str, Any] = Field(default_factory=dict, description="Structured event payload")
    token_usage: TraceTokenUsage | None = Field(default=None, description="Optional token usage")
    thinking: TraceThinking | None = Field(default=None, description="Optional reasoning/thinking payload")
    assistant_message_id: str | None = Field(default=None, description="Optional related assistant message id")
    task_id: str | None = Field(default=None, description="Optional related subagent task id")
    payload_truncated: bool | None = Field(default=None, description="Whether payload was truncated for storage/wire")
    payload_original_chars: int | None = Field(default=None, ge=0, description="Original serialized payload length")

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def _validate_truncation_metadata(self) -> ExecutionTraceEvent:
        if self.payload_truncated and not self.payload_original_chars:
            raise ValueError("payload_original_chars must be positive when payload_truncated is true")
        return self


class ExecutionTraceRun(CapyBaseModel):
    run_id: str = Field(..., description="Run identifier")
    started_at: float | None = Field(default=None, description="Unix timestamp when this run started")
    updated_at: float | None = Field(default=None, description="Unix timestamp when this run was last updated")
    events: list[ExecutionTraceEvent] = Field(default_factory=list, description="Trace events for this run")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ExecutionTraceState(CapyBaseModel):
    version: str = Field(default=TRACE_SCHEMA_VERSION, description="Execution trace schema version")
    runs: dict[str, ExecutionTraceRun] = Field(default_factory=dict, description="Trace runs keyed by run id")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _dump_model(model: CapyBaseModel, *, exclude_none: bool = True) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_none=exclude_none, by_alias=True)


def _trace_event_payload(value: ExecutionTraceEvent | dict[str, Any]) -> ExecutionTraceEventPayload:
    if isinstance(value, ExecutionTraceEvent):
        return _dump_model(value, exclude_none=True)
    if isinstance(value, dict):
        return _dump_model(ExecutionTraceEvent.model_validate(value), exclude_none=True)
    return {}


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, CapyBaseModel):
        return _dump_model(value)
    if isinstance(value, dict):
        return value
    return {}


def resolve_trace_run_id(runtime: Any) -> str:
    """Resolve (or lazily create) a per-run trace id."""
    context = getattr(runtime, "context", None)
    if not isinstance(context, dict):
        return "run-unknown"

    native_run_id = context.get("run_id")
    if isinstance(native_run_id, str) and native_run_id:
        context[TRACE_RUN_ID_KEY] = native_run_id
        return native_run_id

    existing = context.get(TRACE_RUN_ID_KEY)
    if isinstance(existing, str) and existing:
        return existing

    generated = f"run-{uuid.uuid4().hex[:12]}"
    context[TRACE_RUN_ID_KEY] = generated
    return generated


def next_trace_seq(runtime: Any) -> int:
    context = getattr(runtime, "context", None)
    if not isinstance(context, dict):
        return 1
    current = context.get(TRACE_SEQ_KEY, 0)
    if not isinstance(current, int) or current < 0:
        current = 0
    current += 1
    context[TRACE_SEQ_KEY] = current
    return current


def run_started_emitted(runtime: Any) -> bool:
    context = getattr(runtime, "context", None)
    if not isinstance(context, dict):
        return False
    return bool(context.get(TRACE_RUN_STARTED_KEY))


def mark_run_started(runtime: Any) -> None:
    context = getattr(runtime, "context", None)
    if not isinstance(context, dict):
        return
    context[TRACE_RUN_STARTED_KEY] = True


def _serialize_payload(payload: Any) -> tuple[dict[str, Any], bool, int]:
    if payload is None:
        return {}, False, 0
    if isinstance(payload, dict):
        as_dict = payload
    else:
        as_dict = {"value": payload}

    serialized = json.dumps(as_dict, ensure_ascii=False, sort_keys=True, default=str)
    original_chars = len(serialized)
    if original_chars <= TRACE_MAX_PAYLOAD_CHARS:
        return as_dict, False, original_chars
    preview = serialized[:TRACE_MAX_PAYLOAD_CHARS] + "..."
    return {
        "_truncated": True,
        "preview": preview,
    }, True, original_chars


def create_trace_event(
    runtime: Any,
    *,
    stage: TraceStage,
    event_type: str,
    status: str,
    payload: dict[str, Any] | None = None,
    token_usage: TraceTokenUsage | TraceTokenUsagePayload | None = None,
    thinking: TraceThinking | TraceThinkingPayload | None = None,
    turn_id: str | None = None,
    assistant_message_id: str | None = None,
    task_id: str | None = None,
) -> ExecutionTraceEventPayload:
    run_id = resolve_trace_run_id(runtime)
    seq = next_trace_seq(runtime)
    safe_payload, payload_truncated, payload_original_chars = _serialize_payload(payload)

    event = ExecutionTraceEvent(
        id=f"{run_id}:{seq}",
        schema=TRACE_SCHEMA_VERSION,
        run_id=run_id,
        turn_id=turn_id,
        stage=stage,
        event_type=event_type,
        timestamp=time.time(),
        seq=seq,
        status=status,
        payload=safe_payload,
        assistant_message_id=assistant_message_id,
        task_id=task_id,
        payload_truncated=payload_truncated,
        payload_original_chars=payload_original_chars,
        token_usage=token_usage,
        thinking=thinking,
    )
    event_payload = _dump_model(event)
    event_payload.setdefault("turn_id", None)
    event_payload.setdefault("assistant_message_id", None)
    event_payload.setdefault("task_id", None)
    return event_payload


def stream_trace_event(event: ExecutionTraceEvent | dict[str, Any]) -> None:
    """Best-effort real-time stream of trace events."""
    try:
        from src.config.execution_trace_config import get_execution_trace_config

        if not get_execution_trace_config().enabled:
            return
    except Exception:
        # Config load should not break execution.
        return

    try:
        payload = _trace_event_payload(event)
        writer = get_stream_writer()
        writer(
            {
                "type": TRACE_STREAM_EVENT_TYPE,
                "schema": TRACE_SCHEMA_VERSION,
                **payload,
            }
        )
    except Exception:
        # Streaming should never break agent execution.
        return


def _dedupe_sorted_events(events: list[ExecutionTraceEventPayload]) -> list[ExecutionTraceEventPayload]:
    by_id: dict[str, ExecutionTraceEventPayload] = {}
    without_id: list[ExecutionTraceEventPayload] = []
    for event in events:
        event_id = event.get("id")
        if isinstance(event_id, str) and event_id:
            by_id[event_id] = event
        else:
            without_id.append(event)
    deduped = list(by_id.values()) + without_id
    deduped.sort(
        key=lambda item: (
            int(item.get("seq") or 0),
            float(item.get("timestamp") or 0.0),
            str(item.get("id") or ""),
        )
    )
    return deduped


def _trim_events(events: list[ExecutionTraceEventPayload]) -> list[ExecutionTraceEventPayload]:
    if len(events) <= TRACE_MAX_EVENTS_PER_RUN:
        return events
    return events[-TRACE_MAX_EVENTS_PER_RUN:]


def merge_execution_trace(existing: ExecutionTraceStatePayload | None, new: ExecutionTraceStatePayload | None) -> ExecutionTraceStatePayload:
    """Reducer for ThreadState.execution_trace."""
    if existing is None:
        return _as_dict(new) or {"version": TRACE_SCHEMA_VERSION, "runs": {}}
    if new is None:
        return _as_dict(existing)

    merged: ExecutionTraceStatePayload = {
        "version": TRACE_SCHEMA_VERSION,
        "runs": {},
    }
    existing_payload = _as_dict(existing)
    new_payload = _as_dict(new)
    existing_runs = _as_dict(existing_payload.get("runs"))
    new_runs = _as_dict(new_payload.get("runs"))
    run_ids = set(existing_runs.keys()) | set(new_runs.keys())
    for run_id in run_ids:
        old_run = _as_dict(existing_runs.get(run_id))
        new_run = _as_dict(new_runs.get(run_id))
        old_events = old_run.get("events")
        new_events = new_run.get("events")
        combined_events: list[ExecutionTraceEventPayload] = []
        if isinstance(old_events, list):
            combined_events.extend([event for event in old_events if isinstance(event, dict)])
        if isinstance(new_events, list):
            combined_events.extend([event for event in new_events if isinstance(event, dict)])
        merged_run: ExecutionTraceRunPayload = {
            "run_id": run_id,
            "started_at": float(new_run.get("started_at") or old_run.get("started_at") or time.time()),
            "updated_at": float(new_run.get("updated_at") or old_run.get("updated_at") or time.time()),
            "events": _trim_events(_dedupe_sorted_events(combined_events)),
        }
        merged["runs"][run_id] = merged_run

    if len(merged["runs"]) > TRACE_MAX_RUNS_RETAINED:
        ranked = sorted(
            merged["runs"].values(),
            key=lambda run: float(run.get("updated_at") or run.get("started_at") or 0.0),
            reverse=True,
        )
        keep_ids = {str(run.get("run_id") or "") for run in ranked[:TRACE_MAX_RUNS_RETAINED]}
        merged["runs"] = {run_id: run for run_id, run in merged["runs"].items() if run_id in keep_ids}
    return merged


def execution_trace_update(events: list[ExecutionTraceEventPayload]) -> ExecutionTraceStatePayload:
    """Build a minimal state update payload from events."""
    update: ExecutionTraceStatePayload = {
        "version": TRACE_SCHEMA_VERSION,
        "runs": {},
    }
    now = time.time()
    for event in events:
        run_id = str(event.get("run_id") or "run-unknown")
        run = update["runs"].setdefault(
            run_id,
            {
                "run_id": run_id,
                "started_at": float(event.get("timestamp") or now),
                "updated_at": float(event.get("timestamp") or now),
                "events": [],
            },
        )
        run["events"].append(event)
        run["updated_at"] = max(float(run.get("updated_at") or 0.0), float(event.get("timestamp") or now))
    return update


def extract_reasoning_from_message(message: Any) -> str | None:
    if message is None:
        return None
    additional_kwargs = getattr(message, "additional_kwargs", None) or {}
    reasoning = additional_kwargs.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning.strip()

    content = getattr(message, "content", None)
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                thinking = block.get("thinking")
                if isinstance(thinking, str) and thinking.strip():
                    return thinking.strip()
    return None


def extract_token_usage_from_message(message: Any) -> TraceTokenUsagePayload | None:
    response_metadata = getattr(message, "response_metadata", None) or {}
    usage = (
        response_metadata.get("token_usage")
        or response_metadata.get("usage_metadata")
        or response_metadata.get("usage")
        or getattr(message, "usage_metadata", None)
        or {}
    )
    if not isinstance(usage, dict):
        return None

    usage_dict: TraceTokenUsagePayload = {}
    input_tokens = usage.get("input_tokens") or usage.get("prompt_tokens")
    output_tokens = usage.get("output_tokens") or usage.get("completion_tokens")
    total_tokens = usage.get("total_tokens")
    if isinstance(input_tokens, int):
        usage_dict["input_tokens"] = input_tokens
    if isinstance(output_tokens, int):
        usage_dict["output_tokens"] = output_tokens
    if isinstance(total_tokens, int):
        usage_dict["total_tokens"] = total_tokens
    if not usage_dict:
        return None
    if "total_tokens" not in usage_dict and "input_tokens" in usage_dict and "output_tokens" in usage_dict:
        usage_dict["total_tokens"] = usage_dict["input_tokens"] + usage_dict["output_tokens"]
    return _dump_model(TraceTokenUsage.model_validate(usage_dict))


def make_summary_fallback(*, event_type: str, payload: dict[str, Any] | None = None) -> str:
    """Deterministic fallback summary when raw reasoning is unavailable."""
    details = []
    payload = payload or {}
    if "decision" in payload:
        details.append(f"decision={payload['decision']}")
    if "tool" in payload:
        details.append(f"tool={payload['tool']}")
    if "attempt" in payload:
        details.append(f"attempt={payload['attempt']}")
    if "todo_count" in payload:
        details.append(f"todos={payload['todo_count']}")
    if "verdict" in payload:
        details.append(f"verdict={payload['verdict']}")
    suffix = f" ({', '.join(details)})" if details else ""
    return f"No raw reasoning was exposed by the provider. Generated summary for `{event_type}`{suffix}."
