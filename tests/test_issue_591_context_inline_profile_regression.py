from __future__ import annotations

import asyncio
from types import SimpleNamespace

from ai_multi_agent_platform.context import ModelRegistryContextEgressTargetResolver
from ai_multi_agent_platform.contracts import (
    DataClassification,
    EgressCostClass,
    EgressOutcome,
    EgressProfileTrust,
    EgressReasonCode,
    EgressRequest,
    EgressTargetKind,
    OperationContext,
    digest_egress_payload,
)
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
)
from ai_multi_agent_platform.security import build_durable_egress_runtime


def test_context_projects_inline_model_profile_into_strict_external_egress(tmp_path) -> None:
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
    resolver = ModelRegistryContextEgressTargetResolver(registry)

    target = resolver.resolve(
        SimpleNamespace(selected_model_config_id="model-remote-context"),
        SimpleNamespace(),
    )

    assert target is not None
    assert target.kind is EgressTargetKind.CONTEXT_EXPORT
    assert target.target_id == "model-remote-context"
    assert target.profile is not None
    assert target.profile.profile_id == "profile-remote-context:context-export"
    assert target.profile.target_kind is EgressTargetKind.CONTEXT_EXPORT
    assert target.profile.target_id == "model-remote-context"
    assert target.profile.cost_class is EgressCostClass.FREE_EXTERNAL
    assert target.profile.trust is EgressProfileTrust.CONFIGURED
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
