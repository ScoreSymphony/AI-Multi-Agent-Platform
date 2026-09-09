from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ai_multi_agent_platform.context import ModelRegistryContextEgressTargetResolver
from ai_multi_agent_platform.contracts import (
    ContractError,
    DataClassification,
    EgressCostClass,
    EgressOutcome,
    EgressProfile,
    EgressProfileTrust,
    EgressReasonCode,
    EgressRequest,
    EgressTargetKind,
    EgressTargetPosture,
    OperationContext,
    digest_egress_payload,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
)
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationGate,
    EgressApprovalExceptionPolicy,
    build_durable_egress_runtime,
)
from ai_multi_agent_platform.security.egress import InMemoryEgressAuditSink
from ai_multi_agent_platform.security.egress_profiles import EgressProfileDefinition
from ai_multi_agent_platform.testing import FakeAuthorizationProvider


def _remote_registry() -> ModelRegistry:
    registry = ModelRegistry()
    registry.register_model(
        ModelConfiguration(
            config_id="model-remote-context",
            display_name="Remote Context Model",
            provider_id="provider-remote-context",
            capabilities=ModelCapabilities(context_window=32_000),
            location=ModelLocation.REMOTE,
            resource_hints={
                "egress_profile": {
                    "profile_id": "profile-remote-context",
                    "revision": 3,
                    "posture": "external",
                    "allowed_classifications": ["public"],
                    "network_egress_required": True,
                    "cost_class": "free_external",
                    "trust": "configured",
                    "policy_source": "model-resource-hints",
                    "source_revision": "model-policy-3",
                }
            },
        )
    )
    return registry


def _context_target():
    resolver = ModelRegistryContextEgressTargetResolver(_remote_registry())
    return resolver.resolve(
        SimpleNamespace(selected_model_config_id="model-remote-context"),
        SimpleNamespace(),
    )


def _durable_context_denial(*, revision: int = 1) -> EgressProfile:
    return EgressProfile(
        profile_id="profile-context-project-deny",
        revision=revision,
        target_kind=EgressTargetKind.CONTEXT_EXPORT,
        target_id="provider-remote-context",
        posture=EgressTargetPosture.EXTERNAL,
        denied_classifications=(DataClassification.PUBLIC,),
        network_egress_required=True,
        cost_class=EgressCostClass.FREE_EXTERNAL,
        trust=EgressProfileTrust.CONFIGURED,
        policy_source="operator-config",
        source_revision=f"context-policy-{revision}",
    )


def test_context_projects_inline_model_profile_into_strict_external_egress(tmp_path) -> None:
    target = _context_target()

    assert target is not None
    assert target.kind is EgressTargetKind.CONTEXT_EXPORT
    assert target.target_id == "provider-remote-context"
    assert target.profile is not None
    assert target.profile.profile_id == "profile-remote-context:context-export"
    assert target.profile.target_kind is EgressTargetKind.CONTEXT_EXPORT
    assert target.profile.target_id == "provider-remote-context"
    assert target.profile.cost_class is EgressCostClass.FREE_EXTERNAL
    assert target.profile.trust is EgressProfileTrust.CONFIGURED
    assert target.policy_metadata["model_config_id"] == "model-remote-context"
    assert target.policy_metadata["provider_id"] == "provider-remote-context"

    runtime = build_durable_egress_runtime(tmp_path / "egress-profiles.json")
    decision = asyncio.run(
        runtime.gate.evaluate(
            EgressRequest(
                request_id="context-inline-profile-regression",
                target=target,
                context=OperationContext(correlation_id="corr-context-inline-profile"),
                classification=DataClassification.PUBLIC,
                resource_type="context_bundle",
                payload_digest=digest_egress_payload({"context_bundle_id": "bundle-test"}),
            )
        )
    )

    assert decision.outcome is EgressOutcome.ALLOW
    assert decision.reason_code is EgressReasonCode.ALLOWED


def test_durable_provider_context_profile_precedes_inline_model_profile(tmp_path) -> None:
    target = _context_target()
    assert target is not None

    runtime = build_durable_egress_runtime(tmp_path / "egress-profiles.json")
    project_id = new_id("project")
    now = datetime.now(UTC)
    durable_profile = _durable_context_denial()
    runtime.repository.create_profile(
        EgressProfileDefinition(
            profile_id=durable_profile.profile_id,
            target_kind=durable_profile.target_kind,
            target_id=durable_profile.target_id,
            owner_ref=OwnerRef(type="user", id="context-policy-owner"),
            current_revision=1,
            project_id=project_id,
            created_at=now,
            updated_at=now,
        ),
        durable_profile,
    )

    decision = asyncio.run(
        runtime.gate.evaluate(
            EgressRequest(
                request_id="context-durable-profile-precedence",
                target=target,
                context=OperationContext(
                    correlation_id="corr-context-durable-profile",
                    project_id=project_id,
                ),
                classification=DataClassification.PUBLIC,
                resource_type="context_bundle",
                payload_digest=digest_egress_payload({"context_bundle_id": "bundle-project"}),
            )
        )
    )

    assert decision.outcome is EgressOutcome.DENY
    assert decision.reason_code is EgressReasonCode.TARGET_POLICY_DENIED


