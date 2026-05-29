from datetime import UTC, datetime

import pytest
from pydantic import Field, ValidationError

from src.schema.base import (
    CapyBaseModel,
    CapyConfigNode,
    CapyEntity,
    CapyEvent,
    CapyRequest,
    CapyResponse,
    EventSeq,
    ImageMimeType,
    NonEmptyStr,
    SafePathStr,
    Sha256Str,
    model_dump_jsonable,
    new_id,
)


def test_capy_base_model_allows_extra_fields_during_migration() -> None:
    class Example(CapyBaseModel):
        name: str

    model = Example(name="ok", experimental=True)

    assert model.model_extra == {"experimental": True}


def test_capy_event_is_frozen_and_validates_seq_alias() -> None:
    class ExampleEvent(CapyEvent):
        seq: EventSeq
        line: str

    event = ExampleEvent(seq=1, line="hello")

    with pytest.raises(ValidationError):
        ExampleEvent(seq=0, line="hello")
    with pytest.raises(ValidationError):
        event.line = "changed"


def test_request_strips_whitespace_and_response_forbids_extra() -> None:
    class ExampleRequest(CapyRequest):
        prompt: str

    class ExampleResponse(CapyResponse):
        answer: str

    assert ExampleRequest(prompt="  hello  ").prompt == "hello"
    with pytest.raises(ValidationError):
        ExampleResponse(answer="ok", extra="nope")


def test_config_node_allows_experimental_keys() -> None:
    class ExampleConfig(CapyConfigNode):
        enabled: bool = False

    config = ExampleConfig(enabled=True, experimental="kept")

    assert config.model_extra == {"experimental": "kept"}


def test_entity_defaults_are_json_serializable_and_utc_aware() -> None:
    entity = CapyEntity()
    dumped = model_dump_jsonable(entity)

    assert entity.id.startswith("capy_")
    assert entity.created_at.tzinfo == UTC
    assert entity.updated_at.tzinfo == UTC
    assert isinstance(dumped["created_at"], str)
    assert isinstance(datetime.fromisoformat(dumped["created_at"]), datetime)


def test_entity_id_can_be_overridden_by_domain_models() -> None:
    class RunEntity(CapyEntity):
        id: str = Field(default_factory=lambda: new_id("run"))

    assert RunEntity().id.startswith("run_")


def test_shared_string_validators() -> None:
    class Example(CapyBaseModel):
        name: NonEmptyStr
        path: SafePathStr
        digest: Sha256Str
        mime_type: ImageMimeType

    model = Example(
        name="  Capy  ",
        path="/mnt/user-data/workspace/report.md",
        digest="A" * 64,
        mime_type="IMAGE/PNG",
    )

    assert model.name == "Capy"
    assert model.digest == "a" * 64
    assert model.mime_type == "image/png"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "   "),
        ("path", "/mnt/user-data/../secrets.txt"),
        ("digest", "not-a-digest"),
        ("mime_type", "image/svg+xml"),
    ],
)
def test_shared_string_validators_reject_invalid_values(field: str, value: str) -> None:
    class Example(CapyBaseModel):
        name: NonEmptyStr = "Capy"
        path: SafePathStr = "/mnt/user-data/workspace/report.md"
        digest: Sha256Str = "a" * 64
        mime_type: ImageMimeType = "image/png"

    payload = {field: value}

    with pytest.raises(ValidationError):
        Example(**payload)
