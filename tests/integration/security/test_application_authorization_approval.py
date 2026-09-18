from __future__ import annotations

import asyncio
import sys

from ai_multi_agent_platform.applications import (
    ApplicationConfigValueType,
    ApplicationConfigurationField,
    ApplicationLifecycleService,
    ApplicationManifest,
    ApplicationRuntimeRegistry,
    ApplicationService,
    ApplicationServiceRuntime,
    InMemoryApplicationRepository,
    LocalProcessApplicationRuntime,
    register_application_control_plane,
)
from ai_multi_agent_platform.applications.serialization import application_manifest_to_document
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    AuthorizationOutcome,
    ControlPlaneAuthorizationBridge,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

_REQUESTER = "user:application-operator"
_REVIEWER = "user:application-reviewer"


def _manifest() -> ApplicationManifest:
    return ApplicationManifest(
        application_id=new_id("application"),
        name="Approval fixture",
        version="1.0.0",
        description="Application approval and audit fixture",
        services=(
            ApplicationService(
                service_id="app",
                runtime=ApplicationServiceRuntime.PROCESS,
                process=(sys.executable, "-c", "pass"),
            ),
        ),
        configuration=(
            ApplicationConfigurationField(
                name="label",
                value_type=ApplicationConfigValueType.STRING,
                default="initial",
                mutable=True,
            ),
        ),
        runtime_requirements=("local", "process"),
    )


def _stack() -> tuple[
    ControlPlaneHTTP,
    AuthorizationGate,
    InMemoryApplicationRepository,
]:
    events = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=events,
    )
    policy = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=_REQUESTER,
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.CREATE}),
                approval_actions=frozenset({AuthorizationAction.MODIFY}),
                resource_types=frozenset({ResourceType.APPLICATION}),
            ),
            LocalPrincipalPolicy(
                principal_ref=_REVIEWER,
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.APPROVE}),
                resource_types=frozenset({ResourceType.APPLICATION}),
            ),
        )
    )
    gate = AuthorizationGate(policy)
    control_plane = ControlPlane(
        kernel=kernel,
        events=events,
        authorization=ControlPlaneAuthorizationBridge(gate),
    )
    repository = InMemoryApplicationRepository()
    runtime = LocalProcessApplicationRuntime()
    lifecycle = ApplicationLifecycleService(
        repository,
        ApplicationRuntimeRegistry((runtime,)),
    )
    register_application_control_plane(control_plane, lifecycle, repository)
    return ControlPlaneHTTP(control_plane), gate, repository


def _headers(*, key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Request-Id": f"request-{key}",
        "X-Correlation-Id": f"correlation-{key}",
        "X-Principal-Ref": _REQUESTER,
        "X-Owner-Type": "user",
        "X-Owner-Id": "application-operator",
        "Idempotency-Key": key,
    }


async def _command(
    http: ControlPlaneHTTP,
    command: str,
    resource_ref: str,
    *,
    key: str,
    payload: dict[str, object],
):
    return await http.handle(
        HTTPRequest(
            method="POST",
            path=f"/api/v1/commands/{command}",
            headers=_headers(key=key),
            body={"resource_ref": resource_ref, **payload},
        )
    )


def _approval_id(response) -> str:
    assert response.status == 403
    assert isinstance(response.body, dict)
    details = response.body["details"]
    assert isinstance(details, dict)
    assert details["authorization_outcome"] == "require_approval"
    approval_id = details["approval_id"]
    assert isinstance(approval_id, str)
    return approval_id


def test_application_configuration_approval_is_payload_bound_and_audited() -> None:
    http, gate, repository = _stack()
    manifest = _manifest()

    async def scenario() -> None:
        installed = await _command(
            http,
            "application.install",
            manifest.application_id,
            key="application-install",
            payload={
                "manifest": application_manifest_to_document(manifest),
                "runtime_id": "local.process",
            },
        )
        assert installed.status == 200, installed.body
        assert isinstance(installed.body, dict)
        instance_id = installed.body["id"]
        assert isinstance(instance_id, str)

        approved_payload = {"configuration": {"label": "approved"}}
        pending = await _command(
            http,
            "application.configure",
            instance_id,
            key="application-configure-pending",
            payload=approved_payload,
        )
        approval_id = _approval_id(pending)

        await gate.decide_approval(
            approval_id,
            approver=ActorIdentity(_REVIEWER, ActorType.HUMAN),
            approve=True,
            operation=OperationContext(
                correlation_id="correlation-application-review",
                owner_type="user",
                owner_id="application-reviewer",
            ),
        )

        changed = await _command(
            http,
            "application.configure",
            instance_id,
            key="application-configure-changed",
            payload={"configuration": {"label": "changed"}},
        )
        changed_approval_id = _approval_id(changed)
        assert changed_approval_id != approval_id
        assert repository.get_instance(instance_id).configuration["label"] == "initial"

        applied = await _command(
            http,
            "application.configure",
            instance_id,
            key="application-configure-approved",
            payload=approved_payload,
        )
        assert applied.status == 200, applied.body
        assert isinstance(applied.body, dict)
        assert applied.body["configuration"] == {"label": "approved"}
        assert repository.get_instance(instance_id).configuration["label"] == "approved"

        application_records = [
            record
            for record in gate.audit_records
            if record.resource_type is ResourceType.APPLICATION
        ]
        install_record = next(
            record for record in application_records if record.action is AuthorizationAction.CREATE
        )
        assert install_record.outcome is AuthorizationOutcome.ALLOW
        assert install_record.resource_id == manifest.application_id
        assert install_record.requested_action_digest is not None

        configure_records = [
            record
            for record in application_records
            if record.action is AuthorizationAction.MODIFY and record.resource_id == instance_id
        ]
        pending_record = next(
            record
            for record in configure_records
            if record.outcome is AuthorizationOutcome.REQUIRE_APPROVAL
            and record.approval_id == approval_id
        )
        changed_record = next(
            record
            for record in configure_records
            if record.outcome is AuthorizationOutcome.REQUIRE_APPROVAL
            and record.approval_id == changed_approval_id
        )
        applied_record = next(
            record
            for record in configure_records
            if record.outcome is AuthorizationOutcome.ALLOW and record.approval_id == approval_id
        )
        assert pending_record.requested_action_digest is not None
        assert pending_record.requested_action_digest == applied_record.requested_action_digest
        assert changed_record.requested_action_digest != pending_record.requested_action_digest

    asyncio.run(scenario())
