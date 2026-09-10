"""Platform-owned application build/release orchestration."""

from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data import DataAccessContext, FileProvider
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.security import (
    ActorIdentity,
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationGate,
    ProposedAction,
    ResourceType,
    RiskClassification,
)
from ai_multi_agent_platform.workspaces import (
    RunWorkspaceBinding,
    RunWorkspaceBindingRepository,
    WorkspaceProvider,
)

from .contracts import (
    ApplicationReleasePublisher,
    ApplicationReleaseRepository,
    BuildTargetMatcher,
    PublicationResult,
    PublishContext,
)
from .manifest import manifest_sha256, release_manifest
from .models import (
    ApplicationArtifact,
    ApplicationRelease,
    BuildSpecification,
    BuildTargetState,
    BuildTargetStatus,
    GateEvidence,
    GateStatus,
    ReleaseChannel,
    ReleaseStatus,
    ReleaseVisibility,
    utc_now,
)

_SOURCE = "application-distribution"
_OWNER_ID = "application-distribution"


class ApplicationDistributionService:
    """Own application release truth while execution/storage/publishers remain replaceable."""

    def __init__(
        self,
        repository: ApplicationReleaseRepository,
        *,
        kernel: PlatformKernel,
        files: FileProvider,
        workspaces: WorkspaceProvider | None = None,
        run_workspace_bindings: RunWorkspaceBindingRepository | None = None,
        authorization_gate: AuthorizationGate | None = None,
        target_matcher: BuildTargetMatcher | None = None,
        publishers: tuple[ApplicationReleasePublisher, ...] = (),
    ) -> None:
        self.repository = repository
        self.kernel = kernel
        self.files = files
        self.workspaces = workspaces
        self.run_workspace_bindings = run_workspace_bindings
        self.authorization_gate = authorization_gate
        self.target_matcher = target_matcher
        self._publishers: dict[str, ApplicationReleasePublisher] = {}
        for publisher in publishers:
            self.register_publisher(publisher)

    def register_publisher(self, publisher: ApplicationReleasePublisher) -> None:
        provider_id = publisher.provider_id
        if not provider_id.strip():
            raise ValueError("application release publisher id must not be blank")
        existing = self._publishers.get(provider_id)
        if existing is not None and existing is not publisher:
            raise ValueError(f"duplicate application release publisher: {provider_id}")
        self._publishers[provider_id] = publisher

    async def create_release(
        self,
        *,
        application_id: str,
        display_name: str,
        version: str,
        channel: ReleaseChannel,
        visibility: ReleaseVisibility,
        project_id: str,
        workspace_id: str,
        source_revision: str,
        build_specification: BuildSpecification,
        creator_ref: str,
        workspace_snapshot_id: str | None = None,
        release_notes: str | None = None,
        previous_release_id: str | None = None,
    ) -> ApplicationRelease:
        workspaces = self._require_workspaces()
        workspace = await workspaces.get_workspace(workspace_id)
        if workspace.project_id != project_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "application release workspace must belong to the selected project",
            )
        resolved_snapshot_id = workspace_snapshot_id or workspace.base_snapshot_id
        if resolved_snapshot_id is None:
            raise ContractError(
                ErrorCode.CONFLICT,
                "application release requires an immutable workspace snapshot",
            )
        snapshot = await workspaces.get_snapshot(resolved_snapshot_id)
        if snapshot.workspace_id != workspace.id:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "application release workspace snapshot belongs to another workspace",
            )
        if snapshot.source_revision is not None and snapshot.source_revision != source_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "application release source revision does not match its workspace snapshot",
            )

        existing = await self.repository.find_version(application_id, version, channel.value)
        if existing is not None:
            if (
                existing.project_id == project_id
                and existing.workspace_id == workspace_id
                and existing.workspace_snapshot_id == snapshot.id
                and existing.workspace_content_checksum == snapshot.content_checksum
                and existing.source_revision == source_revision
                and existing.build_specification == build_specification
                and existing.visibility is visibility
            ):
                return existing
            raise ContractError(
                ErrorCode.CONFLICT,
                "application release version already exists with different provenance",
                details={"release_id": existing.release_id},
            )
        release = ApplicationRelease(
            application_id=application_id,
            display_name=display_name,
            version=version,
            channel=channel,
            visibility=visibility,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_snapshot_id=snapshot.id,
            workspace_content_checksum=snapshot.content_checksum,
            source_revision=source_revision,
            build_specification=build_specification,
            creator_ref=creator_ref,
            targets=tuple(BuildTargetState(target) for target in build_specification.targets),
            release_notes=release_notes,
            previous_release_id=previous_release_id,
        )
        return await self.repository.save(release, expected_revision=0)

    async def request_build(
        self,
        release_id: str,
        *,
        target_id: str,
        idempotency_key: str,
        actor_ref: str,
    ) -> ApplicationRelease:
        release = await self.repository.get(release_id)
        state = self._target(release, target_id)
        if state.task_id is not None:
            return release
        self._require_mutable(release)
        target = state.target
        if self.target_matcher is not None and not await self.target_matcher.supports(
            release.build_specification,
            target,
        ):
            targets = tuple(
                replace(
                    item,
                    status=BuildTargetStatus.UNSUPPORTED,
                    failure_reason="no eligible execution host for build target requirements",
                )
                if item.target.target_id == target_id
                else item
                for item in release.targets
            )
            updated = replace(
                release,
                status=self._build_status(targets),
                targets=targets,
                revision=release.revision + 1,
            )
            return await self.repository.save(updated, expected_revision=release.revision)

        bindings = self._require_run_workspace_bindings()
        objective = (
            f"Build application release {release.application_id} {release.version} for "
            f"{target.target_id}; source={release.source_revision}; "
            f"build_spec={release.build_specification.spec_id}@"
            f"{release.build_specification.revision}; workspace={release.workspace_id}; "
            f"snapshot={release.workspace_snapshot_id}; output={target.output_path}"
        )
        task_id = new_id("task")
        task = await self.kernel.create_task(
            idempotency_key=f"application-release:{idempotency_key}:create-task:{target_id}",
            task_id=task_id,
            title=f"Build {release.display_name} {release.version} ({target_id})",
            objective=objective,
            owner_type="service",
            owner_id=_OWNER_ID,
            project_id=release.project_id,
            actor_ref=actor_ref,
            source=_SOURCE,
        )
        task_id = task.task_id
        await self.kernel.ready_task(
            idempotency_key=f"application-release:{idempotency_key}:ready-task:{target_id}",
            task_id=task_id,
            actor_ref=actor_ref,
            source=_SOURCE,
        )
        task_state = await self.kernel.get_task(task_id)
        if task_state.plan_ref is None:
            await self.kernel.plan_task(
                idempotency_key=f"application-release:{idempotency_key}:plan-task:{target_id}",
                task_id=task_id,
                actor_ref=actor_ref,
                source=_SOURCE,
            )
        run = await self.kernel.create_run(
            idempotency_key=f"application-release:{idempotency_key}:create-run:{target_id}",
            task_id=task_id,
            actor_ref=actor_ref,
            source=_SOURCE,
        )
        await bindings.bind(
            RunWorkspaceBinding(
                run_id=run.run_id,
                task_id=task_id,
                workspace_id=release.workspace_id,
                workspace_snapshot_id=release.workspace_snapshot_id,
                content_checksum=release.workspace_content_checksum,
            )
        )
        started = await self.kernel.start_run(
            idempotency_key=f"application-release:{idempotency_key}:start-run:{target_id}",
            task_id=task_id,
            run_id=run.run_id,
            actor_ref=actor_ref,
            source=_SOURCE,
        )
        updated_targets = tuple(
            replace(
                item,
                status=BuildTargetStatus.RUNNING,
                task_id=task_id,
                run_id=started.run_id,
            )
            if item.target.target_id == target_id
            else item
            for item in release.targets
        )
        updated = replace(
            release,
            status=ReleaseStatus.BUILDING,
            targets=updated_targets,
            revision=release.revision + 1,
        )
        return await self.repository.save(updated, expected_revision=release.revision)

    async def record_build_artifact(
        self,
        release_id: str,
        *,
        target_id: str,
        artifact_id: str,
        file_id: str,
        filename: str,
        media_type: str,
        build_run_id: str,
        context: DataAccessContext,
        evidence_refs: tuple[str, ...] = (),
    ) -> ApplicationRelease:
        release = await self.repository.get(release_id)
        self._require_mutable(release)
        target_state = self._target(release, target_id)
        if target_state.task_id is None:
            raise ContractError(ErrorCode.CONFLICT, "build target has no canonical Task")
        if target_state.run_id is not None and target_state.run_id != build_run_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "build artifact does not belong to the target's canonical Run",
            )
        run = await self.kernel.get_run(target_state.task_id, build_run_id)
        if run.status.value != "succeeded":
            raise ContractError(
                ErrorCode.CONFLICT,
                "build artifact requires a succeeded canonical Run",
            )
        if artifact_id not in run.artifact_ids:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "build artifact is not attached to the canonical Run",
            )
        binding = await self._require_run_workspace_bindings().get(build_run_id)
        if binding is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "build Run has no canonical workspace snapshot binding",
            )
        if (
            binding.task_id != target_state.task_id
            or binding.workspace_id != release.workspace_id
            or binding.workspace_snapshot_id != release.workspace_snapshot_id
            or binding.content_checksum != release.workspace_content_checksum
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "build Run workspace provenance does not match the application release",
            )
        if (
            context.operation.project_id is not None
            and context.operation.project_id != release.project_id
        ):
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "build artifact data context is outside the application release project",
            )
        file_record = await self.files.get_file(file_id, context)
        if artifact_id not in file_record.artifact_ids:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "canonical File is not linked to the build Artifact",
            )
        if not await self.files.verify_checksum(file_id, context):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "build File checksum verification failed",
            )
        artifact = ApplicationArtifact(
            artifact_id=artifact_id,
            file_id=file_id,
            target_id=target_id,
            filename=filename,
            package_type=target_state.target.package_type,
            media_type=media_type,
            sha256=file_record.sha256,
            build_task_id=target_state.task_id,
            build_run_id=build_run_id,
            evidence_refs=evidence_refs,
        )
        artifacts = tuple(
            item for item in release.artifacts if item.target_id != target_id
        ) + (artifact,)
        targets = tuple(
            replace(item, status=BuildTargetStatus.SUCCEEDED, run_id=build_run_id)
            if item.target.target_id == target_id
            else item
            for item in release.targets
        )
        updated = replace(
            release,
            artifacts=artifacts,
            targets=targets,
            status=self._build_status(targets),
            revision=release.revision + 1,
        )
        return await self.repository.save(updated, expected_revision=release.revision)

    async def record_gate(
        self,
        release_id: str,
        gate: GateEvidence,
    ) -> ApplicationRelease:
        release = await self.repository.get(release_id)
        self._require_mutable(release)
        gates = tuple(item for item in release.gates if item.name != gate.name) + (gate,)
        updated = replace(release, gates=gates, revision=release.revision + 1)
        return await self.repository.save(updated, expected_revision=release.revision)

    async def preview_publication(
        self,
        release_id: str,
        *,
        publisher_id: str,
        context: PublishContext,
    ) -> dict[str, JsonValue]:
        release = await self.repository.get(release_id)
        self._require_publishable(release)
        publisher = self._publisher(publisher_id)
        scoped = self._scoped_publish_context(release, context)
        manifest = release_manifest(release)
        provider_preview = await publisher.preview(release, manifest, scoped)
        return {
            "release_id": release.release_id,
            "provider_id": publisher.provider_id,
            "visibility": release.visibility.value,
            "manifest": manifest,
            "manifest_sha256": manifest_sha256(release),
            "provider_preview": provider_preview,
        }

    async def publish(
        self,
        release_id: str,
        *,
        publisher_id: str,
        context: PublishContext,
    ) -> ApplicationRelease:
        release = await self.repository.get(release_id)
        if release.status is ReleaseStatus.PUBLISHED:
            if release.publisher_id == publisher_id:
                return release
            raise ContractError(
                ErrorCode.CONFLICT,
                "release is already published by another provider",
            )
        self._require_publishable(release)
        publisher = self._publisher(publisher_id)
        scoped = self._scoped_publish_context(release, context)
        await self._authorize_publication(release, publisher, scoped)
        manifest = release_manifest(release)
        result = await publisher.publish(release, manifest, scoped)
        if result.provider_id != publisher.provider_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "publisher result provider id does not match the selected publisher",
            )
        return await self._apply_publication(release, result)

    async def _authorize_publication(
        self,
        release: ApplicationRelease,
        publisher: ApplicationReleasePublisher,
        context: PublishContext,
    ) -> None:
        if self.authorization_gate is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "application release publication requires the canonical authorization gate",
            )
        action = ProposedAction(
            AuthorizationContext(
                actor=context.actor,
                action=AuthorizationAction.EXECUTE,
                resource_type=ResourceType.GENERIC,
                resource_id=release.release_id,
                operation=context.operation,
                workspace_id=release.workspace_id,
                capability_ref=publisher.provider_id,
                side_effect="application_release_publish",
            ),
            payload={
                "publisher_id": publisher.provider_id,
                "application_id": release.application_id,
                "version": release.version,
                "channel": release.channel.value,
                "visibility": release.visibility.value,
                "source_revision": release.source_revision,
                "workspace_snapshot_id": release.workspace_snapshot_id,
                "manifest_sha256": manifest_sha256(release),
            },
        )
        await self.authorization_gate.enforce(
            action,
            approval_id=context.approval_id,
            risk=RiskClassification.HIGH,
        )

    def _scoped_publish_context(
        self,
        release: ApplicationRelease,
        context: PublishContext,
    ) -> PublishContext:
        supplied = context.operation
        if supplied.project_id is not None and supplied.project_id != release.project_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "publication context is outside the application release project",
            )
        operation = OperationContext(
            correlation_id=supplied.correlation_id,
            causation_id=supplied.causation_id,
            owner_type=supplied.owner_type,
            owner_id=supplied.owner_id,
            project_id=release.project_id,
            control=supplied.control,
        )
        return PublishContext(
            actor=context.actor,
            operation=operation,
            approval_id=context.approval_id,
        )

    async def _apply_publication(
        self,
        release: ApplicationRelease,
        result: PublicationResult,
    ) -> ApplicationRelease:
        if result.visibility is not release.visibility:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "publisher changed the requested release visibility",
            )
        by_artifact = {item.artifact_id: item for item in result.artifacts}
        expected = {item.artifact_id for item in release.artifacts}
        if set(by_artifact) != expected:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "publisher result does not cover the canonical release artifacts exactly",
            )
        artifacts = tuple(
            replace(
                item,
                download_url=by_artifact[item.artifact_id].download_url,
                external_metadata=by_artifact[item.artifact_id].external_metadata,
            )
            for item in release.artifacts
        )
        updated = replace(
            release,
            status=ReleaseStatus.PUBLISHED,
            publisher_id=result.provider_id,
            release_url=result.release_url,
            latest_url=result.latest_url,
            external_metadata=result.external_metadata,
            artifacts=artifacts,
            published_at=utc_now(),
            revision=release.revision + 1,
        )
        return await self.repository.save(updated, expected_revision=release.revision)

    def _publisher(self, publisher_id: str) -> ApplicationReleasePublisher:
        try:
            return self._publishers[publisher_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"application release publisher is not registered: {publisher_id}",
            ) from exc

    def _require_workspaces(self) -> WorkspaceProvider:
        if self.workspaces is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "application distribution requires the canonical WorkspaceProvider",
            )
        return self.workspaces

    def _require_run_workspace_bindings(self) -> RunWorkspaceBindingRepository:
        if self.run_workspace_bindings is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "application distribution requires canonical Run workspace bindings",
            )
        return self.run_workspace_bindings

    @staticmethod
    def _target(release: ApplicationRelease, target_id: str) -> BuildTargetState:
        for target in release.targets:
            if target.target.target_id == target_id:
                return target
        raise ContractError(ErrorCode.NOT_FOUND, f"build target not found: {target_id}")

    @staticmethod
    def _build_status(targets: tuple[BuildTargetState, ...]) -> ReleaseStatus:
        statuses = {target.status for target in targets}
        if statuses == {BuildTargetStatus.SUCCEEDED}:
            return ReleaseStatus.READY
        if BuildTargetStatus.SUCCEEDED in statuses:
            return ReleaseStatus.PARTIAL
        if BuildTargetStatus.FAILED in statuses or BuildTargetStatus.UNSUPPORTED in statuses:
            return ReleaseStatus.FAILED
        return ReleaseStatus.BUILDING

    @staticmethod
    def _require_mutable(release: ApplicationRelease) -> None:
        if release.status is ReleaseStatus.PUBLISHED:
            raise ContractError(
                ErrorCode.CONFLICT,
                "published application releases are immutable",
            )

    @staticmethod
    def _require_publishable(release: ApplicationRelease) -> None:
        if release.status is not ReleaseStatus.READY:
            raise ContractError(
                ErrorCode.CONFLICT,
                "only a fully built release can be published",
            )
        required = tuple(
            dict.fromkeys(
                (
                    *release.build_specification.pre_build_checks,
                    *release.build_specification.test_gates,
                    *release.build_specification.post_build_checks,
                )
            )
        )
        gates = {gate.name: gate for gate in release.gates}
        missing = [name for name in required if name not in gates]
        failed = [
            name
            for name in required
            if name in gates and gates[name].status is not GateStatus.PASSED
        ]
        if missing or failed:
            raise ContractError(
                ErrorCode.CONFLICT,
                "mandatory application release gates have not passed",
                details={"missing_gates": missing, "failed_gates": failed},
            )
        if len(release.artifacts) != len(release.targets):
            raise ContractError(
                ErrorCode.CONFLICT,
                "release artifacts do not cover every build target",
            )


def operation_context_for_release(
    release: ApplicationRelease,
    *,
    correlation_id: str,
    actor: ActorIdentity,
) -> OperationContext:
    return OperationContext(
        correlation_id=correlation_id,
        owner_type="user" if actor.actor_type.value == "human" else "service",
        owner_id=actor.actor_id,
        project_id=release.project_id,
    )
