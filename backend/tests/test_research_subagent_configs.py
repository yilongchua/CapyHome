"""Tests for built-in research subagent configurations."""

from src.subagents.builtins import BUILTIN_SUBAGENTS
from src.subagents.registry import (
    get_subagent_config,
    get_subagent_names,
    get_subagent_names_for_mode,
    list_subagents,
)

RESEARCH_SUBAGENTS = {
    "knowledge-researcher",
    "docs-explorer",
    "comparison-dimension-researcher",
    "synthesis-reviewer",
}


def test_research_subagents_are_registered():
    names = set(get_subagent_names())

    assert RESEARCH_SUBAGENTS.issubset(names)


def test_registry_returns_research_subagent_configs():
    configs = {config.name: config for config in list_subagents()}

    for name in RESEARCH_SUBAGENTS:
        assert name in configs
        assert configs[name].description
        assert configs[name].system_prompt
        assert configs[name].model == "inherit"


def test_knowledge_researcher_has_external_research_guidance():
    config = get_subagent_config("knowledge-researcher")

    assert config is not None
    assert set(config.tools or []) == {"web_search", "query_knowledge_vault", "write_file", "str_replace"}
    assert "task" in (config.disallowed_tools or [])
    assert "recall" in (config.disallowed_tools or [])
    assert "bash" in (config.disallowed_tools or [])
    assert "Report path" in config.system_prompt
    assert "one coherent delegated topic" in config.system_prompt
    assert "If web_search fails once" in config.system_prompt


def test_docs_explorer_is_local_corpus_only():
    config = get_subagent_config("docs-explorer")

    assert config is not None
    assert set(config.tools or []) == {"ls", "read_file", "bash"}
    assert "web_search" in (config.disallowed_tools or [])
    assert "/mnt/user-data/workspace/.docs" in config.system_prompt
    assert "Do not infer facts" in config.system_prompt


def test_comparison_dimension_researcher_is_dimension_scoped():
    config = get_subagent_config("comparison-dimension-researcher")

    assert config is not None
    assert "web_search" in (config.tools or [])
    assert "recall" in (config.tools or [])
    assert "Compare only the assigned dimension" in config.system_prompt
    assert "Per-option findings" in config.system_prompt


def test_synthesis_reviewer_is_read_only_quality_gate():
    config = get_subagent_config("synthesis-reviewer")

    assert config is not None
    assert set(config.tools or []) == {"ls", "read_file"}
    assert "web_search" in (config.disallowed_tools or [])
    assert "Verdict" in config.system_prompt
    assert "Missing coverage" in config.system_prompt


def test_research_subagents_are_public_builtins():
    for name in RESEARCH_SUBAGENTS:
        assert BUILTIN_SUBAGENTS[name].name == name


# --- Plan-Mode planning helpers (finder tier) ------------------------------


def test_scope_researcher_is_plan_only_web_and_vault():
    config = get_subagent_config("scope-researcher")

    assert config is not None
    assert set(config.tools or []) == {"web_search", "query_knowledge_vault"}
    assert config.modes == ["plan"]
    assert "task" in (config.disallowed_tools or [])
    assert "Scope facet" in config.system_prompt


def test_finder_agent_is_plan_only_local_files():
    config = get_subagent_config("finder-agent")

    assert config is not None
    assert set(config.tools or []) == {"grep", "ls", "read_file"}
    assert config.modes == ["plan"]
    assert "task" in (config.disallowed_tools or [])
    # finder is local-only: must not reach the web or vault
    assert "web_search" in (config.disallowed_tools or [])
    assert "query_knowledge_vault" in (config.disallowed_tools or [])


def test_existing_subagents_default_to_work_modes():
    # knowledge-researcher is now exclusively a Work-Mode subagent.
    assert get_subagent_config("knowledge-researcher").modes == ["work"]
    # Peers keep the execution-only default.
    assert get_subagent_config("general-purpose").modes == ["work", "auto"]


def test_all_subagents_share_global_max_turns():
    # max_turns is centralized in config.yaml (subagents.max_turns); every
    # subagent resolves to the shared default unless overridden per-agent.
    from src.config.subagents_config import get_subagents_app_config

    expected = get_subagents_app_config().max_turns
    for name in get_subagent_names_for_mode("work") + get_subagent_names_for_mode("plan"):
        assert get_subagent_config(name).max_turns == expected, name


def test_mode_gating_partitions_plan_vs_work_subagents():
    plan_spawnable = set(get_subagent_names_for_mode("plan"))
    work_spawnable = set(get_subagent_names_for_mode("work"))

    assert plan_spawnable == {"scope-researcher", "finder-agent"}
    assert "knowledge-researcher" in work_spawnable
    assert plan_spawnable.isdisjoint(work_spawnable)
