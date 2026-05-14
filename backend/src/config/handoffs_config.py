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
