from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef, Provenance, new_id
from ai_multi_agent_platform.models import (
    JsonModelRoutingProfileRepository,
    ModelRoutingProfileDefinition,
    ModelRoutingProfilePolicy,
    ModelRoutingProfileRevision,
    RoutingProfileFallbackPolicy,
    RoutingRequirements,
    new_model_routing_profile_id,
)
from ai_multi_agent_platform.portability import (
    MODEL_ROUTING_PROFILE_RESOURCE_TYPE,
    ImportContext,
    ModelRoutingProfileImportMutationHandler,
    ModelRoutingProfilePortableCodec,
    ResourceSerializerRegistry,
    register_model_routing_profile_portability_codec,
    snapshot_model_routing_profile,
)

OWNER = OwnerRef(type="user", id="user-portability-owner")


def _seed_profile(
    repository: JsonModelRoutingProfileRepository,
) -> tuple[str, str, str, str]:
    profile_id = new_model_routing_profile_id()
    project_id = new_id("project")
    preferred_model_id = new_id("model_config")
    fallback_model_id = new_id("model_config")
    created_at = datetime.now(UTC)
    revision_one = ModelRoutingProfileRevision(
        profile_id=profile_id,
        revision=1,
        name="Portable routing",
        owner_ref=OWNER,
        project_id=project_id,
        policy=ModelRoutingProfilePolicy(
            requirements=RoutingRequirements(
                min_context_window=16_000,
                tool_calling=True,
                local_only=True,
            ),
            preferred_model_ids=(preferred_model_id,),
            fallback=RoutingProfileFallbackPolicy.ROUTE,
        ),
        provenance=Provenance(source="test", actor_ref=OWNER.id),
        created_at=created_at,
    )
    repository.create_profile(
        ModelRoutingProfileDefinition(
            profile_id=profile_id,
            owner_ref=OWNER,
            project_id=project_id,
            current_revision=1,
            created_at=created_at,
            updated_at=created_at,
        ),
        revision_one,
    )
    revision_two = ModelRoutingProfileRevision(
        profile_id=profile_id,
        revision=2,
        name="Portable routing",
        description="Exact second revision",
        owner_ref=OWNER,
        project_id=project_id,
        policy=ModelRoutingProfilePolicy(
            requirements=RoutingRequirements(
                explicit_model_id=fallback_model_id,
                min_context_window=32_000,
                structured_output=True,
            ),
            preferred_model_ids=(preferred_model_id,),
            fallback=RoutingProfileFallbackPolicy.FAIL,
        ),
        provenance=Provenance(source="test", actor_ref=OWNER.id),
        created_at=created_at,
    )
    repository.update_profile(
        ModelRoutingProfileDefinition(
            profile_id=profile_id,
            owner_ref=OWNER,
            project_id=project_id,
            current_revision=2,
            created_at=created_at,
            updated_at=created_at,
        ),
        revision_two,
    )
    return profile_id, project_id, preferred_model_id, fallback_model_id


def test_routing_profile_portability_round_trip_excludes_runtime_private_state(tmp_path) -> None:
    repository = JsonModelRoutingProfileRepository(tmp_path / "routing-profiles.json")
    profile_id, project_id, preferred_model_id, fallback_model_id = _seed_profile(repository)
    serializers = ResourceSerializerRegistry()
    register_model_routing_profile_portability_codec(serializers)

    resource = serializers.serialize(
        MODEL_ROUTING_PROFILE_RESOURCE_TYPE,
        snapshot_model_routing_profile(repository, profile_id),
    )

    assert resource.resource_id == profile_id
    assert resource.resource_version == "2"
    dependency_ids = {item.identifier for item in resource.dependencies}
    assert dependency_ids == {
        f"project:{project_id}",
        preferred_model_id,
        fallback_model_id,
    }
    payload = json.dumps(resource.payload, sort_keys=True)
    for forbidden in (
        "provider_id",
        "provider_native_model_id",
        "endpoint",
        "node_ref",
        "health",
        "gateway_state",
        "credential",
    ):
        assert forbidden not in payload

    restored = serializers.deserialize(resource)
    snapshot = snapshot_model_routing_profile(repository, profile_id)
    assert restored == snapshot


def test_routing_profile_portability_remaps_canonical_references(tmp_path) -> None:
    repository = JsonModelRoutingProfileRepository(tmp_path / "source.json")
    profile_id, project_id, preferred_model_id, fallback_model_id = _seed_profile(repository)
    registry = ResourceSerializerRegistry()
    registry.register(ModelRoutingProfilePortableCodec())
    resource = registry.serialize(
        MODEL_ROUTING_PROFILE_RESOURCE_TYPE,
        snapshot_model_routing_profile(repository, profile_id),
    )

    target_profile_id = new_model_routing_profile_id()
    target_project_id = new_id("project")
    target_preferred_model_id = new_id("model_config")
    target_fallback_model_id = new_id("model_config")
    decoded = registry.deserialize(
        resource,
        ImportContext(
            id_mapping={
                (MODEL_ROUTING_PROFILE_RESOURCE_TYPE, profile_id): target_profile_id,
                ("project", project_id): target_project_id,
                ("model", preferred_model_id): target_preferred_model_id,
                ("model", fallback_model_id): target_fallback_model_id,
            }
        ),
    )

    assert decoded.definition.profile_id == target_profile_id
    assert decoded.definition.project_id == target_project_id
    assert decoded.revisions[0].policy.preferred_model_ids == (target_preferred_model_id,)
    assert decoded.revisions[1].policy.requirements.explicit_model_id == target_fallback_model_id


