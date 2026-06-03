"""Subagent configuration definitions."""

from pydantic import ConfigDict, Field

from src.schema import CapyBaseModel


class SubagentConfig(CapyBaseModel):
    """Configuration for a subagent.

    Attributes:
        name: Unique identifier for the subagent.
        description: When Claude should delegate to this subagent.
        system_prompt: The system prompt that guides the subagent's behavior.
        tools: Optional list of tool names to allow. If None, inherits all tools.
        disallowed_tools: Optional list of tool names to deny.
        model: Model to use - 'inherit' uses parent's model.
        max_turns: Maximum number of agent turns before stopping.
        timeout_seconds: Maximum execution time in seconds (default: 900 = 15 minutes).
        modes: Runtime modes in which this subagent may be spawned via `task`.
            Defaults to ["work", "auto"] so subagents are execution-only; planning
            subagents must opt into "plan" explicitly. Enforced by task_tool.
    """

    name: str = Field(..., description="Unique identifier for the subagent")
    description: str = Field(..., description="When the lead agent should delegate to this subagent")
    system_prompt: str = Field(..., description="System prompt that guides the subagent")
    tools: list[str] | None = Field(default=None, description="Optional allowlist of tool names. None inherits all tools")
    disallowed_tools: list[str] | None = Field(default_factory=lambda: ["task"], description="Optional denylist of tool names")
    model: str = Field(default="inherit", description="Model to use; 'inherit' uses the parent model")
    max_turns: int = Field(default=50, ge=1, description="Maximum number of agent turns before stopping (config.yaml `subagents.agents.<name>.max_turns` overrides this)")
    timeout_seconds: int = Field(default=3600, ge=1, description="Maximum execution time in seconds")
    modes: list[str] = Field(
        default_factory=lambda: ["work", "auto"],
        description="Runtime modes in which `task` may spawn this subagent. Planning subagents must include 'plan'.",
    )

    model_config = ConfigDict(extra="allow", populate_by_name=True)
