from __future__ import annotations

import asyncio

from ai_multi_agent_platform.control_plane import (
    ORGANIZATION_AUDIT_COLLECTION,
    ControlPlane,
    ControlPlaneHTTP,
    HTTPRequest,
    OrganizationAuditLog,
    RequestContext,
)
from ai_multi_agent_platform.control_plane.models import ActorContext
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.organizations import (
    InMemoryOrganizationRepository,
    MembershipAuthorizationProvider,
    OrganizationService,
)
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeEventProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def _stack() -> tuple[
    ControlPlane,
    ControlPlaneHTTP,
    OrganizationService,
    FakeEventProvider,
]:
    kernel_repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=kernel_repository,
    )
    organization_repository = InMemoryOrganizationRepository()
    organizations = OrganizationService(organization_repository)
    authorization = MembershipAuthorizationProvider(
        FakeAuthorizationProvider(),
        organization_repository,
    )
    audit_events = FakeEventProvider()
    control_plane = ControlPlane(
        kernel=kernel,
        events=kernel_repository,
        authorization=authorization,
        organization_service=organizations,
        organization_audit_events=audit_events,
    )
    return control_plane, ControlPlaneHTTP(control_plane), organizations, audit_events


def _headers(principal: str, *, key: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Request-Id": f"request-{principal}",
        "X-Correlation-Id": f"correlation-{principal}",
        "X-Principal-Ref": principal,
        "X-Owner-Type": "user",
        "X-Owner-Id": principal.removeprefix("user:"),
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


async def _command(
    http: ControlPlaneHTTP,
    command: str,
    resource_ref: str,
    principal: str,
    key: str,
    **payload: object,
):
    body = {"resource_ref": resource_ref, **payload}
    return await http.handle(
        HTTPRequest(
            method="POST",
            path=f"/api/v1/commands/{command}",
            headers=_headers(principal, key=key),
            body=body,
        )
    )


def test_current_organization_composition_preserves_runtime_and_registers_audit() -> None:
    async def scenario() -> None:
        control_plane, http, _, _ = _stack()
        assert control_plane.automation_runtime is not None
        assert control_plane.organization_audit is not None
        assert ORGANIZATION_AUDIT_COLLECTION in control_plane.registered_collections

        manifest = await http.handle(HTTPRequest(method="GET", path="/api/v1"))
        assert manifest.status == 200
        assert isinstance(manifest.body, dict)
        resources = manifest.body["resources"]
        assert isinstance(resources, list)
        assert ORGANIZATION_AUDIT_COLLECTION in resources

    asyncio.run(scenario())


def test_membership_mutations_project_actor_attributed_scope_aware_history() -> None:
    async def scenario() -> None:
        control_plane, http, _, audit_events = _stack()
        created = await _command(
            http,
            "organization.create",
            "organizations",
            "user:owner",
            "audit-org-create",
            name="Audit Org",
        )
        assert created.status == 200
        assert isinstance(created.body, dict)
        organization_id = created.body["id"]
        assert isinstance(organization_id, str)

        team = await _command(
            http,
            "team.create",
            organization_id,
            "user:owner",
            "audit-team-create",
            name="Platform",
        )
        assert team.status == 200
        assert isinstance(team.body, dict)
        team_id = team.body["id"]
        assert isinstance(team_id, str)

        membership = await _command(
            http,
            "membership.add",
            organization_id,
            "user:owner",
            "audit-member-add",
            actor_id="user:member",
            actor_type="human",
            team_id=team_id,
            role_refs=["role:member"],
            policy_refs=["policy:read"],
        )
        assert membership.status == 200
        assert isinstance(membership.body, dict)
        membership_id = membership.body["id"]
        assert isinstance(membership_id, str)

        assigned = await _command(
            http,
            "membership.assign",
            membership_id,
            "user:owner",
            "audit-member-assign",
            role_refs=["role:reviewer"],
            policy_refs=["policy:review"],
        )
        assert assigned.status == 200

        suspended = await _command(
            http,
            "membership.suspend",
            membership_id,
            "user:owner",
            "audit-member-suspend",
        )
        assert suspended.status == 200
        removed = await _command(
            http,
            "membership.remove",
            membership_id,
            "user:owner",
            "audit-member-remove",
        )
        assert removed.status == 200

        audit = control_plane.organization_audit
        assert audit is not None
        history = await audit.read_organization_history(organization_id)
        assert {
            "organization.create",
            "team.create",
            "membership.add",
            "membership.assign",
            "membership.suspend",
            "membership.remove",
        }.issubset({event.event_type for event in history})
        suspend_event = next(event for event in history if event.event_type == "membership.suspend")
        assert suspend_event.provenance is not None
        assert suspend_event.provenance.actor_ref == "user:owner"
        assert suspend_event.payload["affected_actor_id"] == "user:member"
        assert suspend_event.payload["status"] == "suspended"
        assert suspend_event.correlation_id == organization_id

        owner_history = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/{ORGANIZATION_AUDIT_COLLECTION}",
                headers=_headers("user:owner"),
                query={"filter[organization_id]": organization_id},
            )
        )
        assert owner_history.status == 200
        assert isinstance(owner_history.body, dict)
        assert owner_history.body["total"] >= 6
        assert "token_ref" not in repr(owner_history.body)

        outsider_history = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/{ORGANIZATION_AUDIT_COLLECTION}",
                headers=_headers("user:outsider"),
                query={"filter[organization_id]": organization_id},
            )
        )
        assert outsider_history.status == 200
        assert isinstance(outsider_history.body, dict)
        assert outsider_history.body["total"] == 0

        hidden_exact = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/{ORGANIZATION_AUDIT_COLLECTION}/{suspend_event.id}",
                headers=_headers("user:outsider"),
            )
        )
        assert hidden_exact.status == 404
        assert len(await audit_events.read(organization_id)) == len(history)

    asyncio.run(scenario())


def test_audit_event_identity_is_deterministic_for_logical_command_replay() -> None:
    async def scenario() -> None:
        events = FakeEventProvider()
        audit = OrganizationAuditLog(events)
        context = RequestContext(
            request_id="request-audit-idempotency",
            correlation_id="correlation-audit-idempotency",
            actor=ActorContext(principal_ref="user:owner"),
            idempotency_key="same-logical-command",
        )
        result = {
            "id": "org_11111111-1111-1111-1111-111111111111",
            "type": "organization",
            "status": "active",
        }
        first = await audit.record_command(
            context,
            "organization.create",
            "organizations",
            result,
        )
        second = await audit.record_command(
            context,
            "organization.create",
            "organizations",
            result,
        )
        assert first is not None
        assert second is not None
        assert first.id == second.id
        stored = await events.read("org_11111111-1111-1111-1111-111111111111")
        assert len(stored) == 1

    asyncio.run(scenario())