def test_routing_profile_import_restores_history_and_rolls_back(tmp_path) -> None:
    source = JsonModelRoutingProfileRepository(tmp_path / "source.json")
    profile_id, _, _, _ = _seed_profile(source)
    codec = ModelRoutingProfilePortableCodec()
    registry = ResourceSerializerRegistry()
    registry.register(codec)
    resource = registry.serialize(
        MODEL_ROUTING_PROFILE_RESOURCE_TYPE,
        snapshot_model_routing_profile(source, profile_id),
    )
    decoded = registry.deserialize(resource)

    target = JsonModelRoutingProfileRepository(tmp_path / "target.json")
    handler = ModelRoutingProfileImportMutationHandler(target, dependency_audit=lambda _: ())
    asyncio.run(handler.preflight(resource, decoded, ImportContext()))
    token = asyncio.run(handler.apply(resource, decoded, ImportContext()))

    assert token == profile_id
    assert target.get_definition(profile_id).current_revision == 2
    assert tuple(item.revision for item in target.list_revisions(profile_id)) == (1, 2)

    asyncio.run(handler.rollback(resource, decoded, token, ImportContext()))
    with pytest.raises(ContractError) as caught:
        target.get_definition(profile_id)
    assert caught.value.code is ErrorCode.NOT_FOUND


def test_routing_profile_import_compensates_partial_history(tmp_path) -> None:
    source = JsonModelRoutingProfileRepository(tmp_path / "source.json")
    profile_id, _, _, _ = _seed_profile(source)
    registry = ResourceSerializerRegistry()
    register_model_routing_profile_portability_codec(registry)
    resource = registry.serialize(
        MODEL_ROUTING_PROFILE_RESOURCE_TYPE,
        snapshot_model_routing_profile(source, profile_id),
    )
    decoded = registry.deserialize(resource)

    class FailingRepository(JsonModelRoutingProfileRepository):
        def update_profile(self, definition, revision) -> None:  # type: ignore[no-untyped-def]
            raise ContractError(ErrorCode.BACKEND_ERROR, "forced failure")

    target = FailingRepository(tmp_path / "target.json")
    handler = ModelRoutingProfileImportMutationHandler(target, dependency_audit=lambda _: ())
    with pytest.raises(ContractError) as caught:
        asyncio.run(handler.apply(resource, decoded, ImportContext()))
    assert caught.value.code is ErrorCode.BACKEND_ERROR
    with pytest.raises(ContractError) as missing:
        target.get_definition(profile_id)
    assert missing.value.code is ErrorCode.NOT_FOUND


def test_routing_profile_import_rollback_fails_closed_without_reference_audit(tmp_path) -> None:
    source = JsonModelRoutingProfileRepository(tmp_path / "source.json")
    profile_id, _, _, _ = _seed_profile(source)
    registry = ResourceSerializerRegistry()
    register_model_routing_profile_portability_codec(registry)
    resource = registry.serialize(
        MODEL_ROUTING_PROFILE_RESOURCE_TYPE,
        snapshot_model_routing_profile(source, profile_id),
    )
    decoded = registry.deserialize(resource)

    target = JsonModelRoutingProfileRepository(tmp_path / "target.json")
    handler = ModelRoutingProfileImportMutationHandler(target)
    token = asyncio.run(handler.apply(resource, decoded, ImportContext()))

    with pytest.raises(ContractError) as caught:
        asyncio.run(handler.rollback(resource, decoded, token, ImportContext()))
    assert caught.value.code is ErrorCode.CONFLICT
    assert "reference audit" in str(caught.value)
    assert target.get_definition(profile_id).current_revision == 2


def test_routing_profile_import_rollback_refuses_referenced_profile(tmp_path) -> None:
    source = JsonModelRoutingProfileRepository(tmp_path / "source.json")
    profile_id, _, _, _ = _seed_profile(source)
    registry = ResourceSerializerRegistry()
    register_model_routing_profile_portability_codec(registry)
    resource = registry.serialize(
        MODEL_ROUTING_PROFILE_RESOURCE_TYPE,
        snapshot_model_routing_profile(source, profile_id),
    )
    decoded = registry.deserialize(resource)

    target = JsonModelRoutingProfileRepository(tmp_path / "target.json")
    handler = ModelRoutingProfileImportMutationHandler(
        target,
        dependency_audit=lambda _: ("agent:agent_example@r1",),
    )
    token = asyncio.run(handler.apply(resource, decoded, ImportContext()))

    with pytest.raises(ContractError) as caught:
        asyncio.run(handler.rollback(resource, decoded, token, ImportContext()))
    assert caught.value.code is ErrorCode.CONFLICT
    assert caught.value.details["dependencies"] == ["agent:agent_example@r1"]
    assert target.get_definition(profile_id).current_revision == 2
