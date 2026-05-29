"""Activity timeline schema + helpers.

This module powers user-facing activity updates such as:
- CapyHome is thinking...
- CapyHome is working on ...
- Baby Capy is working on ...
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from langgraph.config import get_stream_writer
from pydantic import ConfigDict, Field

from src.schema import CapyBaseModel, CapyEvent

ACTIVITY_SCHEMA_VERSION = "v1"
ACTIVITY_STREAM_EVENT_TYPE = "activity_event.v1"
ACTIVITY_RUN_ID_KEY = "_activity_timeline_run_id"
ACTIVITY_SEQ_KEY = "_activity_timeline_seq"
ACTIVITY_MAX_EVENTS_RETAINED = 1200

ActivityActor = Literal["capyhome", "baby_capy", "system"]
ActivityEventPayload = dict[str, Any]
ActivityTimelineStatePayload = dict[str, Any]
ContextMetricsStatePayload = dict[str, Any]


class ActivityEvent(CapyEvent):
    id: str | None = Field(default=None, description="Stable event id, usually run_id:seq")
    schema_: str = Field(default=ACTIVITY_SCHEMA_VERSION, alias="schema", description="Activity event schema version")
    run_id: str = Field(..., description="Run identifier shared by events from one agent run")
    seq: int | None = Field(default=None, ge=1, description="Monotonic sequence number within the run")
    timestamp: float = Field(..., description="Unix timestamp in seconds")
    actor: ActivityActor = Field(..., description="Actor shown in the activity timeline")
    kind: str = Field(..., description="Machine-readable activity kind")
    line: str = Field(..., description="User-facing activity line")
    task_id: str | None = Field(default=None, description="Optional task/tool call id")
    group_id: str | None = Field(default=None, description="Optional progress group id")
    group_kind: str | None = Field(default=None, description="Optional progress group kind")
    group_title: str | None = Field(default=None, description="Optional progress group title")
    group_role: str | None = Field(default=None, description="Optional role within a progress group")
    subagent_type: str | None = Field(default=None, description="Optional subagent type")
    description: str | None = Field(default=None, description="Optional short task description")
    tool_summary: str | None = Field(default=None, description="Optional compact tool activity summary")
    assistant_message_id: str | None = Field(default=None, description="Optional related assistant message id")
    payload: dict[str, Any] = Field(default_factory=dict, description="Additional structured payload")

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ActivityTimelineState(CapyBaseModel):
    version: str = Field(default=ACTIVITY_SCHEMA_VERSION, description="Activity timeline schema version")
    events: list[ActivityEvent] = Field(default_factory=list, description="Persisted activity events")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ContextMetricsState(CapyBaseModel):
    token_count: int | None = Field(default=None, ge=0, description="Current context token count")
    message_count: int | None = Field(default=None, ge=0, description="Current context message count")
    context_updated_at: float | None = Field(default=None, description="Unix timestamp for the latest context metrics")
    compaction_count: int | None = Field(default=None, ge=0, description="Number of context compactions")
    last_compaction_at: float | None = Field(default=None, description="Unix timestamp for latest compaction")
    messages_compressed: int | None = Field(default=None, ge=0, description="Number of compressed messages")
    messages_kept: int | None = Field(default=None, ge=0, description="Number of retained messages")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _dump_model(model: CapyBaseModel, *, exclude_none: bool = True) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_none=exclude_none, by_alias=True)


def _activity_event_payload(value: ActivityEvent | dict[str, Any]) -> ActivityEventPayload:
    if isinstance(value, ActivityEvent):
        return _dump_model(value, exclude_none=False)
    if isinstance(value, dict):
        return _dump_model(ActivityEvent.model_validate(value), exclude_none=False)
    return {}


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, CapyBaseModel):
        return _dump_model(value)
    if isinstance(value, dict):
        return value
    return {}


def _resolve_run_id(runtime: Any) -> str:
    context = getattr(runtime, "context", None)
    if not isinstance(context, dict):
        return "run-unknown"

    native_run_id = context.get("run_id")
    if isinstance(native_run_id, str) and native_run_id:
        context[ACTIVITY_RUN_ID_KEY] = native_run_id
        return native_run_id

    existing = context.get(ACTIVITY_RUN_ID_KEY)
    if isinstance(existing, str) and existing:
        return existing

    generated = f"run-{uuid.uuid4().hex[:12]}"
    context[ACTIVITY_RUN_ID_KEY] = generated
    return generated


def _next_seq(runtime: Any) -> int:
    context = getattr(runtime, "context", None)
    if not isinstance(context, dict):
        return 1
    current = context.get(ACTIVITY_SEQ_KEY, 0)
    if not isinstance(current, int) or current < 0:
        current = 0
    current += 1
    context[ACTIVITY_SEQ_KEY] = current
    return current


def create_activity_event(
    runtime: Any,
    *,
    actor: ActivityActor,
    kind: str,
    line: str,
    task_id: str | None = None,
    group_id: str | None = None,
    group_kind: str | None = None,
    group_title: str | None = None,
    group_role: str | None = None,
    subagent_type: str | None = None,
    description: str | None = None,
    tool_summary: str | None = None,
    assistant_message_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> ActivityEventPayload:
    run_id = _resolve_run_id(runtime)
    seq = _next_seq(runtime)
    timestamp = time.time()
    event = ActivityEvent(
        id=f"{run_id}:{seq}",
        schema=ACTIVITY_SCHEMA_VERSION,
        run_id=run_id,
        seq=seq,
        timestamp=timestamp,
        actor=actor,
        kind=kind,
        line=line,
        task_id=task_id,
        group_id=group_id,
        group_kind=group_kind,
        group_title=group_title,
        group_role=group_role,
        subagent_type=subagent_type,
        description=description,
        tool_summary=tool_summary,
        assistant_message_id=assistant_message_id,
        payload=payload or {},
    )
    return _dump_model(event, exclude_none=False)


def stream_activity_event(event: ActivityEvent | dict[str, Any]) -> None:
    try:
        payload = _activity_event_payload(event)
        writer = get_stream_writer()
        writer(
            {
                "type": ACTIVITY_STREAM_EVENT_TYPE,
                "schema": ACTIVITY_SCHEMA_VERSION,
                **payload,
            }
        )
    except Exception:
        return


def _dedupe_sort_events(events: list[ActivityEventPayload]) -> list[ActivityEventPayload]:
    by_id: dict[str, ActivityEventPayload] = {}
    without_id: list[ActivityEventPayload] = []
    for event in events:
        event_id = event.get("id")
        if isinstance(event_id, str) and event_id:
            by_id[event_id] = event
        else:
            without_id.append(event)
    deduped = list(by_id.values()) + without_id
    deduped.sort(
        key=lambda item: (
            float(item.get("timestamp") or 0.0),
            int(item.get("seq") or 0),
            str(item.get("id") or ""),
        )
    )
    if len(deduped) > ACTIVITY_MAX_EVENTS_RETAINED:
        return deduped[-ACTIVITY_MAX_EVENTS_RETAINED:]
    return deduped


def merge_activity_timeline(existing: ActivityTimelineStatePayload | None, new: ActivityTimelineStatePayload | None) -> ActivityTimelineStatePayload:
    if existing is None:
        return _as_dict(new) or {"version": ACTIVITY_SCHEMA_VERSION, "events": []}
    if new is None:
        return _as_dict(existing)

    existing_payload = _as_dict(existing)
    new_payload = _as_dict(new)
    old_events = existing_payload.get("events") if isinstance(existing_payload.get("events"), list) else []
    new_events = new_payload.get("events") if isinstance(new_payload.get("events"), list) else []
    merged_events = _dedupe_sort_events(
        [event for event in [*old_events, *new_events] if isinstance(event, dict)]
    )
    return {
        "version": ACTIVITY_SCHEMA_VERSION,
        "events": merged_events,
    }


def activity_timeline_update(events: list[ActivityEventPayload]) -> ActivityTimelineStatePayload:
    state = ActivityTimelineState(events=[ActivityEvent.model_validate(event) for event in events])
    return {
        "version": state.version,
        "events": [_dump_model(event, exclude_none=False) for event in state.events],
    }


def merge_context_metrics(existing: ContextMetricsStatePayload | None, new: ContextMetricsStatePayload | None) -> ContextMetricsStatePayload:
    if existing is None:
        return _as_dict(new)
    if new is None:
        return _as_dict(existing)

    current = _as_dict(existing)
    incoming = _as_dict(new)

    current_ts = float(current.get("context_updated_at") or 0.0)
    incoming_ts = float(incoming.get("context_updated_at") or current_ts)
    if incoming_ts >= current_ts:
        if "token_count" in incoming and isinstance(incoming.get("token_count"), int):
            current["token_count"] = int(incoming["token_count"])
        if "message_count" in incoming and isinstance(incoming.get("message_count"), int):
            current["message_count"] = int(incoming["message_count"])
        current["context_updated_at"] = incoming_ts

    if isinstance(incoming.get("compaction_count"), int):
        current["compaction_count"] = max(
            int(current.get("compaction_count") or 0),
            int(incoming["compaction_count"]),
        )
    if isinstance(incoming.get("last_compaction_at"), (int, float)):
        current["last_compaction_at"] = max(
            float(current.get("last_compaction_at") or 0.0),
            float(incoming["last_compaction_at"]),
        )
    if isinstance(incoming.get("messages_compressed"), int):
        current["messages_compressed"] = int(incoming["messages_compressed"])
    if isinstance(incoming.get("messages_kept"), int):
        current["messages_kept"] = int(incoming["messages_kept"])

    return current


def context_metrics_update(payload: dict[str, Any]) -> ContextMetricsStatePayload:
    return _dump_model(ContextMetricsState.model_validate(_as_dict(payload)))
