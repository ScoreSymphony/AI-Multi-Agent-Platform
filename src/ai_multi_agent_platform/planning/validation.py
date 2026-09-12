"""Deterministic structural and inventory-backed planning proposal validation."""

from __future__ import annotations

from collections.abc import Mapping

from ai_multi_agent_platform.agents import AgentMatchStatus
from ai_multi_agent_platform.models import ModelLocation, RoutingRequirements

from .agent_matching import match_planning_step
from .models import (
    PlanningAgentCandidate,
    PlanningCapabilityCandidate,
    PlanningInventory,
    PlanningModelCandidate,
    PlanningRequest,
    PlanningStepDraft,
    PlanningTeamCandidate,
    PlanProposal,
    ProposalValidation,
)

_FORBIDDEN_PROVIDER_METADATA_KEYS = frozenset(
    {
        "provider_id",
        "provider_model_id",
        "provider_native_id",
        "native_model_id",
        "host_id",
        "node_id",
        "worker_id",
        "gpu_id",
        "vps_id",
        "endpoint_id",
    }
)


class PlanningProposalValidator:
    """Validate immutable proposals without invoking execution or mutating canonical state."""

    def validate(self, proposal: PlanProposal, request: PlanningRequest) -> ProposalValidation:
        errors: list[str] = []
        warnings: list[str] = []
        approval_required = False
        steps = proposal.steps
        if not steps:
            errors.append("Plan requires at least one Step")
        if len(steps) > request.max_steps:
            errors.append(f"Plan has {len(steps)} Steps but max_steps is {request.max_steps}")

        keys = [step.key for step in steps]
        if len(keys) != len(set(keys)):
            errors.append("Step keys must be unique")
        known_keys = set(keys)
        for step in steps:
            unknown = set(step.depends_on) - known_keys
            if unknown:
                errors.append(
                    f"Step {step.key} references unknown dependencies: {sorted(unknown)!r}"
                )
        if not errors and self.has_cycle(steps):
            errors.append("Step dependency graph contains a cycle")

        if request.max_parallel_steps is not None and not errors:
            peak = self.peak_parallelism(steps)
            if peak > request.max_parallel_steps:
                errors.append(
                    f"Plan requires parallelism {peak} but max_parallel_steps is "
                    f"{request.max_parallel_steps}"
                )

        agents = {(item.agent_id, item.revision): item for item in request.inventory.agents}
        teams = {(item.team_id, item.revision): item for item in request.inventory.teams}
        models = {item.model_config_id: item for item in request.inventory.models}
        capabilities = self.capability_map(request.inventory)
        prior = request.prior_plan

        for step in steps:
            assignment_agent: PlanningAgentCandidate | None = None
            assignment_team: PlanningTeamCandidate | None = None
            if step.assignment is None:
                errors.append(f"Step {step.key} requires an Agent, Agent Team or role assignment")
            elif step.assignment.agent_id is not None:
                revision = step.assignment.agent_revision
                if revision is None:
                    errors.append(f"Step {step.key} Agent assignment is missing its revision")
                else:
                    assignment_agent = agents.get((step.assignment.agent_id, revision))
                    if assignment_agent is None:
                        errors.append(
                            f"Step {step.key} references missing Agent revision "
                            f"{step.assignment.agent_id}@{revision}"
                        )
                    elif not assignment_agent.enabled:
                        errors.append(
                            f"Step {step.key} references disabled Agent {assignment_agent.agent_id}"
                        )
            elif step.assignment.team_id is not None:
                revision = step.assignment.team_revision
                if revision is None:
                    errors.append(f"Step {step.key} Team assignment is missing its revision")
                else:
                    assignment_team = teams.get((step.assignment.team_id, revision))
                    if assignment_team is None:
                        errors.append(
                            f"Step {step.key} references missing Agent Team revision "
                            f"{step.assignment.team_id}@{revision}"
                        )
                    elif not assignment_team.enabled:
                        errors.append(
                            f"Step {step.key} references disabled/incompatible Agent Team "
                            f"{assignment_team.team_id}"
                        )
            elif step.assignment.role_requirement is not None:
                role = step.assignment.role_requirement
                match = match_planning_step(step, request, agent_only=True)
                if match is None or match.status is AgentMatchStatus.NO_MATCH:
                    errors.append(
                        f"Step {step.key} has no eligible canonical Agent for role {role!r}"
                    )
                elif match.status is AgentMatchStatus.AMBIGUOUS:
                    refs = [
                        f"{item.agent_id}@{item.revision}"
                        for item in match.ambiguous
                        if hasattr(item, "agent_id")
                    ]
                    errors.append(
                        f"Step {step.key} Agent match for role {role!r} is ambiguous: {refs!r}"
                    )

            if self.contains_provider_private_metadata(step.metadata):
                errors.append(
                    f"Step {step.key} metadata contains provider-private runtime identity"
                )

            required_by_step = {
                requirement.capability_id
                for requirement in step.capability_requirements
                if requirement.required
            }
            if assignment_agent is not None:
                missing_agent_requirements = (
                    set(assignment_agent.required_capability_ids) - required_by_step
                )
                if missing_agent_requirements:
                    errors.append(
                        f"Step {step.key} omits required Agent capabilities: "
                        f"{sorted(missing_agent_requirements)!r}"
                    )

            for requirement in step.capability_requirements:
                candidate = capabilities.get(requirement.capability_id)
                if candidate is None:
                    if requirement.required:
                        errors.append(
                            f"Step {step.key} requires missing capability "
                            f"{requirement.capability_id}"
                        )
                    else:
                        warnings.append(
                            f"Step {step.key} optional capability is missing: "
                            f"{requirement.capability_id}"
                        )
                    continue
                if requirement.required and not candidate.available:
                    errors.append(
                        f"Step {step.key} requires unavailable capability {candidate.capability_id}"
                    )
                if (
                    requirement.exact_version is not None
                    and candidate.version != requirement.exact_version
                ):
                    errors.append(
                        f"Step {step.key} requires {candidate.capability_id}@"
                        f"{requirement.exact_version}, found {candidate.version}"
                    )
                missing_features = set(requirement.required_features) - set(candidate.features)
                if missing_features:
                    errors.append(
                        f"Step {step.key} capability {candidate.capability_id} misses features "
                        f"{sorted(missing_features)!r}"
                    )
                missing_permissions = set(candidate.required_permissions) - set(
                    request.granted_permissions
                )
                if missing_permissions:
                    errors.append(
                        f"Step {step.key} lacks permissions for {candidate.capability_id}: "
                        f"{sorted(missing_permissions)!r}"
                    )
                if (
                    candidate.required_approvals
                    or candidate.safety != "standard"
                    or candidate.side_effects in {"external", "destructive"}
                ):
                    approval_required = True
                    warnings.append(
                        f"Step {step.key} capability {candidate.capability_id} requires activation "
                        "approval"
                    )
                if assignment_agent is not None:
                    if candidate.capability_id in assignment_agent.denied_capability_ids:
                        errors.append(
                            f"Step {step.key} capability {candidate.capability_id} is denied for "
                            f"Agent {assignment_agent.agent_id}"
                        )
                    if (
                        assignment_agent.allowed_capability_ids
                        and candidate.capability_id not in assignment_agent.allowed_capability_ids
                    ):
                        errors.append(
                            f"Step {step.key} capability {candidate.capability_id} is "
                            "outside Agent "
                            f"{assignment_agent.agent_id} allowlist"
                        )
                if assignment_team is not None and assignment_team.shared_capability_ids:
                    if candidate.capability_id not in assignment_team.shared_capability_ids:
                        warnings.append(
                            f"Step {step.key} capability {candidate.capability_id} is not a shared "
                            "Team capability; member-level policy must provide it"
                        )

            if step.requires_model or self.has_model_requirements(step.model_requirements):
                compatible = [
                    candidate
                    for candidate in request.inventory.models
                    if self.model_matches(candidate, step.model_requirements)
                ]
                if not compatible:
                    errors.append(f"Step {step.key} has no compatible available canonical model")
                explicit = step.model_requirements.explicit_model_id
                if explicit is not None and explicit not in models:
                    errors.append(
                        f"Step {step.key} references unknown canonical model "
                        f"configuration {explicit}"
                    )

            if step.reuse_step_ids:
                if prior is None:
                    errors.append(f"Step {step.key} cannot reuse work without a prior Plan")
                else:
                    allowed_reuse = set(prior.completed_step_ids)
                    invalid_reuse = set(step.reuse_step_ids) - allowed_reuse
                    if invalid_reuse:
                        errors.append(
                            f"Step {step.key} may reuse only completed prior Steps, not "
                            f"{sorted(invalid_reuse)!r}"
                        )

        return ProposalValidation(
            valid=not errors,
            errors=tuple(errors),
            warnings=tuple(dict.fromkeys(warnings)),
            approval_required=approval_required,
        )

    @staticmethod
    def has_model_requirements(requirements: RoutingRequirements) -> bool:
        return any(
            (
                requirements.explicit_model_id is not None,
                requirements.min_context_window is not None,
                requirements.tool_calling,
                requirements.structured_output,
                requirements.streaming,
                bool(requirements.modalities),
                bool(requirements.reasoning),
                requirements.local_only,
                requirements.self_hosted_only,
            )
        )

    @staticmethod
    def model_matches(
        candidate: PlanningModelCandidate,
        requirements: RoutingRequirements,
    ) -> bool:
        if not candidate.enabled or not candidate.available:
            return False
        if (
            requirements.explicit_model_id is not None
            and candidate.model_config_id != requirements.explicit_model_id
        ):
            return False
        if requirements.local_only and candidate.location is not ModelLocation.LOCAL:
            return False
        if requirements.self_hosted_only and candidate.location not in {
            ModelLocation.LOCAL,
            ModelLocation.SELF_HOSTED,
        }:
            return False
        if requirements.min_context_window is not None:
            if (
                candidate.context_window is None
                or candidate.context_window < requirements.min_context_window
            ):
                return False
        if requirements.tool_calling and not candidate.tool_calling:
            return False
        if requirements.structured_output and not candidate.structured_output:
            return False
        if requirements.streaming and not candidate.streaming:
            return False
        if requirements.modalities and not set(requirements.modalities).issubset(
            candidate.modalities
        ):
            return False
        if requirements.reasoning and not set(requirements.reasoning).issubset(candidate.reasoning):
            return False
        return True

    @staticmethod
    def has_cycle(steps: tuple[PlanningStepDraft, ...]) -> bool:
        dependencies = {step.key: step.depends_on for step in steps}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(key: str) -> bool:
            if key in visited:
                return False
            if key in visiting:
                return True
            visiting.add(key)
            for dependency in dependencies.get(key, ()):
                if visit(dependency):
                    return True
            visiting.remove(key)
            visited.add(key)
            return False

        return any(visit(key) for key in dependencies)

    @staticmethod
    def peak_parallelism(steps: tuple[PlanningStepDraft, ...]) -> int:
        dependencies = {step.key: set(step.depends_on) for step in steps}
        remaining = dict(dependencies)
        peak = 0
        completed: set[str] = set()
        while remaining:
            ready = sorted(key for key, deps in remaining.items() if deps.issubset(completed))
            if not ready:
                return len(remaining)
            peak = max(peak, len(ready))
            completed.update(ready)
            for key in ready:
                remaining.pop(key)
        return peak

    @staticmethod
    def contains_provider_private_metadata(metadata: object) -> bool:
        if not isinstance(metadata, Mapping):
            return True
        stack: list[object] = [metadata]
        while stack:
            value = stack.pop()
            if isinstance(value, Mapping):
                for key, item in value.items():
                    if str(key).lower() in _FORBIDDEN_PROVIDER_METADATA_KEYS:
                        return True
                    stack.append(item)
            elif isinstance(value, (list, tuple)):
                stack.extend(value)
        return False

    @staticmethod
    def capability_map(inventory: PlanningInventory) -> dict[str, PlanningCapabilityCandidate]:
        result: dict[str, PlanningCapabilityCandidate] = {}
        for candidate in inventory.capabilities:
            current = result.get(candidate.capability_id)
            if current is None or (not current.available and candidate.available):
                result[candidate.capability_id] = candidate
        return result
