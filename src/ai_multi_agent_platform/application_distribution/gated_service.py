"""Gate-aware application-distribution service composition for issue #750."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .contracts import PublishContext
from .gates import (
    ApplicationReleaseGateCoordinator,
    bind_gate_to_release,
    gate_is_current,
    publication_readiness,
    required_gate_names,
)
from .models import ApplicationRelease, GateEvidence, GateStatus
from .service import ApplicationDistributionService as _BaseApplicationDistributionService


class ApplicationDistributionService(_BaseApplicationDistributionService):
    """Application distribution with canonical release-gate reconciliation.

    The base service remains the owner of build/publication state.  This subclass adds the
    narrow evidence bridge required by #750 and deliberately delegates Verification and
    Evaluation truth to their canonical services through ``ApplicationReleaseGateCoordinator``.
    """

    def __init__(self, *args: object, gate_coordinator: ApplicationReleaseGateCoordinator | None = None, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.gate_coordinator = gate_coordinator

    async def reconcile_gates(self, release_id: str) -> ApplicationRelease:
        release = await self.repository.get(release_id)
        if self.gate_coordinator is None or not required_gate_names(release):
            return release
        gates = await self.gate_coordinator.reconcile(release)
        if gates == release.gates:
            return release
        from dataclasses import replace

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
        stale = [
            name
            for name in required_gate_names(release)
            if (gate := next((item for item in release.gates if item.name == name), None)) is None
            or not gate_is_current(gate, release)
        ]
        if stale:
            raise ContractError(
                ErrorCode.CONFLICT,
                "mandatory application release gate evidence is stale for the current subject",
                details={"stale_gates": stale},
            )
        nonpassing = [
            gate.name
            for gate in release.gates
            if gate.name in required_gate_names(release) and gate.status is not GateStatus.PASSED
        ]
        if nonpassing:
            raise ContractError(
                ErrorCode.CONFLICT,
                "mandatory application release gates are not conclusively passing",
                details={"blocking_gates": nonpassing},
            )

    @staticmethod
    def publication_readiness(release: ApplicationRelease) -> dict[str, JsonValue]:
        return publication_readiness(release)
