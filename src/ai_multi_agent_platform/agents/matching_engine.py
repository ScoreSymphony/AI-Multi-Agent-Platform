"""Deterministic eligibility, ranking, capability and model rules for Agent matching."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ai_multi_agent_platform.capabilities import CapabilityRegistry, CapabilitySpec
from ai_multi_agent_platform.contracts.types import HealthStatus
from ai_multi_agent_platform.models import (
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
    RoutingRequirements,
)

from .matching_contracts import (
    AgentCapabilityRequirement,
    AgentMatchCandidate,
    AgentMatchCandidateOutcome,
    AgentMatchingPolicy,
    AgentMatchingRequirements,
    AgentMatchReason,
    AgentMatchRejection,
    AgentMatchResult,
    AgentMatchStatus,
    AgentParticipantRef,
    AgentTieBreakPolicy,
)
from .models import AgentRevisionRef, AgentTeamRevisionRef, CapabilityConstraint


class AgentMatcher:
    """Deterministic, explainable matcher over canonical candidate snapshots."""

    def __init__(
        self,
        *,
        capability_specs: Iterable[CapabilitySpec] = (),
        model_registry: ModelRegistry | None = None,
        policy: AgentMatchingPolicy | None = None,
    ) -> None:
        self._capabilities = tuple(capability_specs)
        self._models = model_registry
        self._policy = policy

    @classmethod
    def from_capability_registry(
        cls,
        registry: CapabilityRegistry,
        *,
        model_registry: ModelRegistry | None = None,
        policy: AgentMatchingPolicy | None = None,
    ) -> AgentMatcher:
        return cls(
            capability_specs=registry.inventory_capabilities(include_unavailable=False),
            model_registry=model_registry,
            policy=policy,
        )

    def match(
        self,
        requirements: AgentMatchingRequirements,
        candidates: Iterable[AgentMatchCandidate],
    ) -> AgentMatchResult:
        materialized = tuple(candidates)
        exact = requirements.exact_agent or requirements.exact_team
        if exact is not None:
            pinned = tuple(candidate for candidate in materialized if candidate.ref == exact)
            if not pinned:
                return AgentMatchResult(
                    status=AgentMatchStatus.NO_MATCH,
                    selected=None,
                    outcomes=(),
                )
            outcome = self.evaluate(requirements, pinned[0])
            return AgentMatchResult(
                status=(
                    AgentMatchStatus.SELECTED if outcome.eligible else AgentMatchStatus.NO_MATCH
                ),
                selected=(pinned[0].ref if outcome.eligible else None),
                outcomes=(outcome,),
            )

        outcomes = tuple(self.evaluate(requirements, candidate) for candidate in materialized)
        eligible = [outcome for outcome in outcomes if outcome.eligible]
        if not eligible:
            return AgentMatchResult(
                status=AgentMatchStatus.NO_MATCH,
                selected=None,
                outcomes=outcomes,
            )
        eligible.sort(
            key=lambda outcome: (outcome.score, _candidate_sort_key(outcome.candidate)),
            reverse=True,
        )
        best_score = eligible[0].score
        best = [outcome for outcome in eligible if outcome.score == best_score]
        if len(best) == 1:
            return AgentMatchResult(
                status=AgentMatchStatus.SELECTED,
                selected=best[0].candidate.ref,
                outcomes=outcomes,
            )
        if requirements.tie_break is AgentTieBreakPolicy.CANONICAL_ID:
            selected = min(best, key=lambda item: _candidate_sort_key(item.candidate))
            return AgentMatchResult(
                status=AgentMatchStatus.SELECTED,
                selected=selected.candidate.ref,
                outcomes=outcomes,
                ambiguous=tuple(item.candidate.ref for item in best),
            )
        return AgentMatchResult(
            status=AgentMatchStatus.AMBIGUOUS,
            selected=None,
            outcomes=outcomes,
            ambiguous=tuple(
                item.candidate.ref
                for item in sorted(best, key=lambda item: _candidate_sort_key(item.candidate))
            ),
        )

    def evaluate(
        self,
        requirements: AgentMatchingRequirements,
        candidate: AgentMatchCandidate,
    ) -> AgentMatchCandidateOutcome:
        rejections = [
            *self._base_rejections(requirements, candidate),
            *self._capability_rejections(requirements, candidate),
        ]
        policy_requirement_rejection = self._policy_requirement_rejection(requirements, candidate)
        if policy_requirement_rejection is not None:
            rejections.append(policy_requirement_rejection)
        model_rejection = self._model_rejection(requirements, candidate)
        if model_rejection is not None:
            rejections.append(model_rejection)
        policy_rejection = self._matching_policy_rejection(requirements, candidate)
        if policy_rejection is not None:
            rejections.append(policy_rejection)
        if rejections:
            return AgentMatchCandidateOutcome(
                candidate=candidate,
                eligible=False,
                rejections=tuple(rejections),
            )
        return self._eligible_outcome(requirements, candidate)

    @staticmethod
    def _base_rejections(
        requirements: AgentMatchingRequirements,
        candidate: AgentMatchCandidate,
    ) -> list[AgentMatchRejection]:
        rejections: list[AgentMatchRejection] = []
        if candidate.kind not in requirements.candidate_kinds:
            rejections.append(
                AgentMatchRejection(AgentMatchReason.UNSUPPORTED_KIND, "candidate kind not allowed")
            )
        if not candidate.enabled:
            rejections.append(
                AgentMatchRejection(AgentMatchReason.DISABLED, "candidate is disabled")
            )
        if not _scope_compatible(candidate, requirements):
            rejections.append(
                AgentMatchRejection(AgentMatchReason.WRONG_SCOPE, "candidate scope is incompatible")
            )
        if requirements.required_roles and not set(requirements.required_roles).issubset(
            candidate.roles
        ):
            rejections.append(
                AgentMatchRejection(
                    AgentMatchReason.ROLE_MISMATCH,
                    "candidate does not provide every required role",
                )
            )
        if _independence_conflict(candidate, requirements.excluded_participants):
            rejections.append(
                AgentMatchRejection(
                    AgentMatchReason.INDEPENDENCE_VIOLATION,
                    "candidate violates supplied producer/reviewer independence",
                )
            )
        return rejections

    def _capability_rejections(
        self,
        requirements: AgentMatchingRequirements,
        candidate: AgentMatchCandidate,
    ) -> list[AgentMatchRejection]:
        rejections: list[AgentMatchRejection] = []
        for capability_id in requirements.forbidden_capability_ids:
            if _candidate_exposes_capability(candidate, capability_id):
                rejections.append(
                    AgentMatchRejection(
                        AgentMatchReason.DENIED_CAPABILITY,
                        f"candidate exposes forbidden capability {capability_id}",
                    )
                )
        for requirement in requirements.required_capabilities:
            rejection = self._capability_rejection(candidate, requirement)
            if rejection is not None:
                rejections.append(rejection)
        return rejections

    @staticmethod
    def _policy_requirement_rejection(
        requirements: AgentMatchingRequirements,
        candidate: AgentMatchCandidate,
    ) -> AgentMatchRejection | None:
        missing_policies = set(requirements.required_policy_refs) - set(candidate.policy_refs)
        if not missing_policies:
            return None
        return AgentMatchRejection(
            AgentMatchReason.POLICY_DENIED,
            "candidate does not satisfy required policy references",
        )

    def _model_rejection(
        self,
        requirements: AgentMatchingRequirements,
        candidate: AgentMatchCandidate,
    ) -> AgentMatchRejection | None:
        if not candidate.model_requirements_valid:
            detail = "candidate model routing policy cannot be resolved canonically"
        elif (
            requirements.model_requirements_are_task_override
            and requirements.model_requirements != RoutingRequirements()
            and not candidate.allows_task_model_override
        ):
            return AgentMatchRejection(
                AgentMatchReason.MODEL_OVERRIDE_FORBIDDEN,
                "candidate Agent policy forbids task-level model overrides",
            )
        elif self._model_feasible(
            candidate,
            requirements.model_requirements,
            work_is_task_override=requirements.model_requirements_are_task_override,
        ):
            return None
        else:
            detail = "candidate and work model requirements have no compatible canonical model"
        return AgentMatchRejection(AgentMatchReason.MODEL_REQUIREMENT_INCOMPATIBLE, detail)

    def _matching_policy_rejection(
        self,
        requirements: AgentMatchingRequirements,
        candidate: AgentMatchCandidate,
    ) -> AgentMatchRejection | None:
        if self._policy is None or self._policy.allows(candidate, requirements):
            return None
        return AgentMatchRejection(
            AgentMatchReason.POLICY_DENIED,
            "candidate denied by canonical policy",
        )

    @staticmethod
    def _eligible_outcome(
        requirements: AgentMatchingRequirements,
        candidate: AgentMatchCandidate,
    ) -> AgentMatchCandidateOutcome:
        preferred_id = int(
            (
                isinstance(candidate.ref, AgentRevisionRef)
                and candidate.ref.agent_id in requirements.preferred_agent_ids
            )
            or (
                isinstance(candidate.ref, AgentTeamRevisionRef)
                and candidate.ref.team_id in requirements.preferred_team_ids
            )
        )
        preferred_roles = len(set(candidate.roles).intersection(requirements.preferred_roles))
        excess_privilege = max(
            0,
            len(_declared_capability_ids(candidate)) - len(requirements.required_capabilities),
        )
        return AgentMatchCandidateOutcome(
            candidate=candidate,
            eligible=True,
            score=(
                preferred_id,
                preferred_roles,
                candidate.matching_priority,
                -excess_privilege,
            ),
            rationale=(
                "all mandatory eligibility constraints satisfied",
                f"preferred-role matches={preferred_roles}",
                f"configured matching priority={candidate.matching_priority}",
                f"excess declared capabilities={excess_privilege}",
            ),
        )

    def _capability_rejection(
        self,
        candidate: AgentMatchCandidate,
        requirement: AgentCapabilityRequirement,
    ) -> AgentMatchRejection | None:
        capability_id = requirement.capability_id
        if capability_id in candidate.denied_capability_ids:
            return AgentMatchRejection(
                AgentMatchReason.DENIED_CAPABILITY,
                f"required capability {capability_id} is explicitly denied",
            )
        declared = _declared_capability_ids(candidate)
        if capability_id not in declared and not candidate.capabilities_unrestricted:
            return AgentMatchRejection(
                AgentMatchReason.MISSING_CAPABILITY,
                f"required capability {capability_id} is not assigned to candidate",
            )

        candidate_constraints = tuple(
            item for item in candidate.capability_constraints if item.capability_id == capability_id
        )
        if not self._capabilities:
            if candidate_constraints and not _constraints_can_jointly_overlap(
                requirement,
                candidate_constraints,
            ):
                return AgentMatchRejection(
                    AgentMatchReason.CAPABILITY_VERSION_INCOMPATIBLE,
                    f"required capability {capability_id} conflicts with candidate constraint",
                )
            return None

        canonical_specs = tuple(
            spec
            for spec in self._capabilities
            if spec.capability_id == capability_id
            and spec.available
            and spec.health is not HealthStatus.UNAVAILABLE
        )
        if not canonical_specs:
            return AgentMatchRejection(
                AgentMatchReason.MISSING_CAPABILITY,
                f"required capability {capability_id} has no available canonical version",
            )
        compatible = [
            spec
            for spec in canonical_specs
            if _capability_requirement_matches(requirement, spec)
            and all(
                _capability_constraint_matches(candidate_constraint, spec)
                for candidate_constraint in candidate_constraints
            )
        ]
        if not compatible:
            return AgentMatchRejection(
                AgentMatchReason.CAPABILITY_VERSION_INCOMPATIBLE,
                f"required capability {capability_id} has no compatible canonical version",
            )
        return None

    def _model_feasible(
        self,
        candidate: AgentMatchCandidate,
        work: RoutingRequirements,
        *,
        work_is_task_override: bool,
    ) -> bool:
        candidate_requirements = candidate.model_requirements or (RoutingRequirements(),)
        merged = tuple(
            value
            for value in (
                _merge_model_requirements(
                    item,
                    work,
                    overlay_replaces_explicit=work_is_task_override,
                )
                for item in candidate_requirements
            )
            if value is not None
        )
        if not merged:
            return False
        if self._models is None:
            return True
        models = self._models.list_models(enabled=True)
        return all(
            any(_model_matches(config, requirement, self._models) for config in models)
            for requirement in merged
        )


def _scope_compatible(
    candidate: AgentMatchCandidate,
    requirements: AgentMatchingRequirements,
) -> bool:
    if candidate.project_id is not None and candidate.project_id != requirements.project_id:
        return False
    if candidate.workspace_id is not None and candidate.workspace_id != requirements.workspace_id:
        return False
    if requirements.organization_id is not None:
        if candidate.owner_ref.type == "organization":
            return candidate.owner_ref.id == requirements.organization_id
        if candidate.owner_ref.type == "team":
            return True
    return True


def _independence_conflict(
    candidate: AgentMatchCandidate,
    excluded: tuple[AgentParticipantRef, ...],
) -> bool:
    excluded_set = set(excluded)
    if candidate.ref in excluded_set:
        return True
    excluded_agents = {item for item in excluded if isinstance(item, AgentRevisionRef)}
    return bool(excluded_agents.intersection(candidate.member_refs))


def _candidate_exposes_capability(candidate: AgentMatchCandidate, capability_id: str) -> bool:
    return capability_id in _declared_capability_ids(candidate)


def _declared_capability_ids(candidate: AgentMatchCandidate) -> set[str]:
    return set(candidate.allowed_capability_ids).union(
        item.capability_id for item in candidate.capability_constraints
    )


def _capability_requirement_matches(
    requirement: AgentCapabilityRequirement,
    spec: CapabilitySpec,
) -> bool:
    if requirement.exact_version is not None and spec.version != requirement.exact_version:
        return False
    if requirement.minimum_version is not None or requirement.maximum_version is not None:
        key = _numeric_version_key(spec.version)
        if key is None:
            return False
        if requirement.minimum_version is not None:
            minimum = _numeric_version_key(requirement.minimum_version)
            if minimum is None:
                return False
            if key < minimum or (key == minimum and not requirement.include_minimum):
                return False
        if requirement.maximum_version is not None:
            maximum = _numeric_version_key(requirement.maximum_version)
            if maximum is None:
                return False
            if key > maximum or (key == maximum and not requirement.include_maximum):
                return False
    return set(requirement.required_features).issubset(spec.features)


def _capability_constraint_matches(
    constraint: CapabilityConstraint,
    spec: CapabilitySpec,
) -> bool:
    requirement = AgentCapabilityRequirement(
        capability_id=constraint.capability_id,
        exact_version=constraint.exact_version,
        minimum_version=constraint.minimum_version,
        maximum_version=constraint.maximum_version,
        required_features=constraint.required_features,
    )
    return _capability_requirement_matches(requirement, spec)


def _constraints_can_overlap(
    requirement: AgentCapabilityRequirement,
    constraint: CapabilityConstraint,
) -> bool:
    return _constraints_can_jointly_overlap(requirement, (constraint,))


def _constraints_can_jointly_overlap(
    requirement: AgentCapabilityRequirement,
    constraints: tuple[CapabilityConstraint, ...],
) -> bool:
    exact_versions = _joint_exact_versions(requirement, constraints)
    if exact_versions:
        return _exact_versions_overlap(requirement, constraints, exact_versions)
    bounds = _joint_version_bounds(requirement, constraints)
    if bounds is None:
        return False
    lower_bounds, upper_bounds = bounds
    return _version_bounds_overlap(lower_bounds, upper_bounds)


def _joint_exact_versions(
    requirement: AgentCapabilityRequirement,
    constraints: tuple[CapabilityConstraint, ...],
) -> tuple[str, ...]:
    return tuple(
        value
        for value in (
            requirement.exact_version,
            *(constraint.exact_version for constraint in constraints),
        )
        if value is not None
    )


def _exact_versions_overlap(
    requirement: AgentCapabilityRequirement,
    constraints: tuple[CapabilityConstraint, ...],
    exact_versions: tuple[str, ...],
) -> bool:
    if len(set(exact_versions)) != 1:
        return False
    version = exact_versions[0]
    if not _version_in_requirement(version, requirement):
        return False
    return all(_version_in_constraint(version, constraint) for constraint in constraints)


def _joint_version_bounds(
    requirement: AgentCapabilityRequirement,
    constraints: tuple[CapabilityConstraint, ...],
) -> (
    tuple[
        list[tuple[tuple[int, int, int], bool]],
        list[tuple[tuple[int, int, int], bool]],
    ]
    | None
):
    lower_bounds: list[tuple[tuple[int, int, int], bool]] = []
    upper_bounds: list[tuple[tuple[int, int, int], bool]] = []
    if requirement.minimum_version is not None:
        parsed = _numeric_version_key(requirement.minimum_version)
        if parsed is None:
            return None
        lower_bounds.append((parsed, requirement.include_minimum))
    if requirement.maximum_version is not None:
        parsed = _numeric_version_key(requirement.maximum_version)
        if parsed is None:
            return None
        upper_bounds.append((parsed, requirement.include_maximum))
    for constraint in constraints:
        if not _append_constraint_bounds(constraint, lower_bounds, upper_bounds):
            return None
    return lower_bounds, upper_bounds


def _append_constraint_bounds(
    constraint: CapabilityConstraint,
    lower_bounds: list[tuple[tuple[int, int, int], bool]],
    upper_bounds: list[tuple[tuple[int, int, int], bool]],
) -> bool:
    if constraint.minimum_version is not None:
        parsed = _numeric_version_key(constraint.minimum_version)
        if parsed is None:
            return False
        lower_bounds.append((parsed, True))
    if constraint.maximum_version is not None:
        parsed = _numeric_version_key(constraint.maximum_version)
        if parsed is None:
            return False
        upper_bounds.append((parsed, False))
    return True


def _version_bounds_overlap(
    lower_bounds: list[tuple[tuple[int, int, int], bool]],
    upper_bounds: list[tuple[tuple[int, int, int], bool]],
) -> bool:
    if not lower_bounds or not upper_bounds:
        return True
    lower_value = max(value for value, _ in lower_bounds)
    upper_value = min(value for value, _ in upper_bounds)
    if lower_value != upper_value:
        return lower_value < upper_value
    lower_inclusive = all(inclusive for value, inclusive in lower_bounds if value == lower_value)
    upper_inclusive = all(inclusive for value, inclusive in upper_bounds if value == upper_value)
    return lower_inclusive and upper_inclusive


def _version_in_constraint(version: str, constraint: CapabilityConstraint) -> bool:
    requirement = AgentCapabilityRequirement(
        capability_id=constraint.capability_id,
        exact_version=constraint.exact_version,
        minimum_version=constraint.minimum_version,
        maximum_version=constraint.maximum_version,
    )
    return _version_in_requirement(version, requirement)


def _version_in_requirement(version: str, requirement: AgentCapabilityRequirement) -> bool:
    pseudo = _CapabilityVersion(version=version)
    return _capability_requirement_matches(requirement, pseudo)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class _CapabilityVersion:
    version: str
    features: tuple[str, ...] = ()


def _numeric_version_key(value: str | None) -> tuple[int, int, int] | None:
    if value is None:
        return None
    parts = value.split(".")
    if not 1 <= len(parts) <= 3 or any(not part.isdigit() for part in parts):
        return None
    normalized = [int(part) for part in parts]
    normalized.extend([0] * (3 - len(normalized)))
    return tuple(normalized)  # type: ignore[return-value]


def _merge_model_requirements(
    base: RoutingRequirements,
    overlay: RoutingRequirements,
    *,
    overlay_replaces_explicit: bool = False,
) -> RoutingRequirements | None:
    if (
        not overlay_replaces_explicit
        and base.explicit_model_id is not None
        and overlay.explicit_model_id is not None
        and base.explicit_model_id != overlay.explicit_model_id
    ):
        return None
    explicit = (
        overlay.explicit_model_id or base.explicit_model_id
        if overlay_replaces_explicit
        else base.explicit_model_id or overlay.explicit_model_id
    )
    local_only = base.local_only or overlay.local_only
    self_hosted_only = base.self_hosted_only or overlay.self_hosted_only
    if local_only and self_hosted_only:
        return None
    return RoutingRequirements(
        explicit_model_id=explicit,
        min_context_window=max(
            base.min_context_window or 0,
            overlay.min_context_window or 0,
        )
        or None,
        tool_calling=base.tool_calling or overlay.tool_calling,
        structured_output=base.structured_output or overlay.structured_output,
        streaming=base.streaming or overlay.streaming,
        modalities=tuple(dict.fromkeys((*base.modalities, *overlay.modalities))),
        reasoning=tuple(dict.fromkeys((*base.reasoning, *overlay.reasoning))),
        local_only=local_only,
        self_hosted_only=self_hosted_only,
    )


def _model_matches(
    config: ModelConfiguration,
    requirements: RoutingRequirements,
    registry: ModelRegistry,
) -> bool:
    if not config.enabled or registry.effective_health(config) is HealthStatus.UNAVAILABLE:
        return False
    if not _model_identity_and_location_match(config, requirements):
        return False
    return _model_capabilities_match(config, requirements)


def _model_identity_and_location_match(
    config: ModelConfiguration,
    requirements: RoutingRequirements,
) -> bool:
    if requirements.explicit_model_id is not None and (
        config.config_id != requirements.explicit_model_id
        and requirements.explicit_model_id not in config.aliases
    ):
        return False
    if requirements.local_only and config.location is not ModelLocation.LOCAL:
        return False
    if requirements.self_hosted_only and config.location not in {
        ModelLocation.LOCAL,
        ModelLocation.SELF_HOSTED,
    }:
        return False
    return True


def _model_capabilities_match(
    config: ModelConfiguration,
    requirements: RoutingRequirements,
) -> bool:
    capabilities = config.capabilities
    if requirements.min_context_window is not None and (
        capabilities.context_window is None
        or capabilities.context_window < requirements.min_context_window
    ):
        return False
    if requirements.tool_calling and not capabilities.tool_calling:
        return False
    if requirements.structured_output and not capabilities.structured_output:
        return False
    if requirements.streaming and not capabilities.streaming:
        return False
    if requirements.modalities and not set(requirements.modalities).issubset(
        capabilities.modalities
    ):
        return False
    if requirements.reasoning and not set(requirements.reasoning).issubset(capabilities.reasoning):
        return False
    return True


def _candidate_sort_key(candidate: AgentMatchCandidate) -> tuple[str, str, int]:
    return candidate.participant_key


__all__ = ["AgentMatcher"]
