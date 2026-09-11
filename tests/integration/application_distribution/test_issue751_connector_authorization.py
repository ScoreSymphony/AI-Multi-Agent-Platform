from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.application_distribution import (
    ApplicationArtifact,
    ApplicationDistributionService,
    ApplicationRelease,
    BuildSpecification,
    BuildTarget,
    BuildTargetState,
    BuildTargetStatus,
    GitHubReleasePublisher,
    InMemoryApplicationReleaseRepository,
    PackageType,
    PublishContext,
    ReleaseChannel,
    ReleaseStatus,
    ReleaseVisibility,
)
from ai_multi_agent_platform.connectors import (
    GITHUB_RELEASE_CREATE_ACTION,
    REFERENCE_CONNECTOR_TYPE,
    REFERENCE_CONNECTOR_VERSION,
    Connection,
    ConnectorRegistry,
    ConnectorService,
    InMemoryConnectorRepository,
    ReferenceConnectorProvider,
)
from ai_multi_agent_platform.contracts import (
    AuthorizationDecision,
    AuthorizationOutcome,
    ContractError,
    ErrorCode,
    OperationContext,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import ActorIdentity, ActorType, AuthorizationGate
from ai_multi_agent_platform.testing import FakeAuthorizationProvider


class _ConnectorDenyPolicy(FakeAuthorizationProvider):
    async def authorize(self, request):
        self.calls.append(request)
        if request.side_effect == "external_action":
            return AuthorizationDecision(
                AuthorizationOutcome.DENY,
                reason="issue751 connector publication denied",
                policy_id="issue751-connector-policy",
            )
        return AuthorizationDecision(
            AuthorizationOutcome.ALLOW, reason="issue751 connector allowed"
        )


def _ready_release() -> ApplicationRelease:
    target = BuildTarget(
        target_id="linux-x64",
        os_name="linux",
        architecture="x86_64",
        package_type=PackageType.ARCHIVE,
        output_path="dist/example-app.tar.gz",
    )
    task_id = new_id("task")
    run_id = new_id("run")
    return ApplicationRelease(
        application_id="example-app",
        display_name="Example App",
        version="1.0.0",
        channel=ReleaseChannel.STABLE,
        visibility=ReleaseVisibility.PUBLIC,
        project_id=new_id("project"),
        workspace_id=new_id("workspace"),
        workspace_snapshot_id=new_id("workspace_snapshot"),
        workspace_content_checksum="9" * 64,
        source_revision="7" * 40,
        build_specification=BuildSpecification(
            command=("python", "-m", "build"),
            targets=(target,),
        ),
        creator_ref="user:tester",
        status=ReleaseStatus.READY,
        targets=(
            BuildTargetState(
                target=target,
                status=BuildTargetStatus.SUCCEEDED,
                task_id=task_id,
                run_id=run_id,
            ),
        ),
        artifacts=(
            ApplicationArtifact(
                artifact_id=new_id("artifact"),
                file_id=new_id("file"),
                target_id=target.target_id,
                filename="example-app.tar.gz",
                package_type=target.package_type,
                media_type="application/gzip",
                sha256="8" * 64,
                build_task_id=task_id,
                build_run_id=run_id,
            ),
        ),
    )


def test_connector_authorization_remains_mandatory_after_application_publication_allow() -> None:
    async def scenario() -> None:
        release = _ready_release()
        releases = InMemoryApplicationReleaseRepository()
        await releases.save(release, expected_revision=0)

        connector_repository = InMemoryConnectorRepository()
        connector_policy = _ConnectorDenyPolicy()
        connectors = ConnectorService(
            connector_repository,
            ConnectorRegistry(),
            authorization_gate=AuthorizationGate(connector_policy),
        )
        await connectors.register_provider(ReferenceConnectorProvider())
        connection = Connection(
            id=new_id("connection"),
            connector_type_id=REFERENCE_CONNECTOR_TYPE,
            connector_version=REFERENCE_CONNECTOR_VERSION,
            owner_type="user",
            owner_id="tester",
            display_name="Issue 751 connector authorization fixture",
            project_id=release.project_id,
        )
        await connector_repository.save_connection(connection)

        application_policy = FakeAuthorizationProvider(allowed=True)
        service = ApplicationDistributionService(
            releases,
            kernel=object(),  # type: ignore[arg-type] - publish path does not use execution
            files=object(),  # type: ignore[arg-type] - publish path uses canonical release artifacts
            authorization_gate=AuthorizationGate(application_policy),
            publishers=(GitHubReleasePublisher(connectors),),
        )
        context = PublishContext(
            actor=ActorIdentity("user:tester", ActorType.HUMAN),
            operation=OperationContext(
                correlation_id="issue751-composed-authorization",
                owner_type="user",
                owner_id="tester",
                project_id=release.project_id,
            ),
            configuration={
                "connection_id": connection.id,
                "repository_ref": "ScoreSymphony/example-app",
            },
        )

        with pytest.raises(ContractError) as caught:
            await service.publish(
                release.release_id,
                publisher_id=GitHubReleasePublisher.provider_id,
                context=context,
            )

        assert caught.value.code is ErrorCode.FORBIDDEN
        assert any(
            call.side_effect == "application_release_publish" for call in application_policy.calls
        )
        external_calls = [
            call for call in connector_policy.calls if call.side_effect == "external_action"
        ]
        assert len(external_calls) == 1
        assert external_calls[0].capability_ref == GITHUB_RELEASE_CREATE_ACTION

        current = await releases.get(release.release_id)
        assert current.status is ReleaseStatus.READY
        assert current.publisher_id is None
        assert current.release_url is None
        assert current.artifacts[0].download_url is None

    asyncio.run(scenario())
