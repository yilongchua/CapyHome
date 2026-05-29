"""Shared Pydantic foundations for CapyHome schema migrations.

These classes are intentionally opt-in. Existing event, state, config, and
gateway schemas keep their current behaviour until each surface migrates
explicitly.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Annotated, Any
from uuid import uuid4

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

_SHA256_RE = re.compile(r"^[A-Fa-f0-9]{64}$")
_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})


def utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


def new_id(prefix: str = "capy") -> str:
    """Return a short project-style identifier."""
    prefix_value = non_empty_str(prefix)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", prefix_value):
        raise ValueError("id prefix must start with a letter and contain only letters, numbers, '_' or '-'")
    return f"{prefix_value}_{uuid4().hex[:12]}"


def non_empty_str(value: str) -> str:
    """Validate that a string contains non-whitespace content."""
    if not isinstance(value, str):
        raise TypeError("value must be a string")
    stripped = value.strip()
    if not stripped:
        raise ValueError("value must not be empty")
    return stripped


def safe_path(value: str) -> str:
    """Validate a non-empty path-like string without traversal segments."""
    path = non_empty_str(value)
    if "\x00" in path:
        raise ValueError("path must not contain null bytes")
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if ".." in parts:
        raise ValueError("path must not contain '..' segments")
    return path


def sha256_str(value: str) -> str:
    """Validate a SHA-256 hex digest."""
    digest = non_empty_str(value)
    if _SHA256_RE.fullmatch(digest) is None:
        raise ValueError("value must be a 64-character SHA-256 hex digest")
    return digest.lower()


def image_mime_type(value: str) -> str:
    """Validate image MIME types supported by the current event/state schemas."""
    mime_type = non_empty_str(value).lower()
    if mime_type not in _IMAGE_MIME_TYPES:
        allowed = ", ".join(sorted(_IMAGE_MIME_TYPES))
        raise ValueError(f"image MIME type must be one of: {allowed}")
    return mime_type


NonEmptyStr = Annotated[str, AfterValidator(non_empty_str)]
SafePathStr = Annotated[str, AfterValidator(safe_path)]
Sha256Str = Annotated[str, AfterValidator(sha256_str)]
ImageMimeType = Annotated[str, AfterValidator(image_mime_type)]
EventSeq = Annotated[int, Field(ge=1)]


class CapyBaseModel(BaseModel):
    """Project-wide root model for migration-friendly internal schemas."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class CapyEvent(CapyBaseModel):
    """Immutable wire-format event base for SSE, channels, and replay payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class CapyRequest(CapyBaseModel):
    """FastAPI request body base."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)


class CapyResponse(CapyBaseModel):
    """FastAPI response body base."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CapyConfigNode(BaseModel):
    """Lenient YAML config node base for experimental config keys."""

    model_config = ConfigDict(extra="allow")


class TimestampMixin(BaseModel):
    """Reusable timestamp fields for persisted records."""

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class IdentifiedMixin(BaseModel):
    """Reusable id field for persisted records."""

    id: str = Field(default_factory=new_id)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CapyEntity(TimestampMixin, IdentifiedMixin, CapyBaseModel):
    """Persisted record base.

    Subclasses can override ``id`` with a prefixed ``default_factory`` when they
    need domain-specific identifiers such as ``run_...`` or ``approval_...``.
    """

    model_config = ConfigDict(extra="allow", frozen=False, populate_by_name=True)


def model_dump_jsonable(model: BaseModel) -> dict[str, Any]:
    """Dump a model using JSON-mode serialization for storage or wire payloads."""
    return model.model_dump(mode="json")
