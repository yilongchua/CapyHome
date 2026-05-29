"""Sandbox metadata for cross-process discovery and state persistence."""

from __future__ import annotations

import time

from pydantic import AliasChoices, ConfigDict, Field

from src.schema import CapyBaseModel


class SandboxInfo(CapyBaseModel):
    """Persisted sandbox metadata that enables cross-process discovery.

    This model holds all the information needed to reconnect to an
    existing sandbox from a different process (e.g., gateway vs langgraph,
    multiple workers, or across K8s pods with shared storage).
    """

    sandbox_id: str = Field(..., description="Deterministic sandbox identifier")
    sandbox_url: str = Field(..., validation_alias=AliasChoices("sandbox_url", "base_url"), description="Reachable sandbox base URL")
    container_name: str | None = Field(default=None, description="Local container name, when using a local backend")
    container_id: str | None = Field(default=None, description="Local container id, when using a local backend")
    created_at: float = Field(default_factory=time.time, description="Unix timestamp when this sandbox metadata was created")

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    def to_dict(self) -> dict:
        return {
            "sandbox_id": self.sandbox_id,
            "sandbox_url": self.sandbox_url,
            "container_name": self.container_name,
            "container_id": self.container_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SandboxInfo:
        normalized = dict(data)
        normalized.setdefault("sandbox_url", normalized.get("base_url", ""))
        normalized.setdefault("created_at", time.time())
        return cls.model_validate(normalized)
