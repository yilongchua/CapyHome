from __future__ import annotations

from src.agents.activity_timeline import (
    ActivityTimelineState,
    ContextMetricsState,
    activity_timeline_update,
    create_activity_event,
    merge_activity_timeline,
    merge_context_metrics,
)


def test_merge_activity_timeline_dedupes_and_orders() -> None:
    existing = activity_timeline_update(
        [
            {
                "id": "run-1:2",
                "run_id": "run-1",
                "seq": 2,
                "timestamp": 20.0,
                "actor": "capyhome",
                "kind": "b",
                "line": "CapyHome is working on ...",
            },
            {
                "id": "run-1:1",
                "run_id": "run-1",
                "seq": 1,
                "timestamp": 10.0,
                "actor": "capyhome",
                "kind": "a",
                "line": "CapyHome is thinking...",
            },
        ]
    )
    new = activity_timeline_update(
        [
            {
                "id": "run-1:2",
                "run_id": "run-1",
                "seq": 2,
                "timestamp": 20.0,
                "actor": "capyhome",
                "kind": "b",
                "line": "CapyHome is working on ...",
            },
            {
                "id": "run-1:3",
                "run_id": "run-1",
                "seq": 3,
                "timestamp": 30.0,
                "actor": "baby_capy",
                "kind": "task_running",
                "line": "Baby Capy is working on ...",
            },
        ]
    )

    merged = merge_activity_timeline(existing, new)
    ids = [event.get("id") for event in merged["events"]]
    assert ids == ["run-1:1", "run-1:2", "run-1:3"]


def test_merge_context_metrics_keeps_latest_context_and_compaction() -> None:
    existing = {
        "token_count": 120,
        "message_count": 4,
        "context_updated_at": 100.0,
        "compaction_count": 1,
        "last_compaction_at": 90.0,
    }
    new = {
        "token_count": 140,
        "message_count": 5,
        "context_updated_at": 120.0,
        "compaction_count": 2,
        "last_compaction_at": 121.0,
        "messages_compressed": 6,
        "messages_kept": 8,
    }

    merged = merge_context_metrics(existing, new)
    assert merged["token_count"] == 140
    assert merged["message_count"] == 5
    assert merged["context_updated_at"] == 120.0
    assert merged["compaction_count"] == 2
    assert merged["last_compaction_at"] == 121.0
    assert merged["messages_compressed"] == 6
    assert merged["messages_kept"] == 8


def test_create_activity_event_preserves_null_wire_keys() -> None:
    event = create_activity_event(
        None,
        actor="capyhome",
        kind="thinking",
        line="CapyHome is thinking...",
    )

    assert event["task_id"] is None
    assert event["group_id"] is None
    assert event["group_kind"] is None
    assert event["group_title"] is None
    assert event["group_role"] is None
    assert event["subagent_type"] is None
    assert event["description"] is None
    assert event["tool_summary"] is None
    assert event["assistant_message_id"] is None
    assert event["payload"] == {}


def test_activity_reducers_accept_model_instances() -> None:
    existing = ActivityTimelineState(
        events=[
            {
                "id": "run-1:1",
                "run_id": "run-1",
                "seq": 1,
                "timestamp": 10.0,
                "actor": "capyhome",
                "kind": "thinking",
                "line": "CapyHome is thinking...",
            }
        ]
    )
    new = activity_timeline_update(
        [
            {
                "id": "run-1:2",
                "run_id": "run-1",
                "seq": 2,
                "timestamp": 20.0,
                "actor": "capyhome",
                "kind": "model_response",
                "line": "CapyHome is working on ...",
            }
        ]
    )

    merged = merge_activity_timeline(existing, new)
    assert [event.get("id") for event in merged["events"]] == ["run-1:1", "run-1:2"]

    metrics = merge_context_metrics(
        ContextMetricsState(token_count=10, message_count=2, context_updated_at=1.0),
        {"token_count": 20, "message_count": 3, "context_updated_at": 2.0},
    )
    assert metrics["token_count"] == 20
