from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.capabilities.types import (
    CapabilityInvocation,
    CapabilitySpec,
    InvocationTrace,
    PolicyDecision,
    SafetyClassification,
    SideEffectClassification,
)
from ai_multi_agent_platform.configuration import LocalSecretProvider, SecretAccessContext
from ai_multi_agent_platform.contracts import (
    AuthorizationDecision,
    AuthorizationOutcome,
    ContractError,
    ErrorCode,
    OperationContext,
    ToolInvocation,
)
from ai_multi_agent_platform.domain import ApprovalStatus, new_id
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    ApprovalService,
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ProposedAction,
    ResourceType,
)
from ai_multi_agent_platform.security.capability_bridge import CapabilityAuthorizationBridge
from ai_multi_agent_platform.security.enforced_providers import (
    AuthorizedFileProvider,
    AuthorizedSecretProvider,
    AuthorizedToolProvider,
)
from ai_multi_agent_platform.security.types import SecretReference
from ai_multi_agent_platform.testing import FakeFileProvider, FakeToolProvider


def _context(
    actor: ActorIdentity,
    *,
    action: AuthorizationAction,
    resource_type: ResourceType,
    resource_id: str,
    project_id: str | None = None,
    correlation_id: str = "corr-auth",
) -> AuthorizationContext:
    return AuthorizationContext(
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        operation=OperationContext(
            correlation_id=correlation_id,
            owner_type="service",
            owner_id=actor.actor_id,
            project_id=project_id,
        ),
    )


def test_authorization_decision_keeps_issue_five_allowed_compatibility() -> None:
    allowed = AuthorizationDecision(allowed=True, reason="legacy")
    denied = AuthorizationDecision(allowed=False, reason="legacy")

    assert allowed.outcome is AuthorizationOutcome.ALLOW
    assert allowed.allowed is True
    assert denied.outcome is AuthorizationOutcome.DENY
    assert denied.allowed is False


def test_local_provider_is_deny_by_default_and_allows_explicit_action() -> None:
    actor = ActorIdentity("user:alice", ActorType.HUMAN)
    policy = LocalPrincipalPolicy(
        principal_ref=actor.actor_id,
        actor_types=frozenset({ActorType.HUMAN}),
        allowed_actions=frozenset({AuthorizationAction.READ}),
        resource_types=frozenset({ResourceType.FILE}),
    )
    provider = LocalAuthorizationProvider((policy,))
    gate = AuthorizationGate(provider)

    allowed = asyncio.run(
        gate.enforce(
            ProposedAction(
                _context(
                    actor,
                    action=AuthorizationAction.READ,
                    resource_type=ResourceType.FILE,
                    resource_id="file:one",
                )
            )
        )
    )
    assert allowed.allowed

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            gate.enforce(
                ProposedAction(
                    _context(
                        actor,
                        action=AuthorizationAction.DELETE,
                        resource_type=ResourceType.FILE,
                        resource_id="file:one",
                    )
                )
            )
        )
    assert captured.value.code is ErrorCode.FORBIDDEN


def test_required_approval_resumes_only_the_exact_action() -> None:
    agent = ActorIdentity(new_id("agent"), ActorType.AGENT)
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=agent.actor_id,
                actor_types=frozenset({ActorType.AGENT}),
                approval_actions=frozenset({AuthorizationAction.EXECUTE}),
                resource_types=frozenset({ResourceType.TOOL}),
            ),
        )
    )
    approvals = ApprovalService()
    gate = AuthorizationGate(provider, approvals=approvals)
    context = _context(
        agent,
        action=AuthorizationAction.EXECUTE,
        resource_type=ResourceType.TOOL,
        resource_id="tool:write",
    )
    original = ProposedAction(context, payload={"path": "a.txt", "content": "one"})

    first = asyncio.run(gate.decide(original))
    assert first.outcome is AuthorizationOutcome.REQUIRE_APPROVAL
    approval_id = first.constraints["approval_id"]
    assert isinstance(approval_id, str)
    approvals.decide(approval_id, approver_ref="user:reviewer", approve=True)

    resumed = asyncio.run(gate.enforce(original, approval_id=approval_id))
    assert resumed.allowed

    modified = ProposedAction(context, payload={"path": "a.txt", "content": "two"})
    with pytest.raises(ContractError) as captured:
        asyncio.run(gate.enforce(modified, approval_id=approval_id))
    assert captured.value.details["authorization_outcome"] == "require_approval"
    assert captured.value.details["approval_id"] != approval_id


def test_rejected_cancelled_and_expired_approvals_never_authorize() -> None:
    actor = ActorIdentity("service:automation", ActorType.SERVICE)
    action = ProposedAction(
        _context(
            actor,
            action=AuthorizationAction.EXECUTE,
            resource_type=ResourceType.TASK,
            resource_id=new_id("task"),
        )
    )
    approvals = ApprovalService()

    rejected = approvals.request(action, reason="review", policy_id="test")
    approvals.decide(rejected.approval_id, approver_ref="user:reviewer", approve=False)
    assert approvals.get(rejected.approval_id).status is ApprovalStatus.REJECTED
    assert not approvals.valid_for(rejected.approval_id, action)

    other = ProposedAction(
        _context(
            actor,
            action=AuthorizationAction.EXECUTE,
            resource_type=ResourceType.TASK,
            resource_id=new_id("task"),
        )
    )
    cancelled = approvals.request(other, reason="review", policy_id="test")
    approvals.cancel(cancelled.approval_id, actor_ref=actor.actor_id)
    assert approvals.get(cancelled.approval_id).status is ApprovalStatus.CANCELLED
    assert not approvals.valid_for(cancelled.approval_id, other)

    expiring = ProposedAction(
        _context(
            actor,
            action=AuthorizationAction.EXECUTE,
            resource_type=ResourceType.TASK,
            resource_id=new_id("task"),
        )
    )
    expiring_record = approvals.request(
        expiring,
        reason="short review",
        policy_id="test",
        expires_at=datetime.now(UTC) + timedelta(milliseconds=5),
    )
    time.sleep(0.01)
    assert approvals.get(expiring_record.approval_id).status is ApprovalStatus.EXPIRED
    assert not approvals.valid_for(expiring_record.approval_id, expiring)


