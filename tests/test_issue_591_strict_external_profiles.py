from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.contracts import (
    DataClassification,
    EgressCostClass,
    EgressOutcome,
    EgressProfile,
    EgressProfileTrust,
    EgressReasonCode,
    EgressRequest,
    EgressTarget,
    EgressTargetKind,
    EgressTargetPosture,
    OperationContext,
    digest_egress_payload,
)
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.security import build_durable_egress_runtime


def _request(
    *,
    posture: EgressTargetPosture,
    profile: EgressProfile | None = None,
) -> EgressRequest:
    return EgressRequest(
        request_id="strict-external-profile-test",
        target=EgressTarget(
            kind=EgressTargetKind.API,
            target_id="api:test",
            posture=EgressTargetPosture.UNKNOWN if profile is not None else posture,
            profile=profile,
        ),
        context=OperationContext(correlation_id="corr-strict-egress"),
        classification=DataClassification.PUBLIC,
        resource_type="test_payload",
        payload_digest=digest_egress_payload({"value": "not-logged"}),
    )


def _configured_free_external_profile() -> EgressProfile:
    return EgressProfile(
        profile_id="egress-profile:api:test",
        revision=1,
        target_kind=EgressTargetKind.API,
        target_id="api:test",
        posture=EgressTargetPosture.EXTERNAL,
        allowed_classifications=(DataClassification.PUBLIC,),
        network_egress_required=True,
        cost_class=EgressCostClass.FREE_EXTERNAL,
        credential_required=False,
        policy_source="test-config",
        source_revision="1",
        trust=EgressProfileTrust.CONFIGURED,
    )


def test_durable_runtime_blocks_profileless_external_by_default(tmp_path: Path) -> None:
    runtime = build_durable_egress_runtime(tmp_path / "egress-profiles.json")

    decision = asyncio.run(
        runtime.gate.evaluate(_request(posture=EgressTargetPosture.EXTERNAL))
    )

    assert decision.outcome is EgressOutcome.UNKNOWN_BLOCKED
    assert decision.reason_code is EgressReasonCode.UNVERIFIED_PROFILE
    assert decision.audit_metadata["external_profile_required"] is True


def test_public_single_node_uses_strict_external_profile_policy(tmp_path: Path) -> None:
    deployment = build_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
    )

    decision = asyncio.run(
        deployment.egress.runtime.gate.evaluate(
            _request(posture=EgressTargetPosture.EXTERNAL)
        )
    )

    assert decision.outcome is EgressOutcome.UNKNOWN_BLOCKED
    assert decision.reason_code is EgressReasonCode.UNVERIFIED_PROFILE


def test_durable_runtime_keeps_profileless_local_functional(tmp_path: Path) -> None:
    runtime = build_durable_egress_runtime(tmp_path / "egress-profiles.json")

    decision = asyncio.run(runtime.gate.evaluate(_request(posture=EgressTargetPosture.LOCAL)))

    assert decision.outcome is EgressOutcome.ALLOW
    assert decision.reason_code is EgressReasonCode.ALLOWED


def test_inline_external_profile_satisfies_strict_requirement(tmp_path: Path) -> None:
    runtime = build_durable_egress_runtime(tmp_path / "egress-profiles.json")

    decision = asyncio.run(
        runtime.gate.evaluate(
            _request(
                posture=EgressTargetPosture.EXTERNAL,
                profile=_configured_free_external_profile(),
            )
        )
    )

    assert decision.outcome is EgressOutcome.ALLOW
    assert decision.reason_code is EgressReasonCode.ALLOWED


def test_focused_embedding_can_opt_out_of_strict_external_profiles(tmp_path: Path) -> None:
    runtime = build_durable_egress_runtime(
        tmp_path / "egress-profiles.json",
        require_external_profile=False,
    )

    decision = asyncio.run(
        runtime.gate.evaluate(_request(posture=EgressTargetPosture.EXTERNAL))
    )

    assert decision.outcome is EgressOutcome.ALLOW
    assert decision.reason_code is EgressReasonCode.ALLOWED
