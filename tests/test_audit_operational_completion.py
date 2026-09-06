from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.contracts import AuthorizationOutcome, HealthStatus, OperationContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ProposedAction,
    ResourceType,
    SqliteApprovalService,
    SqliteAuthorizationAuditSink,
)


def _approval_action(agent_id: str) -> ProposedAction:
    return ProposedAction(
        AuthorizationContext(
            actor=ActorIdentity(agent_id, ActorType.AGENT),
            action=AuthorizationAction.EXECUTE,
            resource_type=ResourceType.RUN,
            resource_id=new_id("run"),
            operation=OperationContext(
                correlation_id="corr-durable-approval",
                owner_type="agent",
                owner_id=agent_id,
            ),
        ),
        payload={"operation": "sensitive"},
    )


def test_sqlite_approval_and_audit_survive_gate_restart(tmp_path: Path) -> None:
    agent_id = new_id("agent")
    action = _approval_action(agent_id)
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=agent_id,
                actor_types=frozenset({ActorType.AGENT}),
                approval_actions=frozenset({AuthorizationAction.EXECUTE}),
                resource_types=frozenset({ResourceType.RUN}),
            ),
            LocalPrincipalPolicy(
                principal_ref="user:reviewer",
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.APPROVE}),
                resource_types=frozenset({ResourceType.RUN}),
            ),
        )
    )
    approvals_path = tmp_path / "approvals.sqlite3"
    audit_path = tmp_path / "authorization-audit.sqlite3"

    first_gate = AuthorizationGate(
        provider,
        approvals=SqliteApprovalService(approvals_path),
        audit_sink=SqliteAuthorizationAuditSink(audit_path),
    )
    first = asyncio.run(first_gate.decide(action))
    assert first.outcome is AuthorizationOutcome.REQUIRE_APPROVAL
    approval_id = first.constraints["approval_id"]
    assert isinstance(approval_id, str)

    second_gate = AuthorizationGate(
        provider,
        approvals=SqliteApprovalService(approvals_path),
        audit_sink=SqliteAuthorizationAuditSink(audit_path),
    )
    pending = second_gate.approvals.get(approval_id)
    assert pending.requested_action_digest == action.digest
    asyncio.run(
        second_gate.decide_approval(
            approval_id,
            approver=ActorIdentity("user:reviewer", ActorType.HUMAN),
            approve=True,
            operation=OperationContext(
                correlation_id="corr-review",
                owner_type="user",
                owner_id="reviewer",
            ),
        )
    )

    third_gate = AuthorizationGate(
        provider,
        approvals=SqliteApprovalService(approvals_path),
        audit_sink=SqliteAuthorizationAuditSink(audit_path),
    )
    resumed = asyncio.run(third_gate.decide(action))
    assert resumed.outcome is AuthorizationOutcome.ALLOW
    assert len(SqliteAuthorizationAuditSink(audit_path).all()) >= 3


def test_single_node_installs_security_observability_and_health(tmp_path: Path) -> None:
    deployment = build_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "single-node", secure_cookie=False)
    )

    assert isinstance(deployment.approval_gate.approvals, SqliteApprovalService)
    assert deployment.authorization_audit.database_path.exists()
    assert deployment.observability_exporter is not None
    assert asyncio.run(deployment.health_provider.health()) is HealthStatus.HEALTHY

    deployment.bootstrap_admin("admin", "correct horse battery staple")
    result = asyncio.run(deployment.run_reference_smoke())
    timeline = deployment.observability_exporter.query_timeline(task_id=result.task_id)

    assert timeline
    assert any(entry.event_name.startswith("executor.") for entry in timeline)
