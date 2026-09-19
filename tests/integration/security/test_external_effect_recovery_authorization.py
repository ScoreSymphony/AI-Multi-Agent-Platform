from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.capabilities import (
    ExternalEffectIdempotency,
    ExternalEffectReconciliationSupport,
    ExternalEffectRecoveryCoordinator,
    ExternalEffectRecoveryDisposition,
    ExternalEffectRecoveryRecord,
    ExternalEffectRecoveryStatus,
    InMemoryExternalEffectRecoveryRepository,
    SideEffectClassification,
)
from ai_multi_agent_platform.capabilities.recovery_control_plane import (
    EXTERNAL_EFFECT_AUTHORIZE_RETRY_COMMAND,
    register_external_effect_recovery_control_plane,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import ActorContext, ControlPlane, RequestContext
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def _record() -> ExternalEffectRecoveryRecord:
    now = datetime.now(UTC)
    return ExternalEffectRecoveryRecord(
        effect_id="external_effect_authz",
        invocation_id="invocation-authz",
        canonical_tool_invocation_id=None,
        task_id="task_authz",
        run_id="run_authz",
        capability_id="external.publish",
        capability_version="1.0",
        provider_id="provider.authz",
        provider_tool_ref="publish",
        side_effects=SideEffectClassification.EXTERNAL,
        idempotency=ExternalEffectIdempotency.NONE,
        reconciliation_support=ExternalEffectReconciliationSupport.UNSUPPORTED,
        idempotency_key="private-idempotency-key",
        status=ExternalEffectRecoveryStatus.BLOCKED,
        disposition=ExternalEffectRecoveryDisposition.UNCERTAIN_MANUAL_REVIEW,
        reason="provider_outcome_unacknowledged:timeout",
        created_at=now,
        updated_at=now,
    )


def _control_plane(
    authorization: FakeAuthorizationProvider,
    recovery: ExternalEffectRecoveryCoordinator,
) -> ControlPlane:
    repository = InMemoryKernelRepository()
    control_plane = ControlPlane(
        kernel=PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=repository,
        ),
        events=repository,
        authorization=authorization,
    )
    register_external_effect_recovery_control_plane(control_plane, recovery)
    return control_plane


@pytest.mark.asyncio
async def test_operator_retry_authorization_is_checked_before_recovery_transition() -> None:
    repository = InMemoryExternalEffectRecoveryRepository()
    original = repository.save(_record())
    recovery = ExternalEffectRecoveryCoordinator(repository)
    authorization = FakeAuthorizationProvider(allowed=False)
    control_plane = _control_plane(authorization, recovery)
    context = RequestContext(
        request_id="external-effect-authz-denied",
        correlation_id="external-effect-authz-denied",
        actor=ActorContext(principal_ref="operator:denied"),
        idempotency_key="operator-command-key",
    )

    with pytest.raises(ContractError) as caught:
        await control_plane.execute_command(
            context,
            EXTERNAL_EFFECT_AUTHORIZE_RETRY_COMMAND,
            original.effect_id,
            {"reason": "reviewed provider logs"},
        )

    assert caught.value.code is ErrorCode.FORBIDDEN
    assert repository.get(original.effect_id) == original
    assert [call.action for call in authorization.calls] == [
        EXTERNAL_EFFECT_AUTHORIZE_RETRY_COMMAND
    ]


@pytest.mark.asyncio
async def test_operator_recovery_transition_requires_control_plane_idempotency_key() -> None:
    repository = InMemoryExternalEffectRecoveryRepository()
    original = repository.save(_record())
    recovery = ExternalEffectRecoveryCoordinator(repository)
    authorization = FakeAuthorizationProvider(allowed=True)
    control_plane = _control_plane(authorization, recovery)
    context = RequestContext(
        request_id="external-effect-missing-command-key",
        correlation_id="external-effect-missing-command-key",
        actor=ActorContext(principal_ref="operator:reviewer"),
    )

    with pytest.raises(ContractError) as caught:
        await control_plane.execute_command(
            context,
            EXTERNAL_EFFECT_AUTHORIZE_RETRY_COMMAND,
            original.effect_id,
            {"reason": "reviewed provider logs"},
        )

    assert caught.value.code is ErrorCode.INVALID_REQUEST
    assert repository.get(original.effect_id) == original
    assert authorization.calls == []
