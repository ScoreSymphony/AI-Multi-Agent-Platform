from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.contracts import (
    ContractError,
    DataClassification,
    EgressCostClass,
    EgressProfile,
    EgressProfileTrust,
    EgressTargetKind,
    EgressTargetPosture,
    ErrorCode,
    OperationContext,
)
from ai_multi_agent_platform.control_plane import ActorContext, RequestContext
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.portability.egress_profile_codecs import (
    EGRESS_PROFILE_RESOURCE_TYPE,
    EgressProfilePortableCodec,
    snapshot_egress_profile,
)
from ai_multi_agent_platform.portability.egress_profile_import import (
    EgressProfileImportMutationHandler,
)
from ai_multi_agent_platform.portability.registry import ImportContext, ResourceSerializerRegistry
from ai_multi_agent_platform.security.egress_control_plane import EgressProfileCommandHandlers
from ai_multi_agent_platform.security.egress_profiles import (
    EgressProfileDefinition,
    EgressProfileService,
    JsonEgressProfileRepository,
)

OWNER = OwnerRef(type="user", id="egress-profile-owner")


def _context(project_id: str | None = None) -> OperationContext:
    return OperationContext(
        correlation_id="corr-egress-profile-591",
        owner_type=OWNER.type,
        owner_id=OWNER.id,
        project_id=project_id,
    )


def _request_context() -> RequestContext:
    return RequestContext(
        request_id="request-egress-profile-591",
        correlation_id="corr-egress-profile-591",
        actor=ActorContext(
            principal_ref=OWNER.id,
            owner_type=OWNER.type,
            owner_id=OWNER.id,
            actor_type="human",
        ),
    )


def _profile(
    profile_id: str,
    target_id: str,
    *,
    revision: int = 1,
    trust: EgressProfileTrust = EgressProfileTrust.CONFIGURED,
    cost: EgressCostClass = EgressCostClass.FREE_EXTERNAL,
    metadata: dict[str, object] | None = None,
) -> EgressProfile:
    return EgressProfile(
        profile_id=profile_id,
        revision=revision,
        target_kind=EgressTargetKind.MODEL_PROVIDER,
        target_id=target_id,
        posture=EgressTargetPosture.EXTERNAL,
        allowed_classifications=(
            DataClassification.PUBLIC,
            DataClassification.INTERNAL,
        ),
        network_egress_required=True,
        cost_class=cost,
        policy_source="operator-config",
        source_revision=f"operator-{revision}",
        trust=trust,
        metadata=metadata or {},
    )


def test_durable_profile_history_survives_restart_and_preserves_exact_revisions(tmp_path) -> None:
    path = tmp_path / "egress-profiles.json"
    project_id = new_id("project")
    profile_id = "egress_profile_restart_591"
    target_id = new_id("model_config")
    service = EgressProfileService(JsonEgressProfileRepository(path))

    first = _profile(profile_id, target_id)
    asyncio.run(
        service.create_profile(
            first,
            owner_ref=OWNER,
            project_id=project_id,
            principal_ref=OWNER.id,
            context=_context(project_id),
        )
    )
    second = replace(
        first,
        revision=2,
        denied_classifications=(DataClassification.CONFIDENTIAL,),
        source_revision="operator-2",
    )
    asyncio.run(
        service.version_profile(
            second,
            expected_revision=1,
            principal_ref=OWNER.id,
            context=_context(project_id),
        )
    )

    restored = JsonEgressProfileRepository(path)
    definition = restored.get_definition(profile_id)

    assert definition.current_revision == 2
    assert restored.get_revision(profile_id, 1) == first
    assert restored.get_revision(profile_id, 2) == second
    assert restored.resolve(EgressTargetKind.MODEL_PROVIDER, target_id, project_id) == second


def test_project_profile_precedes_global_default_and_disable_falls_back(tmp_path) -> None:
    repository = JsonEgressProfileRepository(tmp_path / "egress-profiles.json")
    target_id = new_id("model_config")
    project_id = new_id("project")
    now = datetime.now(UTC)
    global_profile = _profile("egress_profile_global_591", target_id)
    scoped_profile = replace(
        global_profile,
        profile_id="egress_profile_scoped_591",
        cost_class=EgressCostClass.PAID_EXTERNAL,
    )
    repository.create_profile(
        EgressProfileDefinition(
            profile_id=global_profile.profile_id,
            target_kind=global_profile.target_kind,
            target_id=target_id,
            owner_ref=OWNER,
            current_revision=1,
            created_at=now,
            updated_at=now,
        ),
        global_profile,
    )
    repository.create_profile(
        EgressProfileDefinition(
            profile_id=scoped_profile.profile_id,
            target_kind=scoped_profile.target_kind,
            target_id=target_id,
            owner_ref=OWNER,
            current_revision=1,
            project_id=project_id,
            created_at=now,
            updated_at=now,
        ),
        scoped_profile,
    )

    assert (
        repository.resolve(EgressTargetKind.MODEL_PROVIDER, target_id, project_id) == scoped_profile
    )
    assert (
        repository.resolve(EgressTargetKind.MODEL_PROVIDER, target_id, new_id("project"))
        == global_profile
    )

    repository.set_enabled(scoped_profile.profile_id, False)
    assert (
        repository.resolve(EgressTargetKind.MODEL_PROVIDER, target_id, project_id) == global_profile
    )


