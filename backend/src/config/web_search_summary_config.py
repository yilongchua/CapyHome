"""Configuration for web-search result summarization middleware."""

from pydantic import BaseModel, Field


class WebSearchSummaryConfig(BaseModel):
    """Controls inline summarization of oversized web-search tool outputs."""

    enabled: bool = Field(
        default=True,
        description="Whether to summarize oversized web-search tool results before they enter context.",
    )
    summary_threshold_chars: int = Field(
        default=12000,
        ge=500,
        le=200000,
        description=(
            "Only summarize tool outputs larger than this many characters. Set to match "
            "routing.timeouts.tool_result_caps['web_search'] (12000) so any result that "
            "fits the cap reaches the agent in full, and only larger results are summarized. "
            "Note: results below this threshold are NOT summarized, so "
            "routing.timeouts.unsummarized_web_search_chars must be >= this value or the "
            "truncation middleware's adaptive cap will chop the full content."
        ),
    )
    timeout_seconds: float = Field(
        default=180.0,
        gt=0.0,
        le=3600.0,
        description="Hard timeout (seconds) for the summarizer LLM call.",
    )


_web_search_summary_config: WebSearchSummaryConfig = WebSearchSummaryConfig()


def get_web_search_summary_config() -> WebSearchSummaryConfig:
    """Get current web-search summarization configuration."""
    return _web_search_summary_config


def set_web_search_summary_config(config: WebSearchSummaryConfig) -> None:
    """Set web-search summarization configuration."""
    global _web_search_summary_config
    _web_search_summary_config = config


def load_web_search_summary_config_from_dict(config_dict: dict) -> None:
    """Load web-search summarization configuration from dictionary."""
    global _web_search_summary_config
    _web_search_summary_config = WebSearchSummaryConfig(**config_dict)
