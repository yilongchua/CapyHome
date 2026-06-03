"""Built-in subagent configurations."""

from .bash_agent import BASH_AGENT_CONFIG
from .comparison_dimension_researcher import COMPARISON_DIMENSION_RESEARCHER_CONFIG
from .docs_explorer import DOCS_EXPLORER_CONFIG
from .finder_agent import FINDER_AGENT_CONFIG
from .general_purpose import GENERAL_PURPOSE_CONFIG
from .knowledge_researcher import KNOWLEDGE_RESEARCHER_CONFIG
from .scope_researcher import SCOPE_RESEARCHER_CONFIG
from .synthesis_reviewer import SYNTHESIS_REVIEWER_CONFIG
from .vault_source_researcher import VAULT_SOURCE_RESEARCHER_CONFIG

__all__ = [
    "GENERAL_PURPOSE_CONFIG",
    "BASH_AGENT_CONFIG",
    "KNOWLEDGE_RESEARCHER_CONFIG",
    "DOCS_EXPLORER_CONFIG",
    "COMPARISON_DIMENSION_RESEARCHER_CONFIG",
    "SYNTHESIS_REVIEWER_CONFIG",
    "VAULT_SOURCE_RESEARCHER_CONFIG",
    "SCOPE_RESEARCHER_CONFIG",
    "FINDER_AGENT_CONFIG",
]

# Registry of built-in subagents
BUILTIN_SUBAGENTS = {
    "general-purpose": GENERAL_PURPOSE_CONFIG,
    "bash": BASH_AGENT_CONFIG,
    "knowledge-researcher": KNOWLEDGE_RESEARCHER_CONFIG,
    "docs-explorer": DOCS_EXPLORER_CONFIG,
    "comparison-dimension-researcher": COMPARISON_DIMENSION_RESEARCHER_CONFIG,
    "synthesis-reviewer": SYNTHESIS_REVIEWER_CONFIG,
    "vault-source-researcher": VAULT_SOURCE_RESEARCHER_CONFIG,
    # Plan-Mode planning helpers (modes include "plan").
    "scope-researcher": SCOPE_RESEARCHER_CONFIG,
    "finder-agent": FINDER_AGENT_CONFIG,
}
