"""Configuration for handoff artifact writing."""

from pydantic import BaseModel, Field


class HandoffsConfig(BaseModel):
    """Runtime artifact configuration."""

    enabled: bool = Field(
        default=True,
        description="Enable writing runtime artifacts for planner/evaluator/scratchpad stages.",
    )
    dir: str = Field(
        default=".runtime",
        description="Relative directory under thread workspace for runtime artifacts.",
    )
    work_handoff_retry_attempts: int = Field(
        default=3,
        ge=0,
        description="Extra automatic retries for work-mode handoff after the first failed attempt.",
    )
    work_handoff_recursion_limit: int = Field(
        default=1000,
        ge=50,
        description="Recursion limit used by spawned work-mode handoff runs.",
    )


_handoffs_config: HandoffsConfig = HandoffsConfig()


def get_handoffs_config() -> HandoffsConfig:
    """Get current handoffs configuration."""
    return _handoffs_config


def set_handoffs_config(config: HandoffsConfig) -> None:
    """Set handoffs configuration."""
    global _handoffs_config
    _handoffs_config = config


def load_handoffs_config_from_dict(config_dict: dict) -> None:
    """Load handoffs configuration from dictionary."""
    global _handoffs_config
    _handoffs_config = HandoffsConfig(**config_dict)
