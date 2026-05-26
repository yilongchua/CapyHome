"""Drift validator: every entry in internal_tools.json must match its handler.

Failures here mean the JSON description has diverged from the actual handler
signature — either rename the JSON parameter or update the handler. CI runs
this on every PR to keep the LLM-facing surface honest.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.tools.loader import build_structured_tool, load_tool_definitions, schema_drift_report

INTERNAL_TOOLS_JSON = Path(__file__).resolve().parents[1] / "src" / "tools" / "internal_tools.json"


@pytest.fixture(scope="module")
def definitions():
    defns = load_tool_definitions(INTERNAL_TOOLS_JSON)
    assert defns, "internal_tools.json is empty — Phase 2 migration not yet applied."
    return defns


def test_internal_tool_names_are_unique(definitions) -> None:
    names = [defn.name for defn in definitions]
    assert len(names) == len(set(names)), f"Duplicate tool names in internal_tools.json: {names}"


def test_all_handlers_resolve_to_basetool(definitions) -> None:
    for defn in definitions:
        # build_structured_tool raises ToolDefinitionError on any handler issue.
        tool = build_structured_tool(defn)
        assert tool.name == defn.name


def test_no_schema_drift_against_handlers(definitions) -> None:
    drift: list[str] = []
    for defn in definitions:
        tool = build_structured_tool(defn)
        drift.extend(schema_drift_report(defn, tool))
    assert not drift, "Schema drift detected:\n  - " + "\n  - ".join(drift)


def test_descriptions_are_non_trivial(definitions) -> None:
    """Doc 03 calls out terse descriptions on recall/setup_agent/write_todos. Guard against regression."""
    too_short: list[str] = []
    for defn in definitions:
        if len(defn.description.strip()) < 60:
            too_short.append(f"{defn.name}: {len(defn.description)} chars")
    assert not too_short, "Tool descriptions must be at least one sentence: " + ", ".join(too_short)


def test_every_tool_documents_return_value(definitions) -> None:
    missing = [defn.name for defn in definitions if not (defn.returns or "").strip()]
    assert not missing, f"Missing 'returns' documentation on: {missing}"


def test_every_tool_has_at_least_one_example(definitions) -> None:
    missing = [defn.name for defn in definitions if not defn.examples]
    assert not missing, f"Missing 'examples' on: {missing}"
