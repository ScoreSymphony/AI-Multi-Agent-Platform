from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.models import (
    JsonModelRoutingProfileRepository,
    ModelRoutingProfileDefinition,
    ModelRoutingProfilePolicy,
    ModelRoutingProfileRevision,
    new_model_routing_profile_id,
)
from ai_multi_agent_platform.portability import (
    ImportContext,
    ModelRoutingProfilePortableCodec,
    snapshot_model_routing_profile,
)
from ai_multi_agent_platform.portability.model_routing_profile_codecs import (
    MODEL_ROUTING_PROFILE_RESOURCE_TYPE,
)
from ai_multi_agent_platform.portability.models import PortableResource

OWNER = OwnerRef(type="user", id="user-routing-schema")
FUTURE_SCHEMA_VERSION = "99.0"


def _seed_repository(path) -> tuple[JsonModelRoutingProfileRepository, str]:
    repository = JsonModelRoutingProfileRepository(path)
    profile_id = new_model_routing_profile_id()
    created_at = datetime.now(UTC)
    definition = ModelRoutingProfileDefinition(
        profile_id=profile_id,
        owner_ref=OWNER,
        current_revision=1,
        created_at=created_at,
        updated_at=created_at,
    )
    revision = ModelRoutingProfileRevision(
        profile_id=profile_id,
        revision=1,
        name="Schema guarded",
        owner_ref=OWNER,
        policy=ModelRoutingProfilePolicy(),
        created_at=created_at,
    )
    repository.create_profile(definition, revision)
    return repository, profile_id


def _portable_resource(repository, profile_id: str) -> PortableResource:
    codec = ModelRoutingProfilePortableCodec()
    exported = codec.serialize(snapshot_model_routing_profile(repository, profile_id))
    return PortableResource(
        resource_type=MODEL_ROUTING_PROFILE_RESOURCE_TYPE,
        resource_id=exported.resource_id,
        resource_version=exported.resource_version,
        payload=exported.payload,
        id_policy=exported.id_policy,
        dependencies=exported.dependencies,
    )


def test_repository_rejects_unsupported_definition_schema_version_on_restart(tmp_path) -> None:
    path = tmp_path / "profiles.json"
    _, _ = _seed_repository(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["profiles"][0]["definition"]["schema_version"] = FUTURE_SCHEMA_VERSION
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ContractError) as caught:
        JsonModelRoutingProfileRepository(path)

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
    assert "unsupported routing profile schema_version" in str(caught.value)


def test_repository_rejects_unsupported_revision_schema_version_on_restart(tmp_path) -> None:
    path = tmp_path / "profiles.json"
    _, _ = _seed_repository(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["profiles"][0]["revisions"][0]["schema_version"] = FUTURE_SCHEMA_VERSION
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ContractError) as caught:
        JsonModelRoutingProfileRepository(path)

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
    assert "unsupported routing profile schema_version" in str(caught.value)


def test_portable_codec_rejects_unsupported_definition_schema_version(tmp_path) -> None:
    repository, profile_id = _seed_repository(tmp_path / "source.json")
    codec = ModelRoutingProfilePortableCodec()
    resource = _portable_resource(repository, profile_id)
    payload = json.loads(json.dumps(resource.payload))
    payload["definition"]["schema_version"] = FUTURE_SCHEMA_VERSION

    with pytest.raises(ContractError) as caught:
        codec.deserialize(replace(resource, payload=payload), ImportContext())

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION


def test_portable_codec_rejects_unsupported_revision_schema_version(tmp_path) -> None:
    repository, profile_id = _seed_repository(tmp_path / "source.json")
    codec = ModelRoutingProfilePortableCodec()
    resource = _portable_resource(repository, profile_id)
    payload = json.loads(json.dumps(resource.payload))
    payload["revisions"][0]["schema_version"] = FUTURE_SCHEMA_VERSION

    with pytest.raises(ContractError) as caught:
        codec.deserialize(replace(resource, payload=payload), ImportContext())

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
