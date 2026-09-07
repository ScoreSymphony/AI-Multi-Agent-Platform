"""Deterministic, fail-closed Skill resolution and reproducible bundle materialization."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from ai_multi_agent_platform.capabilities import (
    CapabilityCompatibilityRequest,
    CapabilityRegistry,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import validate_id
from ai_multi_agent_platform.models import ModelConfiguration, ModelLocation

from .models import (
    SkillBundle,
    SkillBundleEntry,
    SkillCapabilityRequirement,
    SkillRevision,
    SkillRevisionRef,
    SkillRunBinding,
    SkillTrustStatus,
    new_skill_binding_id,
    new_skill_bundle_id,
)
from .repository import SkillRepository

SKILL_RESOLVER_VERSION = "1"
SKILL_RESOLVER_POLICY_VERSION = "1"


@dataclass(frozen=True, slots=True)
class SkillResolutionRequest:
    """Canonical inputs for the minimal effective method set of one Agent execution."""

    run_id: str
    task_id: str
    agent_id: str
    agent_revision: int
    agent_role: str
    required_skills: tuple[SkillRevisionRef, ...] = ()
    explicit_skills: tuple[SkillRevisionRef, ...] = ()
    planner_skills: tuple[SkillRevisionRef, ...] = ()
    default_skills: tuple[SkillRevisionRef, ...] = ()
    allowed_capability_ids: frozenset[str] = frozenset()
    available_capability_versions: Mapping[str, str] | None = None
    granted_permissions: frozenset[str] = frozenset()
    available_worker_capabilities: frozenset[str] = frozenset()
    model_configuration: ModelConfiguration | None = None
    step_id: str | None = None
    project_id: str | None = None
    workspace_id: str | None = None
    allow_deprecated: bool = False

    def __post_init__(self) -> None:
        validate_id(self.run_id, "run")
        validate_id(self.task_id, "task")
        validate_id(self.agent_id, "agent")
        if self.agent_revision < 1:
            raise ValueError("agent_revision must be >= 1")
        if not self.agent_role.strip():
            raise ValueError("agent_role must not be blank")
        if self.step_id is not None:
            validate_id(self.step_id, "step")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.workspace_id is not None:
            validate_id(self.workspace_id, "workspace")
        versions = dict(self.available_capability_versions or {})
        if set(versions) - set(self.allowed_capability_ids):
            raise ValueError("available capability versions must be within allowed_capability_ids")
        if any(not value.strip() for value in versions.values()):
            raise ValueError("available capability versions must not be blank")


class SkillResolver:
    """Resolve only requested/defaulted Skills; installed Skills are never ambient authority."""

    def __init__(
        self,
        repository: SkillRepository,
        *,
        capability_registry: CapabilityRegistry | None = None,
        resolver_version: str = SKILL_RESOLVER_VERSION,
        policy_version: str = SKILL_RESOLVER_POLICY_VERSION,
    ) -> None:
        if not resolver_version.strip() or not policy_version.strip():
            raise ValueError("resolver and policy versions must not be blank")
        self.repository = repository
        self.capability_registry = capability_registry
        self.resolver_version = resolver_version
        self.policy_version = policy_version

    def resolve(self, request: SkillResolutionRequest) -> SkillBundle:
        revisions = self._select_revisions(request)
        capability_versions = self._validate_capabilities(revisions, request)
        entries = tuple(self._entry(revision) for revision in revisions)
        capability_ids = tuple(
            sorted(
                {
                    requirement.capability_id
                    for revision in revisions
                    for requirement in revision.profile.capability_requirements
                }
            )
        )
        digest = self._bundle_digest(
            request=request,
            entries=entries,
            capability_ids=capability_ids,
            capability_versions=capability_versions,
        )
        model = request.model_configuration
        bundle = SkillBundle(
            skill_bundle_id=new_skill_bundle_id(),
            digest=digest,
            entries=entries,
            resolver_version=self.resolver_version,
            policy_version=self.policy_version,
            run_id=request.run_id,
            task_id=request.task_id,
            agent_id=request.agent_id,
            agent_revision=request.agent_revision,
            step_id=request.step_id,
            project_id=request.project_id,
            workspace_id=request.workspace_id,
            capability_ids=capability_ids,
            capability_versions=capability_versions,
            audit_metadata={
                "selection_order": [
                    "agent_required",
                    "task_explicit",
                    "planner",
                    "agent_default",
                ],
                "dependency_expansion": "depth_first_before_dependent",
                "minimal_resolution": True,
                "model_config_id": model.config_id if model is not None else None,
                "model_revision": model.revision if model is not None else None,
            },
        )
        self.repository.save_bundle(bundle)
        return bundle

    def resolve_and_bind(self, request: SkillResolutionRequest) -> tuple[SkillBundle, SkillRunBinding]:
        """Persist immutable pre-execution evidence before an AgentRun is provider-mapped."""

        bundle = self.resolve(request)
        binding = SkillRunBinding(
            binding_id=new_skill_binding_id(),
            run_id=request.run_id,
            task_id=request.task_id,
            agent_id=request.agent_id,
            agent_revision=request.agent_revision,
            skill_bundle_id=bundle.skill_bundle_id,
            skill_bundle_hash=bundle.digest,
        )
        self.repository.create_binding(binding)
        return bundle, binding

    def _select_revisions(self, request: SkillResolutionRequest) -> tuple[SkillRevision, ...]:
        ordered_refs = (
            *request.required_skills,
            *request.explicit_skills,
            *request.planner_skills,
            *request.default_skills,
        )
        selected: list[SkillRevision] = []
        selected_by_id: dict[str, SkillRevisionRef] = {}
        resolved: set[tuple[str, int]] = set()
        visiting: list[tuple[str, int]] = []

        def visit(ref: SkillRevisionRef) -> None:
            key = (ref.skill_id, ref.revision)
            if key in resolved:
                return
            previous = selected_by_id.get(ref.skill_id)
            if previous is not None and previous.revision != ref.revision:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "one Skill Bundle cannot contain multiple revisions of the same Skill",
                    details={
                        "skill_id": ref.skill_id,
                        "first_revision": previous.revision,
                        "second_revision": ref.revision,
                    },
                )
            if key in visiting:
                cycle = [*visiting[visiting.index(key) :], key]
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "Skill dependency cycle detected",
                    details={
                        "cycle": cast(
                            JsonValue,
                            [f"{skill_id}@{revision}" for skill_id, revision in cycle],
                        )
                    },
                )
            try:
                revision = self.repository.get_skill_revision(ref.skill_id, ref.revision)
            except ContractError as exc:
                if exc.code is ErrorCode.NOT_FOUND:
                    raise ContractError(
                        ErrorCode.UNAVAILABLE,
                        f"mandatory Skill revision is unavailable: {ref.skill_id}@{ref.revision}",
                    ) from exc
                raise

            self._validate_revision(revision, request)
            selected_by_id[ref.skill_id] = ref
            visiting.append(key)
            for dependency in revision.profile.dependencies:
                visit(dependency)
            visiting.pop()
            selected.append(revision)
            resolved.add(key)

        for ref in ordered_refs:
            visit(ref)

        self._validate_conflicts(selected)
        return tuple(selected)

    @staticmethod
    def _validate_revision(revision: SkillRevision, request: SkillResolutionRequest) -> None:
        profile = revision.profile
        if not profile.enabled:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"Skill is disabled: {revision.skill_id}@{revision.revision}",
            )
        if profile.deprecated and not request.allow_deprecated:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"Skill is deprecated: {revision.skill_id}@{revision.revision}",
            )
        if profile.trust_status is not SkillTrustStatus.ADOPTED:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                f"Skill is not adopted/trusted: {revision.skill_id}@{revision.revision}",
            )
        if profile.compatible_agent_roles and request.agent_role not in profile.compatible_agent_roles:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Skill is incompatible with the selected Agent role",
                details={"skill_id": revision.skill_id, "agent_role": request.agent_role},
            )
        if revision.project_id is not None and revision.project_id != request.project_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "Skill is outside the Run project scope",
                details={"skill_id": revision.skill_id},
            )
        if revision.workspace_id is not None and revision.workspace_id != request.workspace_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "Skill is outside the Run workspace scope",
                details={"skill_id": revision.skill_id},
            )
        SkillResolver._validate_model_requirements(revision, request.model_configuration)

    @staticmethod
    def _validate_model_requirements(
        revision: SkillRevision,
        model: ModelConfiguration | None,
    ) -> None:
        requirements = revision.profile.routing_requirements
        constrained = any(
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
        if not constrained:
            return
        if model is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "Skill declares model prerequisites but no canonical model context was resolved",
                details={"skill_id": revision.skill_id},
            )
        capabilities = model.capabilities
        mismatches: list[str] = []
        if requirements.explicit_model_id not in {None, model.config_id}:
            mismatches.append("explicit_model_id")
        if requirements.min_context_window is not None:
            if (
                capabilities.context_window is None
                or capabilities.context_window < requirements.min_context_window
            ):
                mismatches.append("min_context_window")
        if requirements.tool_calling and not capabilities.tool_calling:
            mismatches.append("tool_calling")
        if requirements.structured_output and not capabilities.structured_output:
            mismatches.append("structured_output")
        if requirements.streaming and not capabilities.streaming:
            mismatches.append("streaming")
        if not set(requirements.modalities).issubset(capabilities.modalities):
            mismatches.append("modalities")
        if not set(requirements.reasoning).issubset(capabilities.reasoning):
            mismatches.append("reasoning")
        if requirements.local_only and model.location is not ModelLocation.LOCAL:
            mismatches.append("local_only")
        if requirements.self_hosted_only and model.location is not ModelLocation.SELF_HOSTED:
            mismatches.append("self_hosted_only")
        if mismatches:
            raise ContractError(
                ErrorCode.NO_COMPATIBLE_ROUTE,
                "resolved model does not satisfy Skill prerequisites",
                details={
                    "skill_id": revision.skill_id,
                    "model_config_id": model.config_id,
                    "mismatches": cast(JsonValue, mismatches),
                },
            )

    @staticmethod
    def _validate_conflicts(revisions: list[SkillRevision]) -> None:
        selected_ids = {revision.skill_id for revision in revisions}
        conflicts: set[tuple[str, str]] = set()
        for revision in revisions:
            for conflicting_id in revision.profile.conflicts_with_skill_ids:
                if conflicting_id in selected_ids:
                    first, second = sorted((revision.skill_id, conflicting_id))
                    conflicts.add((first, second))
        if conflicts:
            raise ContractError(
                ErrorCode.CONFLICT,
                "resolved Skills have an explicit composition conflict",
                details={
                    "conflicts": cast(JsonValue, [list(pair) for pair in sorted(conflicts)])
                },
            )

    def _validate_capabilities(
        self,
        revisions: tuple[SkillRevision, ...],
        request: SkillResolutionRequest,
    ) -> dict[str, str]:
        resolved_versions: dict[str, str] = {}
        for revision in revisions:
            for requirement in revision.profile.capability_requirements:
                capability_id = requirement.capability_id
                if capability_id not in request.allowed_capability_ids:
                    raise ContractError(
                        ErrorCode.FORBIDDEN,
                        "Skill cannot widen the Agent/Run capability scope",
                        details={
                            "skill_id": revision.skill_id,
                            "capability_id": capability_id,
                        },
                    )
                version = self._resolve_capability(requirement, request)
                if version is not None:
                    previous = resolved_versions.get(capability_id)
                    if previous is not None and previous != version:
                        raise ContractError(
                            ErrorCode.CONFLICT,
                            "Skills resolved incompatible concrete capability versions",
                            details={"capability_id": capability_id},
                        )
                    resolved_versions[capability_id] = version
        return resolved_versions

    def _resolve_capability(
        self,
        requirement: SkillCapabilityRequirement,
        request: SkillResolutionRequest,
    ) -> str | None:
        if self.capability_registry is not None:
            compatibility: CapabilityCompatibilityRequest | None = None
            if (
                requirement.minimum_version is not None
                or requirement.maximum_version is not None
                or requirement.required_features
            ):
                compatibility = CapabilityCompatibilityRequest(
                    minimum_version=requirement.minimum_version,
                    maximum_version=requirement.maximum_version,
                    required_features=requirement.required_features,
                )
            registration, _provider = self.capability_registry.resolve(
                requirement.capability_id,
                version=requirement.exact_version,
                compatibility=compatibility,
                granted_permissions=request.granted_permissions,
                available_worker_capabilities=request.available_worker_capabilities,
            )
            return registration.capability.version

        versions = dict(request.available_capability_versions or {})
        available = versions.get(requirement.capability_id)
        if requirement.exact_version is not None:
            if available != requirement.exact_version:
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "required Skill capability version is unavailable",
                    details={
                        "capability_id": requirement.capability_id,
                        "required_version": requirement.exact_version,
                        "available_version": available,
                    },
                )
            return available
        if (
            requirement.minimum_version is not None
            or requirement.maximum_version is not None
            or requirement.required_features
        ):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Skill capability compatibility constraints require a CapabilityRegistry",
                details={"capability_id": requirement.capability_id},
            )
        return available

    @staticmethod
    def _entry(revision: SkillRevision) -> SkillBundleEntry:
        source = revision.profile.source
        return SkillBundleEntry(
            ref=revision.ref,
            content_digest=_sha256(_revision_payload(revision)),
            trust_status=revision.profile.trust_status,
            source_revision=source.source_revision if source is not None else None,
            source_checksum=source.checksum if source is not None else None,
        )

    def _bundle_digest(
        self,
        *,
        request: SkillResolutionRequest,
        entries: tuple[SkillBundleEntry, ...],
        capability_ids: tuple[str, ...],
        capability_versions: Mapping[str, str],
    ) -> str:
        model = request.model_configuration
        payload: dict[str, object] = {
            "resolver_version": self.resolver_version,
            "policy_version": self.policy_version,
            "context": {
                "run_id": request.run_id,
                "task_id": request.task_id,
                "agent_id": request.agent_id,
                "agent_revision": request.agent_revision,
                "agent_role": request.agent_role,
                "step_id": request.step_id,
                "project_id": request.project_id,
                "workspace_id": request.workspace_id,
                "model_config_id": model.config_id if model is not None else None,
                "model_revision": model.revision if model is not None else None,
            },
            "entries": [
                {
                    "skill_id": entry.ref.skill_id,
                    "revision": entry.ref.revision,
                    "content_digest": entry.content_digest,
                    "trust_status": entry.trust_status.value,
                    "source_revision": entry.source_revision,
                    "source_checksum": entry.source_checksum,
                }
                for entry in entries
            ],
            "capability_ids": list(capability_ids),
            "capability_versions": dict(sorted(capability_versions.items())),
        }
        return _sha256(payload)


def _revision_payload(revision: SkillRevision) -> dict[str, object]:
    profile = revision.profile
    routing = profile.routing_requirements
    source = profile.source
    return {
        "skill_id": revision.skill_id,
        "revision": revision.revision,
        "scope": {
            "project_id": revision.project_id,
            "workspace_id": revision.workspace_id,
        },
        "profile": {
            "name": profile.name,
            "description": profile.description,
            "purpose_categories": list(profile.purpose_categories),
            "content": {
                "content": profile.content.content,
                "ref": profile.content.ref,
                "version": profile.content.version,
            },
            "dependencies": [
                {"skill_id": item.skill_id, "revision": item.revision}
                for item in profile.dependencies
            ],
            "capability_requirements": [
                {
                    "capability_id": item.capability_id,
                    "exact_version": item.exact_version,
                    "minimum_version": item.minimum_version,
                    "maximum_version": item.maximum_version,
                    "required_features": list(item.required_features),
                }
                for item in profile.capability_requirements
            ],
            "compatible_agent_roles": list(profile.compatible_agent_roles),
            "routing_requirements": {
                "explicit_model_id": routing.explicit_model_id,
                "min_context_window": routing.min_context_window,
                "tool_calling": routing.tool_calling,
                "structured_output": routing.structured_output,
                "streaming": routing.streaming,
                "modalities": list(routing.modalities),
                "reasoning": list(routing.reasoning),
                "local_only": routing.local_only,
                "self_hosted_only": routing.self_hosted_only,
            },
            "expected_inputs": list(profile.expected_inputs),
            "expected_outputs": list(profile.expected_outputs),
            "workspace_assumptions": list(profile.workspace_assumptions),
            "side_effects": list(profile.side_effects),
            "conflicts_with_skill_ids": list(profile.conflicts_with_skill_ids),
            "risk_level": profile.risk_level.value,
            "trust_status": profile.trust_status.value,
            "evaluation_status": profile.evaluation_status.value,
            "evaluation_metadata": dict(profile.evaluation_metadata),
            "source": None
            if source is None
            else {
                "source_url": source.source_url,
                "source_revision": source.source_revision,
                "license": source.license,
                "checksum": source.checksum,
                "signature": source.signature,
                "requested_capability_ids": list(source.requested_capability_ids),
                "filesystem_implications": list(source.filesystem_implications),
                "network_implications": list(source.network_implications),
                "embedded_hook_refs": list(source.embedded_hook_refs),
                "cost_implications": source.cost_implications,
            },
            "enabled": profile.enabled,
            "deprecated": profile.deprecated,
            "replacement": None
            if profile.replacement is None
            else {
                "skill_id": profile.replacement.skill_id,
                "revision": profile.replacement.revision,
            },
            "metadata": dict(profile.metadata),
        },
    }


def _sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
