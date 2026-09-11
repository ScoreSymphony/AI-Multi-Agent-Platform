"""Gate-aware application-distribution service composition for issue #750."""

from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data import FileProvider
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.security import AuthorizationGate
from ai_multi_agent_platform.workspaces import (
    RunWorkspaceBindingRepository,
    WorkspaceProvider,
)

from .contracts import (
    ApplicationReleasePublisher,
    ApplicationReleaseRepository,
    BuildTargetMatcher,
    PublishContext,
)
from .gates import (
    ApplicationReleaseGateCoordinator,
    bind_gate_to_release,
    gate_is_current,
    publication_readiness,
    required_gate_names,
)
from .models import ApplicationRelease, GateEvidence, GateStatus, ReleaseStatus
from .service import ApplicationDistributionService as _BaseApplicationDistributionService


class ApplicationDistributionService(_BaseApplicationDistributionService):
    """Application distribution with canonical release-gate reconciliation.

    The base service remains the owner of build/publication state. This subclass adds the
    narrow evidence bridge required by #750 and deliberately delegates Verification and
    Evaluation truth to their canonical services through ``ApplicationReleaseGateCoordinator``.
    """

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
        gate_coordinator: ApplicationReleaseGateCoordinator | None = None,
    ) -> None:
        super().__init__(
            repository,
            kernel=kernel,
            files=files,
            workspaces=workspaces,
            run_workspace_bindings=run_workspace_bindings,
            authorization_gate=authorization_gate,
            target_matcher=target_matcher,
            publishers=publishers,
        )
        self.gate_coordinator = gate_coordinator

    async def reconcile_gates(self, release_id: str) -> ApplicationRelease:
        release = await self.repository.get(release_id)
        if (
            release.status is ReleaseStatus.PUBLISHED
            or self.gate_coordinator is None
            or not required_gate_names(release)
        ):
            return release
        gates = await self.gate_coordinator.reconcile(release)
        if gates == release.gates:
            return release
        updated = replace(release, gates=gates, revision=release.revision + 1)
        return await self.repository.save(updated, expected_revision=release.revision)

    async def record_gate(
        self,
        release_id: str,
        gate: GateEvidence,
    ) -> ApplicationRelease:
        release = await self.repository.get(release_id)
        return await super().record_gate(release_id, bind_gate_to_release(gate, release))

    async def preview_publication(
        self,
        release_id: str,
        *,
        publisher_id: str,
        context: PublishContext,
    ) -> dict[str, JsonValue]:
        await self.reconcile_gates(release_id)
        return await super().preview_publication(
            release_id,
            publisher_id=publisher_id,
            context=context,
        )

    async def publish(
        self,
        release_id: str,
        *,
        publisher_id: str,
        context: PublishContext,
    ) -> ApplicationRelease:
        await self.reconcile_gates(release_id)
        return await super().publish(
            release_id,
            publisher_id=publisher_id,
            context=context,
        )

    @staticmethod
    def _require_publishable(release: ApplicationRelease) -> None:
        _BaseApplicationDistributionService._require_publishable(release)
        gates = {gate.name: gate for gate in release.gates}
        stale = [
            name
            for name in required_gate_names(release)
            if name not in gates or not gate_is_current(gates[name], release)
        ]
        if stale:
            stale_details: dict[str, JsonValue] = {"stale_gates": [*stale]}
            raise ContractError(
                ErrorCode.CONFLICT,
                "mandatory application release gate evidence is stale for the current subject",
                details=stale_details,
            )
        nonpassing = [
            name
            for name in required_gate_names(release)
            if gates[name].status is not GateStatus.PASSED
        ]
        if nonpassing:
            blocking_details: dict[str, JsonValue] = {"blocking_gates": [*nonpassing]}
            raise ContractError(
                ErrorCode.CONFLICT,
                "mandatory application release gates are not conclusively passing",
                details=blocking_details,
            )

    @staticmethod
    def publication_readiness(release: ApplicationRelease) -> dict[str, JsonValue]:
        return publication_readiness(release)
