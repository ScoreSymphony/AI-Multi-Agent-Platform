"""Reference Agent runtime and orchestrator mapping boundary for issue #33."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol, cast

from ai_multi_agent_platform.capabilities import (
    CapabilityCompatibilityRequest,
    CapabilityRegistry,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.models import (
    DeterministicModelRouter,
    ModelRegistry,
    RoutingRequirements,
)

from .models import (
    AgentExecutionSpec,
    AgentRevision,
    AgentRevisionRef,
    AgentRunRecord,
    AgentRunStatus,
    AgentTeamRevision,
    AgentTeamRevisionRef,
    CapabilityConstraint,
    ModelFallbackPolicy,
    OrchestratorMapping,
    UnavailableMemberPolicy,
    new_agent_run_id,
)
from .service import AgentService


class AgentOrchestratorMapper(Protocol):
    """Maps a canonical Agent execution snapshot into one private runtime representation."""

    @property
    def adapter_id(self) -> str: ...

    async def map_agent(self, spec: AgentExecutionSpec) -> OrchestratorMapping: ...


class ReferenceOrchestratorMapper:
    """Hermes-free deterministic mapper used by the reference runtime and contract tests."""

    adapter_id = "reference-orchestrator"

    async def map_agent(self, spec: AgentExecutionSpec) -> OrchestratorMapping:
        agent = spec.agent_revision
        runtime_ref = f"reference:{agent.agent_id}:r{agent.revision}:{spec.run_id}"
        return OrchestratorMapping(
            adapter_id=self.adapter_id,
            runtime_ref=runtime_ref,
            metadata={
                "agent_id": agent.agent_id,
                "agent_revision": agent.revision,
                "run_id": spec.run_id,
            },
        )


class AgentRuntime:
    """Resolve and pin canonical Agent snapshots before replaceable orchestration begins."""

    def __init__(
        self,
        service: AgentService,
        *,
        model_registry: ModelRegistry | None = None,
        capability_registry: CapabilityRegistry | None = None,
        routing_profiles: Mapping[str, RoutingRequirements] | None = None,
    ) -> None:
        self.service = service
        self.model_registry = model_registry
        self.capability_registry = capability_registry
        self.routing_profiles = dict(routing_profiles or {})

    def prepare_agent(
        self,
        *,
        task_id: str,
        run_id: str,
        agent_id: str,
        revision: int | None = None,
        team_revision: AgentTeamRevision | None = None,
        task_model_override: RoutingRequirements | None = None,
        requested_capability_ids: tuple[str, ...] = (),
        shared_capability_ids: tuple[str, ...] = (),
        available_capability_ids: frozenset[str] = frozenset(),
        granted_permissions: frozenset[str] = frozenset(),
        available_worker_capabilities: frozenset[str] = frozenset(),
        task_context: Mapping[str, JsonValue] | None = None,
        project_context: Mapping[str, JsonValue] | None = None,
    ) -> AgentExecutionSpec:
        agent = self.service.get_agent_revision(agent_id, revision)
        if not agent.profile.enabled:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"agent is disabled: {agent.agent_id}@{agent.revision}",
            )

        requirements = self._effective_model_requirements(agent, task_model_override)
        model_id, provider_id = self._resolve_model(agent, requirements)
        capability_ids = self._resolve_capabilities(
            agent,
            requested_capability_ids=requested_capability_ids,
            shared_capability_ids=shared_capability_ids,
            available_capability_ids=available_capability_ids,
            granted_permissions=granted_permissions,
            available_worker_capabilities=available_worker_capabilities,
        )
        return AgentExecutionSpec(
            task_id=task_id,
            run_id=run_id,
            agent_revision=agent,
            capability_ids=capability_ids,
            selected_model_config_id=model_id,
            selected_provider_id=provider_id,
            team_revision=team_revision,
            task_context=task_context or {},
            project_context=project_context or {},
        )

    async def start_agent(
        self,
        *,
        task_id: str,
        run_id: str,
        agent_id: str,
        revision: int | None = None,
        mapper: AgentOrchestratorMapper | None = None,
        team_revision: AgentTeamRevision | None = None,
        task_model_override: RoutingRequirements | None = None,
        requested_capability_ids: tuple[str, ...] = (),
        shared_capability_ids: tuple[str, ...] = (),
        available_capability_ids: frozenset[str] = frozenset(),
        granted_permissions: frozenset[str] = frozenset(),
        available_worker_capabilities: frozenset[str] = frozenset(),
        task_context: Mapping[str, JsonValue] | None = None,
        project_context: Mapping[str, JsonValue] | None = None,
    ) -> AgentRunRecord:
        spec = self.prepare_agent(
            task_id=task_id,
            run_id=run_id,
            agent_id=agent_id,
            revision=revision,
            team_revision=team_revision,
            task_model_override=task_model_override,
            requested_capability_ids=requested_capability_ids,
            shared_capability_ids=shared_capability_ids,
            available_capability_ids=available_capability_ids,
            granted_permissions=granted_permissions,
            available_worker_capabilities=available_worker_capabilities,
            task_context=task_context,
            project_context=project_context,
        )
        selected_mapper = mapper or ReferenceOrchestratorMapper()
        mapping = await selected_mapper.map_agent(spec)
        if mapping.adapter_id != selected_mapper.adapter_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "orchestrator mapping adapter ID does not match selected mapper",
            )
        record = self._record_from_spec(spec, mapping)
        self.service.repository.create_agent_run(record)
        return record

    async def start_team(
        self,
        *,
        task_id: str,
        run_id: str,
        team_id: str,
        revision: int | None = None,
        mapper: AgentOrchestratorMapper | None = None,
        task_model_override: RoutingRequirements | None = None,
        requested_capability_ids: tuple[str, ...] = (),
        available_capability_ids: frozenset[str] = frozenset(),
        granted_permissions: frozenset[str] = frozenset(),
        available_worker_capabilities: frozenset[str] = frozenset(),
        task_context: Mapping[str, JsonValue] | None = None,
        project_context: Mapping[str, JsonValue] | None = None,
    ) -> tuple[AgentRunRecord, ...]:
        team = self.service.get_team_revision(team_id, revision)
        if not team.profile.enabled:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"agent team is disabled: {team.team_id}@{team.revision}",
            )

        specs: list[AgentExecutionSpec] = []
        for member in team.profile.members:
            try:
                spec = self.prepare_agent(
                    task_id=task_id,
                    run_id=run_id,
                    agent_id=member.agent.agent_id,
                    revision=member.agent.revision,
                    team_revision=team,
                    task_model_override=task_model_override,
                    requested_capability_ids=requested_capability_ids,
                    shared_capability_ids=team.profile.shared_capability_ids,
                    available_capability_ids=available_capability_ids,
                    granted_permissions=granted_permissions,
                    available_worker_capabilities=available_worker_capabilities,
                    task_context=task_context,
                    project_context=project_context,
                )
            except ContractError:
                if (
                    not member.required
                    and team.profile.unavailable_member_policy
                    is UnavailableMemberPolicy.SKIP_OPTIONAL
                ):
                    continue
                raise
            specs.append(spec)

        if not specs:
            raise ContractError(ErrorCode.UNAVAILABLE, "agent team has no executable members")
        if (
            team.profile.max_parallel_agents is not None
            and len(specs) > team.profile.max_parallel_agents
        ):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "team member count exceeds max_parallel_agents for one reference start",
                details={
                    "member_count": len(specs),
                    "max_parallel_agents": team.profile.max_parallel_agents,
                },
            )

        selected_mapper = mapper or ReferenceOrchestratorMapper()
        mappings = [await selected_mapper.map_agent(spec) for spec in specs]
        for mapping in mappings:
            if mapping.adapter_id != selected_mapper.adapter_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "orchestrator mapping adapter ID does not match selected mapper",
                )

        records = tuple(
            self._record_from_spec(spec, mapping)
            for spec, mapping in zip(specs, mappings, strict=True)
        )
        for record in records:
            self.service.repository.create_agent_run(record)
        return records

    def finish_agent_run(
        self,
        agent_run_id: str,
        *,
        status: AgentRunStatus,
        artifact_ids: tuple[str, ...] = (),
        result_ids: tuple[str, ...] = (),
        model_call_refs: tuple[str, ...] = (),
        tool_invocation_refs: tuple[str, ...] = (),
        error: str | None = None,
        telemetry: Mapping[str, JsonValue] | None = None,
        verification_context: Mapping[str, JsonValue] | None = None,
    ) -> AgentRunRecord:
        if status not in {
            AgentRunStatus.SUCCEEDED,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "finish_agent_run requires a terminal AgentRun status",
            )
        current = self.service.repository.get_agent_run(agent_run_id)
        updated = replace(
            current,
            status=status,
            artifact_ids=artifact_ids,
            result_ids=result_ids,
            model_call_refs=model_call_refs,
            tool_invocation_refs=tool_invocation_refs,
            error=error,
            telemetry=telemetry or current.telemetry,
            verification_context=verification_context or current.verification_context,
            finished_at=datetime.now(UTC),
        )
        self.service.repository.update_agent_run(updated)
        return updated

    def _effective_model_requirements(
        self,
        agent: AgentRevision,
        task_override: RoutingRequirements | None,
    ) -> RoutingRequirements:
        requirements = agent.profile.model.requirements
        profile_ref = agent.profile.model.routing_profile_ref
        if profile_ref is not None:
            try:
                profile_requirements = self.routing_profiles[profile_ref]
            except KeyError as exc:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    f"agent routing profile is not configured: {profile_ref}",
                ) from exc
            requirements = _merge_requirements(profile_requirements, requirements)

        if task_override is not None:
            if not agent.profile.model.allow_task_override:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "task-level model override is not permitted by this Agent revision",
                )
            requirements = _merge_requirements(requirements, task_override)
        return requirements

    def _resolve_model(
        self,
        agent: AgentRevision,
        requirements: RoutingRequirements,
    ) -> tuple[str | None, str | None]:
        if self.model_registry is None:
            if requirements != RoutingRequirements():
                raise ContractError(
                    ErrorCode.UNAVAILABLE,
                    "agent requires model routing but no ModelRegistry is attached",
                )
            return None, None

        router = DeterministicModelRouter(self.model_registry)
        try:
            route = router.route(requirements)
        except ContractError:
            if (
                requirements.explicit_model_id is None
                or agent.profile.model.fallback is not ModelFallbackPolicy.ROUTE
            ):
                raise
            route = router.route(replace(requirements, explicit_model_id=None))
        return route.model_config_id, route.provider_id

    def _resolve_capabilities(
        self,
        agent: AgentRevision,
        *,
        requested_capability_ids: tuple[str, ...],
        shared_capability_ids: tuple[str, ...],
        available_capability_ids: frozenset[str],
        granted_permissions: frozenset[str],
        available_worker_capabilities: frozenset[str],
    ) -> tuple[str, ...]:
        policy = agent.profile.capabilities
        effective = set(policy.required_ids)
        effective.update(requested_capability_ids)
        effective.update(shared_capability_ids)

        denied = effective.intersection(policy.denied)
        if denied:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "Agent capability policy denies one or more requested capabilities",
                details={"capability_ids": cast(JsonValue, sorted(denied))},
            )
        if policy.allowed:
            outside_allowlist = effective - set(policy.allowed)
            if outside_allowlist:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "Agent capability request exceeds its allowlist",
                    details={"capability_ids": cast(JsonValue, sorted(outside_allowlist))},
                )

        constraints = {item.capability_id: item for item in policy.constraints}
        if self.capability_registry is None:
            approval_constrained = sorted(
                capability_id
                for capability_id in effective
                if (constraint := constraints.get(capability_id)) is not None
                and constraint.approval_ref is not None
            )
            if approval_constrained:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "Agent approval requirements need a canonical CapabilityRegistry",
                    details={"capability_ids": cast(JsonValue, approval_constrained)},
                )
            missing = effective - set(available_capability_ids)
            if missing:
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "Agent requires capabilities that are not available in the reference runtime",
                    details={"capability_ids": cast(JsonValue, sorted(missing))},
                )
            return tuple(sorted(effective))

        for capability_id in sorted(effective):
            constraint = constraints.get(capability_id)
            self._resolve_registered_capability(
                capability_id,
                constraint,
                granted_permissions=granted_permissions,
                available_worker_capabilities=available_worker_capabilities,
            )
        return tuple(sorted(effective))

    def _resolve_registered_capability(
        self,
        capability_id: str,
        constraint: CapabilityConstraint | None,
        *,
        granted_permissions: frozenset[str],
        available_worker_capabilities: frozenset[str],
    ) -> None:
        assert self.capability_registry is not None
        exact_version = constraint.exact_version if constraint is not None else None
        compatibility: CapabilityCompatibilityRequest | None = None
        if constraint is not None and (
            constraint.minimum_version is not None
            or constraint.maximum_version is not None
            or constraint.required_features
        ):
            compatibility = CapabilityCompatibilityRequest(
                minimum_version=constraint.minimum_version,
                maximum_version=constraint.maximum_version,
                required_features=constraint.required_features,
            )
        registration, _ = self.capability_registry.resolve(
            capability_id,
            version=exact_version,
            compatibility=compatibility,
            granted_permissions=granted_permissions,
            available_worker_capabilities=available_worker_capabilities,
        )
        if constraint is not None and constraint.approval_ref is not None:
            required_approvals = set(registration.capability.required_approvals)
            if constraint.approval_ref not in required_approvals:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    (
                        "Agent capability approval requirement is not enforced by the "
                        "resolved canonical capability"
                    ),
                    details={
                        "capability_id": capability_id,
                        "approval_ref": constraint.approval_ref,
                        "capability_required_approvals": cast(
                            JsonValue,
                            sorted(required_approvals),
                        ),
                    },
                )

    @staticmethod
    def _record_from_spec(
        spec: AgentExecutionSpec,
        mapping: OrchestratorMapping,
    ) -> AgentRunRecord:
        team_ref = None
        if spec.team_revision is not None:
            team_ref = AgentTeamRevisionRef(
                team_id=spec.team_revision.team_id,
                revision=spec.team_revision.revision,
            )
        return AgentRunRecord(
            agent_run_id=new_agent_run_id(),
            run_id=spec.run_id,
            task_id=spec.task_id,
            agent=AgentRevisionRef(
                agent_id=spec.agent_revision.agent_id,
                revision=spec.agent_revision.revision,
            ),
            team=team_ref,
            status=AgentRunStatus.RUNNING,
            selected_model_config_id=spec.selected_model_config_id,
            selected_provider_id=spec.selected_provider_id,
            capability_ids=spec.capability_ids,
            orchestrator_adapter_id=mapping.adapter_id,
            orchestrator_runtime_ref=mapping.runtime_ref,
            telemetry={"orchestrator_mapping": dict(mapping.metadata)},
        )


def _merge_requirements(
    base: RoutingRequirements,
    overlay: RoutingRequirements,
) -> RoutingRequirements:
    """Merge requirements monotonically while allowing an explicit model override."""

    context_windows = [
        value
        for value in (base.min_context_window, overlay.min_context_window)
        if value is not None
    ]
    modalities = tuple(dict.fromkeys((*base.modalities, *overlay.modalities)))
    reasoning = tuple(dict.fromkeys((*base.reasoning, *overlay.reasoning)))
    local_only = base.local_only or overlay.local_only
    self_hosted_only = base.self_hosted_only or overlay.self_hosted_only
    if local_only and self_hosted_only:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "combined Agent model requirements conflict: local_only and self_hosted_only",
        )
    return RoutingRequirements(
        explicit_model_id=overlay.explicit_model_id or base.explicit_model_id,
        min_context_window=max(context_windows) if context_windows else None,
        tool_calling=base.tool_calling or overlay.tool_calling,
        structured_output=base.structured_output or overlay.structured_output,
        streaming=base.streaming or overlay.streaming,
        modalities=modalities,
        reasoning=reasoning,
        local_only=local_only,
        self_hosted_only=self_hosted_only,
    )