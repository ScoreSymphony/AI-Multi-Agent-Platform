"""Owner-domain promotion adapters for governed learning candidates (#595)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Protocol, cast

from ai_multi_agent_platform.agents import AgentService, InstructionSource
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import Provenance
from ai_multi_agent_platform.models import (
    ModelRoutingProfileRef,
    ModelRoutingProfileService,
    RoutingProfileFallbackPolicy,
    RoutingRequirements,
)
from ai_multi_agent_platform.skills import SkillContent, SkillService

from .models import LearningCandidate, LearningTargetType, PromotionReceipt


class OwnerPromotionAdapter(Protocol):
    target_type: LearningTargetType

    async def promote(
        self,
        candidate: LearningCandidate,
        *,
        principal_ref: str,
        context: OperationContext,
        actor_type: str | None = None,
    ) -> PromotionReceipt: ...


class PromotionRegistry:
    """Explicit target-owner registry. Unsupported owner domains remain unsupported."""

    def __init__(self, adapters: tuple[OwnerPromotionAdapter, ...] = ()) -> None:
        self._adapters: dict[LearningTargetType, OwnerPromotionAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: OwnerPromotionAdapter) -> None:
        if adapter.target_type in self._adapters:
            raise ValueError(f"duplicate learning promotion adapter: {adapter.target_type.value}")
        self._adapters[adapter.target_type] = adapter

    def resolve(self, target_type: LearningTargetType) -> OwnerPromotionAdapter:
        try:
            return self._adapters[target_type]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"learning promotion target is not supported: {target_type.value}",
            ) from exc


class AgentPromotionAdapter:
    target_type = LearningTargetType.AGENT
    _SUPPORTED_KEYS = frozenset({"description", "role_instruction", "enabled", "metadata_patch"})

    def __init__(self, service: AgentService) -> None:
        self.service = service

    async def promote(
        self,
        candidate: LearningCandidate,
        *,
        principal_ref: str,
        context: OperationContext,
        actor_type: str | None = None,
    ) -> PromotionReceipt:
        del context, actor_type
        _require_target(candidate, self.target_type)
        current = self.service.get_agent_revision(candidate.target.resource_id)
        recovered = _recover_receipt(current.revision, current.provenance, candidate)
        if recovered is not None:
            return recovered
        _require_fresh(candidate, current.revision)
        change = dict(candidate.proposed_change)
        _require_supported_keys(change, self._SUPPORTED_KEYS)
        profile = current.profile
        if "description" in change:
            profile = replace(profile, description=_string(change["description"], "description"))
        if "role_instruction" in change:
            profile = replace(
                profile,
                instructions=replace(
                    profile.instructions,
                    role=InstructionSource(
                        content=_string(change["role_instruction"], "role_instruction")
                    ),
                ),
            )
        if "enabled" in change:
            profile = replace(profile, enabled=_boolean(change["enabled"], "enabled"))
        if "metadata_patch" in change:
            patch = _mapping(change["metadata_patch"], "metadata_patch")
            profile = replace(profile, metadata={**dict(profile.metadata), **patch})
        if profile == current.profile:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "learning candidate does not change the target Agent revision",
            )
        revision = self.service.update_agent(
            current.agent_id,
            profile,
            expected_revision=current.revision,
            provenance=_promotion_provenance(candidate, principal_ref),
        )
        return PromotionReceipt(
            target_type=self.target_type,
            target_id=revision.agent_id,
            previous_revision=current.revision,
            new_revision=revision.revision,
            canonical_ref=f"{revision.agent_id}@r{revision.revision}",
            candidate_digest=candidate.content_digest,
        )


class SkillPromotionAdapter:
    target_type = LearningTargetType.SKILL
    _SUPPORTED_KEYS = frozenset(
        {
            "description",
            "content",
            "enabled",
            "expected_inputs",
            "expected_outputs",
            "metadata_patch",
        }
    )

    def __init__(self, service: SkillService) -> None:
        self.service = service

    async def promote(
        self,
        candidate: LearningCandidate,
        *,
        principal_ref: str,
        context: OperationContext,
        actor_type: str | None = None,
    ) -> PromotionReceipt:
        del context, actor_type
        _require_target(candidate, self.target_type)
        current = self.service.get_skill_revision(candidate.target.resource_id)
        recovered = _recover_receipt(current.revision, current.provenance, candidate)
        if recovered is not None:
            return recovered
        _require_fresh(candidate, current.revision)
        change = dict(candidate.proposed_change)
        _require_supported_keys(change, self._SUPPORTED_KEYS)
        profile = current.profile
        if "description" in change:
            profile = replace(profile, description=_string(change["description"], "description"))
        if "content" in change:
            profile = replace(
                profile,
                content=SkillContent(content=_string(change["content"], "content")),
            )
        if "enabled" in change:
            profile = replace(profile, enabled=_boolean(change["enabled"], "enabled"))
        if "expected_inputs" in change:
            profile = replace(
                profile,
                expected_inputs=_string_tuple(change["expected_inputs"], "expected_inputs"),
            )
        if "expected_outputs" in change:
            profile = replace(
                profile,
                expected_outputs=_string_tuple(change["expected_outputs"], "expected_outputs"),
            )
        if "metadata_patch" in change:
            patch = _mapping(change["metadata_patch"], "metadata_patch")
            profile = replace(profile, metadata={**dict(profile.metadata), **patch})
        if profile == current.profile:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "learning candidate does not change the target Skill revision",
            )
        revision = self.service.update_skill(
            current.skill_id,
            profile,
            expected_revision=current.revision,
            project_id=current.project_id,
            workspace_id=current.workspace_id,
            provenance=_promotion_provenance(candidate, principal_ref),
        )
        return PromotionReceipt(
            target_type=self.target_type,
            target_id=revision.skill_id,
            previous_revision=current.revision,
            new_revision=revision.revision,
            canonical_ref=f"{revision.skill_id}@r{revision.revision}",
            candidate_digest=candidate.content_digest,
        )


class RoutingProfilePromotionAdapter:
    target_type = LearningTargetType.MODEL_ROUTING_PROFILE
    _SUPPORTED_KEYS = frozenset(
        {"name", "description", "preferred_model_ids", "fallback", "requirements"}
    )

    def __init__(self, service: ModelRoutingProfileService) -> None:
        self.service = service

    async def promote(
        self,
        candidate: LearningCandidate,
        *,
        principal_ref: str,
        context: OperationContext,
        actor_type: str | None = None,
    ) -> PromotionReceipt:
        _require_target(candidate, self.target_type)
        definition = self.service.repository.get_definition(candidate.target.resource_id)
        current = self.service.repository.get_revision(
            ModelRoutingProfileRef(definition.profile_id, definition.current_revision)
        )
        recovered = _recover_receipt(current.revision, current.provenance, candidate)
        if recovered is not None:
            return recovered
        _require_fresh(candidate, current.revision)
        change = dict(candidate.proposed_change)
        _require_supported_keys(change, self._SUPPORTED_KEYS)
        name = current.name
        description = current.description
        policy = current.policy
        if "name" in change:
            name = _string(change["name"], "name")
        if "description" in change:
            description = _string(change["description"], "description")
        if "preferred_model_ids" in change:
            policy = replace(
                policy,
                preferred_model_ids=_string_tuple(
                    change["preferred_model_ids"], "preferred_model_ids"
                ),
            )
        if "fallback" in change:
            policy = replace(
                policy,
                fallback=RoutingProfileFallbackPolicy(_string(change["fallback"], "fallback")),
            )
        if "requirements" in change:
            policy = replace(
                policy,
                requirements=_routing_requirements(
                    _mapping(change["requirements"], "requirements"),
                    policy.requirements,
                ),
            )
        if name == current.name and description == current.description and policy == current.policy:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "learning candidate does not change the target routing profile revision",
            )
        revision = await self.service.version_profile(
            current.profile_id,
            name=name,
            description=description,
            policy=policy,
            principal_ref=principal_ref,
            context=context,
            actor_type=actor_type,
            expected_revision=current.revision,
            provenance=_promotion_provenance(candidate, principal_ref),
        )
        return PromotionReceipt(
            target_type=self.target_type,
            target_id=revision.profile_id,
            previous_revision=current.revision,
            new_revision=revision.revision,
            canonical_ref=revision.ref.canonical_ref,
            candidate_digest=candidate.content_digest,
        )


def _promotion_provenance(candidate: LearningCandidate, principal_ref: str) -> Provenance:
    return Provenance(
        source="learning_candidate",
        actor_ref=principal_ref,
        details={
            "learning_candidate_id": candidate.learning_candidate_id,
            "learning_candidate_revision": candidate.revision,
            "learning_candidate_digest": candidate.content_digest,
            "target_base_revision": candidate.target.revision,
        },
    )


def _recover_receipt(
    current_revision: int,
    provenance: Provenance | None,
    candidate: LearningCandidate,
) -> PromotionReceipt | None:
    if current_revision <= candidate.target.revision:
        return None
    details = {} if provenance is None else dict(provenance.details)
    if (
        current_revision == candidate.target.revision + 1
        and provenance is not None
        and provenance.source == "learning_candidate"
        and details.get("learning_candidate_id") == candidate.learning_candidate_id
        and details.get("learning_candidate_digest") == candidate.content_digest
    ):
        return PromotionReceipt(
            target_type=candidate.target.resource_type,
            target_id=candidate.target.resource_id,
            previous_revision=candidate.target.revision,
            new_revision=current_revision,
            canonical_ref=f"{candidate.target.resource_id}@r{current_revision}",
            candidate_digest=candidate.content_digest,
            already_applied=True,
        )
    raise ContractError(
        ErrorCode.CONFLICT,
        "learning candidate target is stale",
        details={
            "target_revision": candidate.target.revision,
            "current_revision": current_revision,
        },
    )


def _require_fresh(candidate: LearningCandidate, current_revision: int) -> None:
    if current_revision != candidate.target.revision:
        raise ContractError(
            ErrorCode.CONFLICT,
            "learning candidate target is stale",
            details={
                "target_revision": candidate.target.revision,
                "current_revision": current_revision,
            },
        )


def _require_target(candidate: LearningCandidate, target_type: LearningTargetType) -> None:
    if candidate.target.resource_type is not target_type:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "learning promotion adapter received the wrong target type",
        )


def _require_supported_keys(
    change: Mapping[str, JsonValue],
    supported: frozenset[str],
) -> None:
    unsupported = sorted(set(change) - supported)
    if unsupported:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "learning proposal contains owner-domain fields not supported by this adapter",
            details={"unsupported_fields": cast(JsonValue, unsupported)},
        )


def _string(value: JsonValue, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be a non-blank string")
    return value


def _boolean(value: JsonValue, name: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be a boolean")
    return value


def _string_tuple(value: JsonValue, name: str) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be a list of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"{name} must contain only non-blank strings",
            )
        result.append(item)
    if len(result) != len(set(result)):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} values must be unique")
    return tuple(result)


def _mapping(value: JsonValue, name: str) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be an object")
    result: dict[str, JsonValue] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"{name} keys must be non-blank strings",
            )
        result[key] = cast(JsonValue, item)
    return result


def _routing_requirements(
    patch: Mapping[str, JsonValue],
    current: RoutingRequirements,
) -> RoutingRequirements:
    supported = frozenset(
        {
            "explicit_model_id",
            "min_context_window",
            "tool_calling",
            "structured_output",
            "streaming",
            "modalities",
            "reasoning",
            "local_only",
            "self_hosted_only",
        }
    )
    _require_supported_keys(patch, supported)
    values: dict[str, Any] = {
        "explicit_model_id": current.explicit_model_id,
        "min_context_window": current.min_context_window,
        "tool_calling": current.tool_calling,
        "structured_output": current.structured_output,
        "streaming": current.streaming,
        "modalities": current.modalities,
        "reasoning": current.reasoning,
        "local_only": current.local_only,
        "self_hosted_only": current.self_hosted_only,
    }
    for key, value in patch.items():
        if key in {
            "tool_calling",
            "structured_output",
            "streaming",
            "local_only",
            "self_hosted_only",
        }:
            values[key] = _boolean(value, key)
        elif key in {"modalities", "reasoning"}:
            values[key] = _string_tuple(value, key)
        elif key == "explicit_model_id":
            values[key] = None if value is None else _string(value, key)
        elif key == "min_context_window":
            if value is None:
                values[key] = None
            elif isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "min_context_window must be a positive integer or null",
                )
            else:
                values[key] = value
    return RoutingRequirements(**values)