def test_verified_trust_requires_explicit_verification_transition(tmp_path) -> None:
    repository = JsonEgressProfileRepository(tmp_path / "egress-profiles.json")
    service = EgressProfileService(repository)
    profile = _profile("egress_profile_verify_591", new_id("model_config"))
    asyncio.run(
        service.create_profile(
            profile,
            owner_ref=OWNER,
            project_id=None,
            principal_ref=OWNER.id,
            context=_context(),
        )
    )

    with pytest.raises(ContractError) as direct_verified:
        asyncio.run(
            service.version_profile(
                replace(profile, revision=2, trust=EgressProfileTrust.VERIFIED),
                expected_revision=1,
                principal_ref=OWNER.id,
                context=_context(),
            )
        )
    assert direct_verified.value.code is ErrorCode.FORBIDDEN

    verified = asyncio.run(
        service.verify_profile(
            profile.profile_id,
            expected_revision=1,
            verification_ref="operator-review:591",
            principal_ref=OWNER.id,
            context=_context(),
        )
    )
    assert verified.revision == 2
    assert verified.trust is EgressProfileTrust.VERIFIED
    assert verified.metadata["verification_ref"] == "operator-review:591"


def test_control_plane_create_never_accepts_self_asserted_verified_trust(tmp_path) -> None:
    handlers = EgressProfileCommandHandlers(
        EgressProfileService(JsonEgressProfileRepository(tmp_path / "egress-profiles.json"))
    )
    payload = {
        "profile_id": "egress_profile_control_plane_591",
        "target_kind": "model_provider",
        "target_id": new_id("model_config"),
        "posture": "external",
        "source_revision": "operator-config:1",
        "trust": "verified",
    }

    with pytest.raises(ContractError) as denied:
        asyncio.run(
            handlers.create_profile(
                _request_context(),
                "egress-profiles",
                payload,
            )
        )

    assert denied.value.code is ErrorCode.FORBIDDEN


def test_portability_import_strips_source_authority_and_arbitrary_metadata(tmp_path) -> None:
    source = JsonEgressProfileRepository(tmp_path / "source.json")
    profile = _profile(
        "egress_profile_portable_591",
        new_id("model_config"),
        trust=EgressProfileTrust.VERIFIED,
        metadata={
            "allow_sensitive_external": True,
            "verification_ref": "source-review",
            "verified_by": "source-admin",
            "safe_note": "portable",
        },
    )
    now = datetime.now(UTC)
    source.create_profile(
        EgressProfileDefinition(
            profile_id=profile.profile_id,
            target_kind=profile.target_kind,
            target_id=profile.target_id,
            owner_ref=OWNER,
            current_revision=1,
            created_at=now,
            updated_at=now,
        ),
        profile,
    )
    serializers = ResourceSerializerRegistry()
    serializers.register(EgressProfilePortableCodec())
    resource = serializers.serialize(
        EGRESS_PROFILE_RESOURCE_TYPE,
        snapshot_egress_profile(source, profile.profile_id),
    )

    decoded = serializers.deserialize(resource, ImportContext())
    imported_revision = decoded.revisions[0]

    assert imported_revision.trust is EgressProfileTrust.UNVERIFIED
    assert "allow_sensitive_external" not in imported_revision.metadata
    assert "verification_ref" not in imported_revision.metadata
    assert "verified_by" not in imported_revision.metadata
    assert "safe_note" not in imported_revision.metadata
    assert imported_revision.metadata["imported_unverified"] is True

    target = JsonEgressProfileRepository(tmp_path / "target.json")
    handler = EgressProfileImportMutationHandler(target, target_owner_ref=OWNER)
    asyncio.run(handler.preflight(resource, decoded, ImportContext()))
    token = asyncio.run(handler.apply(resource, decoded, ImportContext()))

    assert token == profile.profile_id
    assert target.get_revision(profile.profile_id, 1).trust is EgressProfileTrust.UNVERIFIED
