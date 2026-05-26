"""Phase 5: external_tools.json applies mode/phase policy to MCP tools.

We don't spin up a real MCP server; instead we wrap a few @tool stubs with
server-prefixed names and assert the policy filter behaves correctly.
"""

from __future__ import annotations

from langchain.tools import tool

from src.tools.loader import filter_mcp_tools_by_policy
from src.tools.schema import ExternalPolicy, McpServerPolicy


@tool("filesystem__read", parse_docstring=True)
def fs_read(path: str) -> str:
    """Read a file via the fake filesystem server.

    Args:
        path: where to read.
    """
    return path


@tool("filesystem__write", parse_docstring=True)
def fs_write(path: str) -> str:
    """Write a file via the fake filesystem server.

    Args:
        path: where to write.
    """
    return path


@tool("github__list_issues", parse_docstring=True)
def gh_list(repo: str) -> str:
    """List issues from the fake github server.

    Args:
        repo: repo name.
    """
    return repo


@tool("unrelated__ping", parse_docstring=True)
def unrelated_ping(value: str) -> str:
    """Server with no policy entry.

    Args:
        value: anything.
    """
    return value


def _all_tools() -> list:
    return [fs_read, fs_write, gh_list, unrelated_ping]


def test_empty_policy_passes_all_tools_through() -> None:
    kept = filter_mcp_tools_by_policy(_all_tools(), ExternalPolicy(), mode="work", phase="approved")
    assert {t.name for t in kept} == {t.name for t in _all_tools()}


def test_policy_admits_tools_in_their_declared_phase() -> None:
    policy = ExternalPolicy(
        mcp_servers=[
            McpServerPolicy(name="filesystem", mode=["work"], phase=["approved"]),
        ]
    )
    kept = {t.name for t in filter_mcp_tools_by_policy(_all_tools(), policy, mode="work", phase="approved")}
    assert "filesystem__read" in kept and "filesystem__write" in kept
    # No policy entry for github/unrelated — they pass through unchanged.
    assert "github__list_issues" in kept and "unrelated__ping" in kept


def test_policy_drops_tools_outside_their_phase() -> None:
    policy = ExternalPolicy(
        mcp_servers=[
            McpServerPolicy(name="filesystem", mode=["work"], phase=["approved"]),
        ]
    )
    kept = {t.name for t in filter_mcp_tools_by_policy(_all_tools(), policy, mode="work", phase="draft")}
    assert "filesystem__read" not in kept
    assert "filesystem__write" not in kept
    # Tools without a policy entry are unaffected.
    assert "github__list_issues" in kept
    assert "unrelated__ping" in kept


def test_policy_respects_mode() -> None:
    policy = ExternalPolicy(
        mcp_servers=[
            McpServerPolicy(name="filesystem", mode=["plan"], phase=["draft", "approved"]),
        ]
    )
    plan_kept = {t.name for t in filter_mcp_tools_by_policy(_all_tools(), policy, mode="plan", phase="draft")}
    work_kept = {t.name for t in filter_mcp_tools_by_policy(_all_tools(), policy, mode="work", phase="approved")}
    assert "filesystem__read" in plan_kept
    assert "filesystem__read" not in work_kept


def test_policy_respects_subagent_visibility() -> None:
    policy = ExternalPolicy(
        mcp_servers=[
            McpServerPolicy(
                name="filesystem",
                mode=["work"],
                phase=["approved"],
                subagent_visible=False,
            ),
        ]
    )
    lead_kept = {t.name for t in filter_mcp_tools_by_policy(_all_tools(), policy, mode="work", phase="approved", subagent=False)}
    sub_kept = {t.name for t in filter_mcp_tools_by_policy(_all_tools(), policy, mode="work", phase="approved", subagent=True)}
    assert "filesystem__read" in lead_kept
    assert "filesystem__read" not in sub_kept


def test_policy_supports_custom_name_prefix() -> None:
    policy = ExternalPolicy(
        mcp_servers=[
            McpServerPolicy(name="filesystem", name_prefix="fs_custom_", mode=["work"], phase=["approved"]),
        ]
    )

    @tool("fs_custom_special", parse_docstring=True)
    def fs_custom_special(value: str) -> str:
        """Custom-prefixed filesystem tool.

        Args:
            value: anything.
        """
        return value

    tools = [fs_custom_special, gh_list]
    kept = {t.name for t in filter_mcp_tools_by_policy(tools, policy, mode="work", phase="approved")}
    assert "fs_custom_special" in kept

    blocked = {t.name for t in filter_mcp_tools_by_policy(tools, policy, mode="plan", phase="draft")}
    assert "fs_custom_special" not in blocked
