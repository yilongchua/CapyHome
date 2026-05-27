"""Canonical ``plan.md`` serializer and parser for plan_agent → work_agent handoff.

The on-disk ``plan.md`` becomes the source of truth across the mode boundary.
The plan_agent writes it, and the work_agent parses it back into
``ThreadState.plan`` and ``ThreadState.todo_graph`` at handoff. This lets users
edit ``plan.md`` directly between approval and execution — their edits are
honored instead of being silently overwritten by the checkpointed state.

Format: YAML frontmatter (canonical, machine-readable) + Markdown body
(human-readable, regenerated from frontmatter on write).

The frontmatter carries every field needed to reconstruct ``PlanState`` and
``TodoGraphState``. The body is informational and is not parsed back.

``plan_version: 5`` marks the canonical-handoff format. Older versions (1–4)
do not carry structured todos in the frontmatter; ``parse_plan_md`` returns
``None`` for those so callers fall back to checkpointed state.
"""

from __future__ import annotations

import logging
from typing import Any

import yaml

CANONICAL_PLAN_VERSION = 5

logger = logging.getLogger(__name__)


def serialize_plan_md(
    plan: dict[str, Any],
    todo_graph: dict[str, Any] | None,
    *,
    body_renderer=None,
) -> str:
    """Serialize a plan + todo graph into canonical ``plan.md`` text.

    The frontmatter holds the canonical structured data (plan_version=5).
    The body is regenerated for human consumption via ``body_renderer`` if
    provided; otherwise a minimal markdown summary is rendered.

    ``body_renderer`` is called as ``body_renderer(plan, nodes)`` and must
    return a markdown string (without the frontmatter).
    """
    nodes = list((todo_graph or {}).get("nodes") or [])
    ready_ids = list((todo_graph or {}).get("ready_ids") or [])

    frontmatter: dict[str, Any] = {
        "plan_version": CANONICAL_PLAN_VERSION,
        "plan_id": plan.get("plan_id") or "",
        "title": plan.get("title") or "Execution Plan",
        "status": plan.get("status") or "draft",
        "domain": plan.get("domain") or "generic",
        "target_mode": plan.get("target_mode") or "work",
        "created_at": plan.get("created_at") or "",
        "last_synced_at": plan.get("last_synced_at") or "",
        "objective": plan.get("objective") or "",
        "summary": plan.get("summary") or "",
        "assumptions": list(plan.get("assumptions") or []),
        "constraints": list(plan.get("constraints") or []),
        "risks": list(plan.get("risks") or []),
        "acceptance_criteria": list(plan.get("acceptance_criteria") or []),
        "todos": [_node_to_frontmatter(n) for n in nodes],
        "todo_ready_ids": ready_ids,
        "clarifications": list(plan.get("clarifications") or []),
        "clarification_answers": list(plan.get("clarification_answers") or []),
        "clarification_pending": bool(plan.get("clarification_pending", False)),
        "clarification_resolved": bool(plan.get("clarification_resolved", False)),
        "total_todos": len(nodes),
        "completed_todos": sum(1 for n in nodes if str(n.get("status") or "") == "completed"),
    }

    if body_renderer is not None:
        body = body_renderer(plan, nodes)
    else:
        body = _default_body(plan, nodes)

    fm_yaml = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f"---\n{fm_yaml}---\n\n{body}"