def test_authorized_tool_provider_blocks_agent_before_backend_invocation() -> None:
    agent_id = new_id("agent")
    raw = FakeToolProvider()
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=agent_id,
                actor_types=frozenset({ActorType.AGENT}),
                allowed_actions=frozenset({AuthorizationAction.READ}),
                resource_types=frozenset({ResourceType.TOOL}),
            ),
        )
    )
    secured = AuthorizedToolProvider(raw, AuthorizationGate(provider))
    invocation = ToolInvocation(
        invocation_id="invoke-denied",
        tool_ref="tool:dangerous",
        arguments={"delete": True},
        context=OperationContext(
            correlation_id="corr-agent",
            owner_type="agent",
            owner_id=agent_id,
        ),
    )

    with pytest.raises(ContractError):
        asyncio.run(secured.invoke(invocation))
    assert raw.calls == []


def test_project_isolation_is_enforced_before_file_provider() -> None:
    project_allowed = new_id("project")
    project_other = new_id("project")
    actor = "user:alice"
    raw = FakeFileProvider()
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=actor,
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.MODIFY}),
                resource_types=frozenset({ResourceType.FILE}),
                project_ids=frozenset({project_allowed}),
            ),
        )
    )
    secured = AuthorizedFileProvider(raw, AuthorizationGate(provider))
    context = OperationContext(
        correlation_id="corr-file",
        owner_type="user",
        owner_id="alice",
        project_id=project_other,
    )

    with pytest.raises(ContractError):
        asyncio.run(secured.write("file:blocked", b"payload", context))
    assert raw.calls == []


def test_service_identity_secret_resolution_uses_policy_without_secret_value_in_audit() -> None:
    project_id = new_id("project")
    inner = LocalSecretProvider()
    reference = SecretReference(
        provider="local-secrets",
        secret_id="database-password",
        scope="project",
    )
    asyncio.run(inner.create(reference, "super-secret", purpose="database"))

    service = "service:worker"
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=service,
                actor_types=frozenset({ActorType.SERVICE}),
                allowed_actions=frozenset({AuthorizationAction.INVOKE_SENSITIVE_CAPABILITY}),
                resource_types=frozenset({ResourceType.SECRET_REFERENCE}),
                project_ids=frozenset({project_id}),
            ),
        )
    )
    gate = AuthorizationGate(provider)
    secured = AuthorizedSecretProvider(inner, gate)

    material = asyncio.run(
        secured.resolve(
            reference,
            SecretAccessContext(
                consumer_ref=service,
                project_id=project_id,
                purpose="database",
            ),
        )
    )
    assert material.reveal() == "super-secret"
    assert "super-secret" not in repr(gate.audit_records)


def test_authorization_audit_preserves_correlation_and_scope() -> None:
    project_id = new_id("project")
    actor = ActorIdentity("user:auditor", ActorType.HUMAN)
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=actor.actor_id,
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.READ}),
            ),
        )
    )
    gate = AuthorizationGate(provider)
    action = ProposedAction(
        _context(
            actor,
            action=AuthorizationAction.READ,
            resource_type=ResourceType.PROJECT,
            resource_id=project_id,
            project_id=project_id,
            correlation_id="corr-visible",
        )
    )

    asyncio.run(gate.enforce(action))
    record = gate.audit_records[-1]
    assert record.correlation_id == "corr-visible"
    assert record.project_id == project_id
    assert record.actor_ref == actor.actor_id
    assert record.outcome is AuthorizationOutcome.ALLOW


def test_capability_bridge_routes_agent_policy_and_approval_to_issue_15_gate() -> None:
    project_id = new_id("project")
    task_id = new_id("task")
    run_id = new_id("run")
    agent_id = new_id("agent")
    capability = CapabilitySpec(
        capability_id="capability.shell",
        name="Shell",
        safety=SafetyClassification.SENSITIVE,
        side_effects=SideEffectClassification.EXTERNAL,
    )
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=agent_id,
                actor_types=frozenset({ActorType.AGENT}),
                approval_actions=frozenset({AuthorizationAction.INVOKE_SENSITIVE_CAPABILITY}),
                resource_types=frozenset({ResourceType.CAPABILITY}),
                project_ids=frozenset({project_id}),
            ),
        )
    )
    gate = AuthorizationGate(provider)
    bridge = CapabilityAuthorizationBridge(gate)
    request = CapabilityInvocation(
        invocation_id="invoke-capability",
        capability_id=capability.capability_id,
        arguments={"command": "echo safe"},
        context=OperationContext(
            correlation_id="corr-cap",
            project_id=project_id,
        ),
        trace=InvocationTrace(
            correlation_id="corr-cap",
            project_id=project_id,
            task_id=task_id,
            run_id=run_id,
            agent_id=agent_id,
        ),
    )

    first = asyncio.run(bridge.policy_hook(request, capability))
    assert first is PolicyDecision.REQUIRE_APPROVAL
    pending = bridge.pending_approval(request.invocation_id)
    assert pending is not None
    gate.approvals.decide(
        pending.approval_id,
        approver_ref="user:reviewer",
        approve=True,
    )

    second = asyncio.run(bridge.policy_hook(request, capability))
    assert second is PolicyDecision.ALLOW
