from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from ai_multi_agent_platform.context import ModelRegistryContextEgressTargetResolver
from ai_multi_agent_platform.contracts import (
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
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
)
from ai_multi_agent_platform.security import build_durable_egress_runtime
from ai_multi_agent_platform.security.egress_profiles import EgressProfileDefinition


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
    project_id = "project-context-restricted"
    now = datetime.now(UTC)
    durable_profile = EgressProfile(
        profile_id="profile-context-project-deny",
        revision=1,
        target_kind=EgressTargetKind.CONTEXT_EXPORT,
        target_id="provider-remote-context",
        posture=EgressTargetPosture.EXTERNAL,
        denied_classifications=(DataClassification.PUBLIC,),
        network_egress_required=True,
        cost_class=EgressCostClass.FREE_EXTERNAL,
        trust=EgressProfileTrust.CONFIGURED,
        policy_source="operator-config",
        source_revision="context-policy-1",
    )
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
