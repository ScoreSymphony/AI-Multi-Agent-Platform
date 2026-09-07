from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.models import ModelCapabilities, ModelConfiguration
from ai_multi_agent_platform.portability.registry import ImportContext, ResourceSerializerRegistry
from ai_multi_agent_platform.portability.skill_codecs import (
    SKILL_RESOURCE_TYPE,
    SkillPortableSnapshot,
    register_skill_portability_codecs,
    snapshot_skill,
)
from ai_multi_agent_platform.portability.skill_import import SkillImportMutationHandler
from ai_multi_agent_platform.skills import (
    InMemorySkillRepository,
    SkillContent,
    SkillEvaluationStatus,
    SkillProfile,
    SkillResolutionRequest,
    SkillResolver,
    SkillRevisionRef,
    SkillService,
    SkillSource,
    SkillTrustStatus,
    new_skill_id,
)


OWNER = OwnerRef(type="user", id="issue-588-security")


def _profile(name: str, content: str, **changes: object) -> SkillProfile:
    base = SkillProfile(
        name=name,
        purpose_categories=("security-test",),
        content=SkillContent(content=content),
    )
    return replace(base, **changes)


def _request(skill_ref: SkillRevisionRef, *, model: ModelConfiguration | None = None) -> SkillResolutionRequest:
    return SkillResolutionRequest(
        run_id=new_id("run"),
        task_id=new_id("task"),
        agent_id=new_id("agent"),
        agent_revision=1,
        agent_role="worker",
        explicit_skills=(skill_ref,),
        model_configuration=model,
    )


def test_generic_update_cannot_bypass_third_party_review_lifecycle() -> None:
    repository = InMemorySkillRepository()
    service = SkillService(repository)
    source = SkillSource(
        source_url="https://example.invalid/external-skill",
        source_revision="deadbeef",
        license="MIT",
    )
    discovered = service.create_skill(
        _profile(
            "External",
            "method",
            source=source,
            enabled=False,
            trust_status=SkillTrustStatus.DISCOVERED,
        ),
        owner_ref=OWNER,
    )

    bypass = replace(
        discovered.profile,
        trust_status=SkillTrustStatus.ADOPTED,
        evaluation_status=SkillEvaluationStatus.PASSED,
        evaluation_metadata={"forged": True},
    )
    with pytest.raises(ContractError) as error:
        service.update_skill(discovered.skill_id, bypass, expected_revision=1)

    assert error.value.code is ErrorCode.FORBIDDEN
    assert repository.get_skill(discovered.skill_id).current_revision == 1


def test_dependency_cycle_fails_closed() -> None:
    repository = InMemorySkillRepository()
    service = SkillService(repository)
    left_id = new_skill_id()
    right_id = new_skill_id()
    left = service.create_skill(
        _profile("Left", "left", dependencies=(SkillRevisionRef(right_id, 1),)),
        owner_ref=OWNER,
        skill_id=left_id,
    )
    service.create_skill(
        _profile("Right", "right", dependencies=(SkillRevisionRef(left_id, 1),)),
        owner_ref=OWNER,
        skill_id=right_id,
    )

    with pytest.raises(ContractError) as error:
        SkillResolver(repository).resolve(_request(left.ref))

    assert error.value.code is ErrorCode.CONFLICT
    assert "cycle" in str(error.value).lower()


def test_skill_model_prerequisite_is_checked_against_canonical_model() -> None:
    repository = InMemorySkillRepository()
    service = SkillService(repository)
    from ai_multi_agent_platform.models import RoutingRequirements

    skill = service.create_skill(
        _profile(
            "Tool-aware method",
            "method",
            routing_requirements=RoutingRequirements(tool_calling=True),
        ),
        owner_ref=OWNER,
    )
    incompatible = ModelConfiguration(
        config_id="model-config-test",
        display_name="No tools",
        provider_id="provider-test",
        capabilities=ModelCapabilities(tool_calling=False),
    )

    with pytest.raises(ContractError) as error:
        SkillResolver(repository).resolve(_request(skill.ref, model=incompatible))

    assert error.value.code is ErrorCode.NO_COMPATIBLE_ROUTE


def test_portable_external_skill_import_requires_target_side_revalidation() -> None:
    source_repository = InMemorySkillRepository()
    source_service = SkillService(source_repository)
    source = SkillSource(
        source_url="https://example.invalid/external-skill",
        source_revision="cafebabe",
        license="MIT",
        checksum="a" * 64,
    )
    revision = source_service.create_skill(
        _profile(
            "External",
            "method",
            source=source,
            enabled=False,
            trust_status=SkillTrustStatus.DISCOVERED,
        ),
        owner_ref=OWNER,
    )
    revision = source_service.transition_trust(
        revision.skill_id,
        SkillTrustStatus.SOURCE_VERIFIED,
        expected_revision=revision.revision,
    )
    revision = source_service.transition_trust(
        revision.skill_id,
        SkillTrustStatus.SECURITY_REVIEWED,
        expected_revision=revision.revision,
    )
    revision = source_service.transition_trust(
        revision.skill_id,
        SkillTrustStatus.PILOT,
        expected_revision=revision.revision,
    )
    revision = source_service.transition_trust(
        revision.skill_id,
        SkillTrustStatus.ADOPTED,
        expected_revision=revision.revision,
        evaluation_status=SkillEvaluationStatus.PASSED,
        evaluation_metadata={"suite": "external-eval-v1"},
    )
    revision = source_service.set_enabled(
        revision.skill_id,
        True,
        expected_revision=revision.revision,
    )
    assert revision.profile.enabled is True

    serializers = ResourceSerializerRegistry()
    register_skill_portability_codecs(serializers)
    resource = serializers.serialize(
        SKILL_RESOURCE_TYPE,
        snapshot_skill(source_repository, revision.skill_id),
    )
    decoded = serializers.deserialize(resource)
    assert isinstance(decoded, SkillPortableSnapshot)

    target_repository = InMemorySkillRepository()
    handler = SkillImportMutationHandler(target_repository)
    context = ImportContext()
    asyncio.run(handler.preflight(resource, decoded, context))
    asyncio.run(handler.apply(resource, decoded, context))

    imported = target_repository.get_skill_revision(
        revision.skill_id,
        target_repository.get_skill(revision.skill_id).current_revision,
    )
    assert imported.profile.enabled is False
    assert imported.profile.trust_status is SkillTrustStatus.DISCOVERED
    assert imported.profile.evaluation_status is SkillEvaluationStatus.NOT_EVALUATED
    assert imported.profile.evaluation_metadata == {}
    assert imported.provenance is not None
    assert imported.provenance.details["portable_import_requires_revalidation"] is True
    assert imported.provenance.details["imported_source_trust_status"] == "adopted"