def test_durable_context_profile_drives_audit_and_enforcement_fields(tmp_path) -> None:
    target = _context_target()
    assert target is not None
    assert target.profile is not None
    assert target.profile.posture is EgressTargetPosture.EXTERNAL
    assert target.profile.cost_class is EgressCostClass.FREE_EXTERNAL

    audit_sink = InMemoryEgressAuditSink()
    runtime = build_durable_egress_runtime(
        tmp_path / "egress-profiles.json",
        audit_sink=audit_sink,
    )
    project_id = new_id("project")
    now = datetime.now(UTC)
    durable_profile = replace(
        _durable_context_denial(),
        posture=EgressTargetPosture.INTERNAL,
        network_egress_required=False,
        cost_class=EgressCostClass.LOCAL,
    )
    runtime.repository.create_profile(
        EgressProfileDefinition(
            profile_id=durable_profile.profile_id,
            target_kind=durable_profile.target_kind,
            target_id=durable_profile.target_id,
            owner_ref=OwnerRef(type="user", id="context-audit-owner"),
            current_revision=1,
            project_id=project_id,
            created_at=now,
            updated_at=now,
        ),
        durable_profile,
    )
    request = EgressRequest(
        request_id="context-durable-profile-audit",
        target=target,
        context=OperationContext(
            correlation_id="corr-context-durable-audit",
            project_id=project_id,
        ),
        classification=DataClassification.PUBLIC,
        resource_type="context_bundle",
        payload_digest=digest_egress_payload({"context_bundle_id": "bundle-audit"}),
    )

    with pytest.raises(ContractError) as denied:
        asyncio.run(runtime.gate.enforce(request))

    assert denied.value.details["target_posture"] == EgressTargetPosture.INTERNAL.value
    assert denied.value.details["profile_ref"] == durable_profile.canonical_ref
    assert denied.value.details["cost_class"] == EgressCostClass.LOCAL.value
    assert len(audit_sink.events) == 2
    for event in audit_sink.events:
        assert event.target_posture is EgressTargetPosture.INTERNAL
        assert event.profile_ref == durable_profile.canonical_ref
        assert event.cost_class is EgressCostClass.LOCAL


def test_durable_context_profile_revision_invalidates_inline_fallback_approval(tmp_path) -> None:
    target = _context_target()
    assert target is not None

    authorization = AuthorizationGate(FakeAuthorizationProvider())
    runtime = build_durable_egress_runtime(
        tmp_path / "egress-profiles.json",
        approval_gate=authorization,
        approval_policy=EgressApprovalExceptionPolicy(
            approvable_reason_codes=frozenset({EgressReasonCode.TARGET_POLICY_DENIED})
        ),
    )
    actor = ActorIdentity(new_id("user"), ActorType.HUMAN)
    approver = ActorIdentity(new_id("user"), ActorType.HUMAN)
    project_id = new_id("project")
    operation = OperationContext(
        correlation_id="corr-context-approval",
        project_id=project_id,
    )
    now = datetime.now(UTC)
    durable_profile = _durable_context_denial()
    runtime.repository.create_profile(
        EgressProfileDefinition(
            profile_id=durable_profile.profile_id,
            target_kind=durable_profile.target_kind,
            target_id=durable_profile.target_id,
            owner_ref=OwnerRef(type="user", id=actor.actor_id),
            current_revision=1,
            project_id=project_id,
            created_at=now,
            updated_at=now,
        ),
        durable_profile,
    )
    request = EgressRequest(
        request_id="context-durable-profile-approval",
        target=target,
        context=operation,
        classification=DataClassification.PUBLIC,
        resource_type="context_bundle",
        payload_digest=digest_egress_payload({"context_bundle_id": "bundle-approval"}),
    )

    pending = asyncio.run(runtime.gate.evaluate(request, actor=actor))
    assert pending.outcome is EgressOutcome.REQUIRE_APPROVAL
    approval_id = pending.approval_ref
    assert approval_id is not None
    assert pending.audit_metadata["profile_ref"] == durable_profile.canonical_ref

    asyncio.run(
        authorization.decide_approval(
            approval_id,
            approver=approver,
            approve=True,
            operation=operation,
        )
    )
    approved = asyncio.run(runtime.gate.evaluate(request, actor=actor, approval_id=approval_id))
    assert approved.outcome is EgressOutcome.ALLOW
    assert approved.approval_ref == approval_id

    updated_profile = replace(
        durable_profile,
        revision=2,
        source_revision="context-policy-2",
    )
    asyncio.run(
        runtime.profiles.version_profile(
            updated_profile,
            expected_revision=1,
            principal_ref=actor.actor_id,
            context=operation,
        )
    )

    stale = asyncio.run(runtime.gate.evaluate(request, actor=actor, approval_id=approval_id))
    assert stale.outcome is EgressOutcome.REQUIRE_APPROVAL
    assert stale.approval_ref is not None
    assert stale.approval_ref != approval_id
    assert stale.audit_metadata["profile_ref"] == updated_profile.canonical_ref