def parse_plan_md(text: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Parse canonical plan.md text into ``(plan, todo_graph)``.

    Returns ``None`` if the text is not a canonical plan (no frontmatter, or
    ``plan_version`` < ``CANONICAL_PLAN_VERSION``). Callers should fall back
    to checkpointed ``ThreadState`` in that case.

    Raises ``ValueError`` if the frontmatter is malformed.
    """
    fm = _extract_frontmatter(text)
    if fm is None:
        return None
    version = fm.get("plan_version")
    if not isinstance(version, int) or version < CANONICAL_PLAN_VERSION:
        return None

    plan: dict[str, Any] = {
        "plan_id": str(fm.get("plan_id") or ""),
        "title": str(fm.get("title") or "Execution Plan"),
        "status": str(fm.get("status") or "draft"),
        "domain": str(fm.get("domain") or "generic"),
        "target_mode": str(fm.get("target_mode") or "work"),
        "created_at": str(fm.get("created_at") or ""),
        "last_synced_at": str(fm.get("last_synced_at") or ""),
        "objective": str(fm.get("objective") or ""),
        "summary": str(fm.get("summary") or ""),
        "assumptions": list(fm.get("assumptions") or []),
        "constraints": list(fm.get("constraints") or []),
        "risks": list(fm.get("risks") or []),
        "acceptance_criteria": list(fm.get("acceptance_criteria") or []),
        "clarifications": list(fm.get("clarifications") or []),
        "clarification_answers": list(fm.get("clarification_answers") or []),
        "clarification_pending": bool(fm.get("clarification_pending", False)),
        "clarification_resolved": bool(fm.get("clarification_resolved", False)),
    }

    raw_todos = fm.get("todos") or []
    nodes: list[dict[str, Any]] = []
    for raw in raw_todos:
        if not isinstance(raw, dict):
            continue
        nodes.append(_frontmatter_to_node(raw))

    plan["todo_ids"] = [n["id"] for n in nodes if n.get("id")]

    todo_graph: dict[str, Any] = {
        "nodes": nodes,
        "ready_ids": list(fm.get("todo_ready_ids") or []),
    }

    return plan, todo_graph


def _node_to_frontmatter(node: dict[str, Any]) -> dict[str, Any]:
    """Project a runtime todo node into its frontmatter form."""
    out: dict[str, Any] = {
        "id": str(node.get("id") or "").strip(),
        "content": str(node.get("content") or "").strip(),
        "status": str(node.get("status") or "pending"),
        "depends_on": [str(d).strip() for d in (node.get("depends_on") or []) if str(d).strip()],
    }
    # Optional rich fields — only emit when present so frontmatter stays clean.
    for key in ("rationale", "objective", "completion_requirement", "failure_fallback", "owner", "subagent_type", "target_endpoint"):
        value = node.get(key)
        if value:
            out[key] = value
    if node.get("tool_budget") is not None:
        out["tool_budget"] = node["tool_budget"]
    if isinstance(node.get("steps"), list) and node["steps"]:
        out["steps"] = node["steps"]
    if isinstance(node.get("artifacts"), list) and node["artifacts"]:
        out["artifacts"] = node["artifacts"]
    return out


def _frontmatter_to_node(raw: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct a runtime todo node from its frontmatter form."""
    node: dict[str, Any] = {
        "id": str(raw.get("id") or "").strip(),
        "content": str(raw.get("content") or "").strip(),
        "status": str(raw.get("status") or "pending"),
        "depends_on": [str(d).strip() for d in (raw.get("depends_on") or []) if str(d).strip()],
    }
    for key in ("rationale", "objective", "completion_requirement", "failure_fallback", "owner", "subagent_type", "target_endpoint"):
        if raw.get(key):
            node[key] = raw[key]
    if raw.get("tool_budget") is not None:
        node["tool_budget"] = raw["tool_budget"]
    if isinstance(raw.get("steps"), list) and raw["steps"]:
        node["steps"] = raw["steps"]
    if isinstance(raw.get("artifacts"), list) and raw["artifacts"]:
        node["artifacts"] = raw["artifacts"]
    return node


def _extract_frontmatter(text: str) -> dict[str, Any] | None:
    """Extract and parse the YAML frontmatter block.

    Returns ``None`` if the text doesn't start with ``---``. Raises
    ``ValueError`` if the frontmatter is malformed YAML.
    """
    if not text.startswith("---"):
        return None
    # Find the closing fence.
    rest = text[3:]
    end_idx = rest.find("\n---")
    if end_idx == -1:
        return None
    fm_text = rest[:end_idx].lstrip("\n")
    try:
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Malformed plan.md frontmatter: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"plan.md frontmatter must be a mapping, got {type(data).__name__}")
    return data


def _default_body(plan: dict[str, Any], nodes: list[dict[str, Any]]) -> str:
    """Minimal markdown body used when no ``body_renderer`` is provided.

    Real callers (e.g. PlanFileSyncMiddleware) should pass in the existing
    ``render_plan_md`` body via ``body_renderer`` for the rich human view.
    """
    title = plan.get("title") or "Execution Plan"
    status = str(plan.get("status") or "draft").strip().lower() or "draft"
    lines = [f"# {title}", "", f"**Plan status:** `{status}`", ""]
    objective = (plan.get("objective") or plan.get("summary") or "").strip()
    if objective:
        lines.extend(["## Objective", objective, ""])
    if nodes:
        lines.append("## Todos")
        for node in nodes:
            status = node.get("status") or "pending"
            todo_id = node.get("id") or "?"
            content = node.get("content") or ""
            lines.append(f"- [{todo_id}] ({status}) {content}")
        lines.append("")
    return "\n".join(lines)
