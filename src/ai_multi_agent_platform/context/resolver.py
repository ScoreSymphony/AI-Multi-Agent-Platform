"""Deterministic, authorization-aware context resolver for issue #590."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Protocol, cast

from ai_multi_agent_platform.contracts import (
    AuthorizationOutcome,
    AuthorizationProvider,
    AuthorizationRequest,
    OperationContext,
    normalize_authorization_decision,
)
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import validate_id
from ai_multi_agent_platform.security.authorization import ActorIdentity

from .models import (
    ContextBudget,
    ContextBudgetUsage,
    ContextBundle,
    ContextCandidate,
    ContextConflictPolicy,
    ContextDataClassification,
    ContextEntry,
    ContextEntryRole,
    ContextFreshness,
    ContextOmission,
    ContextOmissionReason,
    ContextSourceRef,
    ContextSourceType,
    ContextTransformation,
    ContextTransformationKind,
    new_context_bundle_id,
)

RESOLVER_VERSION = "context-resolver/v1"


class ContextBlockerReason(StrEnum):
    MANDATORY_UNAUTHORIZED = "mandatory_unauthorized"
    MANDATORY_APPROVAL_REQUIRED = "mandatory_approval_required"
    MANDATORY_STALE = "mandatory_stale"
    MANDATORY_UNAVAILABLE = "mandatory_unavailable"
    MANDATORY_CLASSIFICATION = "mandatory_classification"
    MANDATORY_BUDGET = "mandatory_budget"
    PROJECT_SCOPE_MISMATCH = "project_scope_mismatch"
    WORKSPACE_SCOPE_MISMATCH = "workspace_scope_mismatch"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class ContextResolutionBlocker:
    reason: ContextBlockerReason
    source: ContextSourceRef
    detail: str

    def __post_init__(self) -> None:
        if not self.detail.strip():
            raise ValueError("context blocker detail must not be blank")


class ContextResolutionError(RuntimeError):
    """Fail-closed resolution error with canonical blocker evidence."""

    def __init__(self, blocker: ContextResolutionBlocker) -> None:
        super().__init__(f"{blocker.reason.value}: {blocker.detail}")
        self.blocker = blocker


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    version: str = "context-policy/v1"
    allowed_classifications: tuple[ContextDataClassification, ...] = (
        ContextDataClassification.PUBLIC,
        ContextDataClassification.INTERNAL,
        ContextDataClassification.CONFIDENTIAL,
        ContextDataClassification.RESTRICTED,
        ContextDataClassification.SECRET_REFERENCE,
    )
    reject_stale_mandatory: bool = True
    conflict_policy: ContextConflictPolicy = ContextConflictPolicy.PRESERVE
    allow_optional_truncation: bool = True
    minimum_truncated_bytes: int = 32
    truncation_policy_version: str = "utf8-prefix-truncate/v1"
    estimator_id: str = "utf8-bytes-div4-v1"

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("context policy version must not be blank")
        if not self.truncation_policy_version.strip():
            raise ValueError("context policy version must not be blank")
        if not self.estimator_id.strip():
            raise ValueError("context estimator ID must not be blank")
        if self.minimum_truncated_bytes < 1:
            raise ValueError("minimum_truncated_bytes must be >= 1")
        if len(set(self.allowed_classifications)) != len(self.allowed_classifications):
            raise ValueError("allowed context classifications must be unique")


@dataclass(frozen=True, slots=True)
class ContextAssemblyRequest:
    task_id: str
    run_id: str
    agent_id: str
    agent_revision: int
    actor: ActorIdentity
    operation: OperationContext
    candidates: tuple[ContextCandidate, ...]
    budget: ContextBudget
    policy: ContextPolicy = field(default_factory=ContextPolicy)
    workspace_id: str | None = None
    plan_id: str | None = None
    step_id: str | None = None
    skill_bundle_id: str | None = None
    skill_bundle_digest: str | None = None

    def __post_init__(self) -> None:
        validate_id(self.task_id, "task")
        validate_id(self.run_id, "run")
        validate_id(self.agent_id, "agent")
        if self.agent_revision < 1:
            raise ValueError("context assembly agent_revision must be >= 1")
        if self.workspace_id is not None and not self.workspace_id.strip():
            raise ValueError("context assembly workspace_id must not be blank")
        if (self.skill_bundle_id is None) != (self.skill_bundle_digest is None):
            raise ValueError("skill bundle ID and digest must be supplied together")
        object.__setattr__(self, "candidates", tuple(self.candidates))


@dataclass(frozen=True, slots=True)
class ContextSourceRequest:
    """Provider-neutral inventory request for source adapters."""

    task_id: str
    run_id: str
    agent_id: str
    agent_revision: int
    project_id: str | None
    workspace_id: str | None
    plan_id: str | None = None
    step_id: str | None = None


class ContextSourceAdapter(Protocol):
    """A source domain contributes candidates; it never owns assembly truth."""

    @property
    def adapter_id(self) -> str: ...

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]: ...


@dataclass(frozen=True, slots=True)
class StaticContextSourceAdapter:
    """Deterministic fixture/reference adapter useful for local operation and tests."""

    adapter_id: str
    candidates: tuple[ContextCandidate, ...]

    def __post_init__(self) -> None:
        if not self.adapter_id.strip():
            raise ValueError("context source adapter ID must not be blank")
        object.__setattr__(self, "candidates", tuple(self.candidates))

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        del request
        return self.candidates


_RESOURCE_TYPES: Mapping[ContextSourceType, str] = {
    ContextSourceType.SYSTEM_SECURITY: "administrative_settings",
    ContextSourceType.TASK: "task",
    ContextSourceType.PLAN_STEP: "task",
    ContextSourceType.AGENT: "agent",
    ContextSourceType.SKILL: "generic",
    ContextSourceType.MEMORY: "memory",
    ContextSourceType.KNOWLEDGE: "knowledge_source",
    ContextSourceType.RESEARCH_EVIDENCE: "generic",
    ContextSourceType.REPOSITORY: "workspace",
    ContextSourceType.FILE: "file",
    ContextSourceType.ARTIFACT: "artifact",
    ContextSourceType.RESULT: "generic",
    ContextSourceType.PRIOR_RUN: "run",
    ContextSourceType.VERIFICATION: "generic",
    ContextSourceType.HUMAN: "generic",
}

_ROLE_ORDER: Mapping[ContextEntryRole, int] = {
    ContextEntryRole.SECURITY: 0,
    ContextEntryRole.INSTRUCTION: 1,
    ContextEntryRole.CONTEXT: 2,
    ContextEntryRole.EVIDENCE: 3,
}

_SOURCE_ORDER: Mapping[ContextSourceType, int] = {
    ContextSourceType.SYSTEM_SECURITY: 0,
    ContextSourceType.TASK: 1,
    ContextSourceType.PLAN_STEP: 2,
    ContextSourceType.AGENT: 3,
    ContextSourceType.SKILL: 4,
    ContextSourceType.HUMAN: 5,
    ContextSourceType.MEMORY: 6,
    ContextSourceType.KNOWLEDGE: 7,
    ContextSourceType.RESEARCH_EVIDENCE: 8,
    ContextSourceType.REPOSITORY: 9,
    ContextSourceType.FILE: 10,
    ContextSourceType.ARTIFACT: 11,
    ContextSourceType.RESULT: 12,
    ContextSourceType.PRIOR_RUN: 13,
    ContextSourceType.VERIFICATION: 14,
}


class ContextResolver:
    """Canonical resolver: authorization -> filtering -> ranking -> budgeting -> bundle."""

    def __init__(self, authorization: AuthorizationProvider) -> None:
        self.authorization = authorization

    async def resolve(self, request: ContextAssemblyRequest) -> ContextBundle:
        authorized: list[ContextCandidate] = []
        omissions: list[ContextOmission] = []

        for candidate in request.candidates:
            scope_omission = self._scope_filter(request, candidate)
            if scope_omission is not None:
                if candidate.mandatory:
                    raise ContextResolutionError(
                        ContextResolutionBlocker(
                            reason=(
                                ContextBlockerReason.PROJECT_SCOPE_MISMATCH
                                if scope_omission.reason
                                is ContextOmissionReason.PROJECT_SCOPE_MISMATCH
                                else ContextBlockerReason.WORKSPACE_SCOPE_MISMATCH
                            ),
                            source=candidate.source,
                            detail=scope_omission.detail,
                        )
                    )
                omissions.append(scope_omission)
                continue

            if candidate.data_classification not in request.policy.allowed_classifications:
                omission = self._omission(
                    candidate,
                    ContextOmissionReason.CLASSIFICATION,
                    "data classification is excluded by context policy",
                )
                if candidate.mandatory:
                    raise ContextResolutionError(
                        ContextResolutionBlocker(
                            ContextBlockerReason.MANDATORY_CLASSIFICATION,
                            candidate.source,
                            omission.detail,
                        )
                    )
                omissions.append(omission)
                continue

            if candidate.freshness is ContextFreshness.STALE:
                if candidate.mandatory and request.policy.reject_stale_mandatory:
                    raise ContextResolutionError(
                        ContextResolutionBlocker(
                            ContextBlockerReason.MANDATORY_STALE,
                            candidate.source,
                            "mandatory context source is stale",
                        )
                    )
                if not candidate.mandatory:
                    omissions.append(
                        self._omission(
                            candidate,
                            ContextOmissionReason.STALE,
                            "stale optional context excluded by resolver policy",
                        )
                    )
                    continue
            if candidate.freshness is ContextFreshness.UNAVAILABLE:
                if candidate.mandatory:
                    raise ContextResolutionError(
                        ContextResolutionBlocker(
                            ContextBlockerReason.MANDATORY_UNAVAILABLE,
                            candidate.source,
                            "mandatory context source is unavailable",
                        )
                    )
                omissions.append(
                    self._omission(
                        candidate,
                        ContextOmissionReason.UNAVAILABLE,
                        "unavailable context source excluded",
                    )
                )
                continue

            decision = normalize_authorization_decision(
                await self.authorization.authorize(self._authorization_request(request, candidate))
            )
            if decision.outcome is not AuthorizationOutcome.ALLOW:
                reason = (
                    ContextOmissionReason.APPROVAL_REQUIRED
                    if decision.outcome is AuthorizationOutcome.REQUIRE_APPROVAL
                    else ContextOmissionReason.UNAUTHORIZED
                )
                detail = decision.reason or (
                    "context source requires approval"
                    if reason is ContextOmissionReason.APPROVAL_REQUIRED
                    else "context source is unauthorized"
                )
                if candidate.mandatory:
                    blocker_reason = (
                        ContextBlockerReason.MANDATORY_APPROVAL_REQUIRED
                        if reason is ContextOmissionReason.APPROVAL_REQUIRED
                        else ContextBlockerReason.MANDATORY_UNAUTHORIZED
                    )
                    raise ContextResolutionError(
                        ContextResolutionBlocker(blocker_reason, candidate.source, detail)
                    )
                omissions.append(self._omission(candidate, reason, detail))
                continue
            authorized.append(candidate)

        deduplicated, duplicate_omissions = self._deduplicate(authorized)
        omissions.extend(duplicate_omissions)
        self._check_conflicts(deduplicated, request.policy)

        ordered = sorted(deduplicated, key=self._sort_key)
        entries, budget_omissions = self._apply_budget(ordered, request.budget, request.policy)
        omissions.extend(budget_omissions)

        final_entries = tuple(replace(entry, ordinal=index) for index, entry in enumerate(entries))
        usage = ContextBudgetUsage(
            estimated_tokens=sum(item.estimated_tokens for item in final_entries),
            bytes=sum(item.content_bytes for item in final_entries),
            items=len(final_entries),
            estimator_id=request.policy.estimator_id,
        )
        reproducibility_limited = any(
            item.source.revision is None
            and item.source.digest is None
            and item.source.snapshot_id is None
            for item in final_entries
        )

        return ContextBundle(
            context_bundle_id=new_context_bundle_id(),
            task_id=request.task_id,
            run_id=request.run_id,
            agent_id=request.agent_id,
            agent_revision=request.agent_revision,
            entries=final_entries,
            omissions=tuple(sorted(omissions, key=self._omission_sort_key)),
            budget=request.budget,
            usage=usage,
            resolver_version=RESOLVER_VERSION,
            policy_version=request.policy.version,
            actor_ref=request.actor.actor_id,
            plan_id=request.plan_id,
            step_id=request.step_id,
            skill_bundle_id=request.skill_bundle_id,
            skill_bundle_digest=request.skill_bundle_digest,
            reproducibility_limited=reproducibility_limited,
        )

    def _authorization_request(
        self,
        request: ContextAssemblyRequest,
        candidate: ContextCandidate,
    ) -> AuthorizationRequest:
        return AuthorizationRequest(
            principal_ref=request.actor.actor_id,
            actor_type=request.actor.actor_type.value,
            action="read",
            resource_type=_RESOURCE_TYPES[candidate.source.source_type],
            resource_ref=candidate.source.source_id,
            context=request.operation,
            organization_id=request.actor.organization_id,
            workspace_id=candidate.workspace_id or request.workspace_id,
            task_id=request.task_id,
            run_id=request.run_id,
            agent_id=request.agent_id,
            security_labels=candidate.security_labels,
            trust_context={
                "context_source_type": candidate.source.source_type.value,
                "context_role": candidate.role.value,
                "context_mandatory": candidate.mandatory,
                "data_classification": candidate.data_classification.value,
            },
        )

    @staticmethod
    def _scope_filter(
        request: ContextAssemblyRequest,
        candidate: ContextCandidate,
    ) -> ContextOmission | None:
        project_id = request.operation.project_id
        if candidate.project_id is not None and candidate.project_id != project_id:
            return ContextResolver._omission(
                candidate,
                ContextOmissionReason.PROJECT_SCOPE_MISMATCH,
                "candidate project scope does not match assembly project scope",
            )
        if candidate.workspace_id is not None and candidate.workspace_id != request.workspace_id:
            return ContextResolver._omission(
                candidate,
                ContextOmissionReason.WORKSPACE_SCOPE_MISMATCH,
                "candidate workspace scope does not match assembly workspace scope",
            )
        return None

    @staticmethod
    def _deduplicate(
        candidates: Sequence[ContextCandidate],
    ) -> tuple[list[ContextCandidate], list[ContextOmission]]:
        by_key: dict[tuple[str, str, str, str, str, str], ContextCandidate] = {}
        omissions: list[ContextOmission] = []
        for candidate in sorted(candidates, key=ContextResolver._sort_key):
            existing = by_key.get(candidate.deduplication_key)
            if existing is None:
                by_key[candidate.deduplication_key] = candidate
                continue
            keep = existing
            drop = candidate
            if candidate.mandatory and not existing.mandatory:
                keep, drop = candidate, existing
                by_key[candidate.deduplication_key] = candidate
            omissions.append(
                ContextResolver._omission(
                    drop,
                    ContextOmissionReason.DUPLICATE,
                    f"duplicate of retained source {keep.source.source_id}",
                )
            )
        return list(by_key.values()), omissions

    @staticmethod
    def _check_conflicts(
        candidates: Sequence[ContextCandidate],
        policy: ContextPolicy,
    ) -> None:
        if policy.conflict_policy is ContextConflictPolicy.PRESERVE:
            return
        groups: dict[str, set[str]] = {}
        sources: dict[str, ContextSourceRef] = {}
        for candidate in candidates:
            if candidate.conflict_key is None:
                continue
            groups.setdefault(candidate.conflict_key, set()).add(
                cast(str, candidate.content_digest)
            )
            sources.setdefault(candidate.conflict_key, candidate.source)
        for key, digests in groups.items():
            if len(digests) > 1:
                raise ContextResolutionError(
                    ContextResolutionBlocker(
                        ContextBlockerReason.CONFLICT,
                        sources[key],
                        f"conflicting context candidates for key {key!r}",
                    )
                )

    @staticmethod
    def _sort_key(candidate: ContextCandidate) -> tuple[object, ...]:
        return (
            0 if candidate.mandatory else 1,
            _ROLE_ORDER[candidate.role],
            _SOURCE_ORDER[candidate.source.source_type],
            -candidate.priority,
            -candidate.relevance,
            candidate.source.canonical_key,
            cast(str, candidate.content_digest),
        )

    @staticmethod
    def _omission_sort_key(omission: ContextOmission) -> tuple[object, ...]:
        return (
            omission.reason.value,
            omission.source.canonical_key,
            omission.content_digest or "",
        )

    @staticmethod
    def _estimate_tokens(candidate: ContextCandidate, content_bytes: int) -> int:
        if candidate.estimated_tokens is not None:
            return candidate.estimated_tokens
        return max(1, math.ceil(content_bytes / 4))

    @classmethod
    def _entry(cls, candidate: ContextCandidate) -> ContextEntry:
        content_bytes_value = candidate.metadata.get("content_bytes")
        content_bytes = (
            len(candidate.inline_content.encode("utf-8"))
            if candidate.inline_content is not None
            else int(content_bytes_value)
            if isinstance(content_bytes_value, int) and not isinstance(content_bytes_value, bool)
            else 0
        )
        estimated_tokens = cls._estimate_tokens(candidate, content_bytes)
        return ContextEntry(
            ordinal=0,
            source=candidate.source,
            role=candidate.role,
            selection_reason=candidate.selection_reason,
            mandatory=candidate.mandatory,
            content_digest=cast(str, candidate.content_digest),
            inline_content=candidate.inline_content,
            content_ref=candidate.content_ref,
            freshness=candidate.freshness,
            trust=candidate.trust,
            data_classification=candidate.data_classification,
            priority=candidate.priority,
            relevance=candidate.relevance,
            estimated_tokens=estimated_tokens,
            content_bytes=content_bytes,
            project_id=candidate.project_id,
            workspace_id=candidate.workspace_id,
            conflict_key=candidate.conflict_key,
            security_labels=candidate.security_labels,
            metadata=candidate.metadata,
        )

    @classmethod
    def _apply_budget(
        cls,
        candidates: Sequence[ContextCandidate],
        budget: ContextBudget,
        policy: ContextPolicy,
    ) -> tuple[list[ContextEntry], list[ContextOmission]]:
        entries: list[ContextEntry] = []
        omissions: list[ContextOmission] = []
        used_tokens = 0
        used_bytes = 0

        for candidate in candidates:
            entry = cls._entry(candidate)
            if cls._fits(
                budget,
                items=len(entries) + 1,
                bytes_=used_bytes + entry.content_bytes,
                tokens=used_tokens + entry.estimated_tokens,
            ):
                entries.append(entry)
                used_bytes += entry.content_bytes
                used_tokens += entry.estimated_tokens
                continue

            if candidate.mandatory:
                raise ContextResolutionError(
                    ContextResolutionBlocker(
                        ContextBlockerReason.MANDATORY_BUDGET,
                        candidate.source,
                        "mandatory context cannot fit the configured context budget",
                    )
                )

            truncated = None
            if policy.allow_optional_truncation and candidate.inline_content is not None:
                truncated = cls._truncate_to_remaining_budget(
                    candidate,
                    budget=budget,
                    current_items=len(entries),
                    used_bytes=used_bytes,
                    used_tokens=used_tokens,
                    policy=policy,
                )
            if truncated is not None:
                entries.append(truncated)
                used_bytes += truncated.content_bytes
                used_tokens += truncated.estimated_tokens
                continue

            omissions.append(
                cls._omission(
                    candidate,
                    ContextOmissionReason.BUDGET,
                    "optional context omitted because the remaining budget is insufficient",
                )
            )
        return entries, omissions

    @staticmethod
    def _fits(
        budget: ContextBudget,
        *,
        items: int,
        bytes_: int,
        tokens: int,
    ) -> bool:
        return (
            (budget.max_items is None or items <= budget.max_items)
            and (budget.max_bytes is None or bytes_ <= budget.max_bytes)
            and (budget.max_tokens is None or tokens <= budget.max_tokens)
        )

    @classmethod
    def _truncate_to_remaining_budget(
        cls,
        candidate: ContextCandidate,
        *,
        budget: ContextBudget,
        current_items: int,
        used_bytes: int,
        used_tokens: int,
        policy: ContextPolicy,
    ) -> ContextEntry | None:
        if budget.max_items is not None and current_items + 1 > budget.max_items:
            return None
        assert candidate.inline_content is not None
        raw = candidate.inline_content.encode("utf-8")
        byte_room = len(raw) if budget.max_bytes is None else max(0, budget.max_bytes - used_bytes)
        token_room = (
            math.ceil(len(raw) / 4)
            if budget.max_tokens is None
            else max(0, budget.max_tokens - used_tokens)
        )
        byte_room = min(byte_room, token_room * 4)
        if byte_room < policy.minimum_truncated_bytes:
            return None

        prefix = raw[:byte_room]
        while prefix:
            try:
                text = prefix.decode("utf-8")
                break
            except UnicodeDecodeError:
                prefix = prefix[:-1]
        else:
            return None
        if len(prefix) < policy.minimum_truncated_bytes or len(prefix) >= len(raw):
            return None

        output_digest = hashlib.sha256(prefix).hexdigest()
        estimated_tokens = max(1, math.ceil(len(prefix) / 4))
        if not cls._fits(
            budget,
            items=current_items + 1,
            bytes_=used_bytes + len(prefix),
            tokens=used_tokens + estimated_tokens,
        ):
            return None
        return ContextEntry(
            ordinal=0,
            source=candidate.source,
            role=candidate.role,
            selection_reason=candidate.selection_reason,
            mandatory=False,
            content_digest=output_digest,
            inline_content=text,
            freshness=candidate.freshness,
            trust=candidate.trust,
            data_classification=candidate.data_classification,
            priority=candidate.priority,
            relevance=candidate.relevance,
            estimated_tokens=estimated_tokens,
            content_bytes=len(prefix),
            project_id=candidate.project_id,
            workspace_id=candidate.workspace_id,
            conflict_key=candidate.conflict_key,
            security_labels=candidate.security_labels,
            transformation=ContextTransformation(
                kind=ContextTransformationKind.TRUNCATE,
                policy_version=policy.truncation_policy_version,
                input_digest=cast(str, candidate.content_digest),
                output_digest=output_digest,
                details={
                    "input_bytes": len(raw),
                    "output_bytes": len(prefix),
                    "method": "utf8-prefix",
                },
            ),
            metadata=candidate.metadata,
        )

    @staticmethod
    def _omission(
        candidate: ContextCandidate,
        reason: ContextOmissionReason,
        detail: str,
    ) -> ContextOmission:
        return ContextOmission(
            source=candidate.source,
            reason=reason,
            mandatory=candidate.mandatory,
            detail=detail,
            content_digest=candidate.content_digest,
        )


class ContextBundleRepository(Protocol):
    def put(self, bundle: ContextBundle) -> ContextBundle: ...

    def get(self, context_bundle_id: str) -> ContextBundle: ...

    def get_by_digest(self, digest: str) -> ContextBundle | None: ...


class ContextAssemblyService:
    """Collect source candidates, resolve once, then persist the immutable bundle."""

    def __init__(
        self,
        resolver: ContextResolver,
        repository: ContextBundleRepository,
    ) -> None:
        self.resolver = resolver
        self.repository = repository

    async def assemble(
        self,
        request: ContextAssemblyRequest,
        *,
        adapters: Sequence[ContextSourceAdapter] = (),
    ) -> ContextBundle:
        candidates = list(request.candidates)
        if adapters:
            source_request = ContextSourceRequest(
                task_id=request.task_id,
                run_id=request.run_id,
                agent_id=request.agent_id,
                agent_revision=request.agent_revision,
                project_id=request.operation.project_id,
                workspace_id=request.workspace_id,
                plan_id=request.plan_id,
                step_id=request.step_id,
            )
            seen_adapter_ids: set[str] = set()
            for adapter in sorted(adapters, key=lambda item: item.adapter_id):
                if adapter.adapter_id in seen_adapter_ids:
                    raise ValueError(f"duplicate context source adapter ID: {adapter.adapter_id}")
                seen_adapter_ids.add(adapter.adapter_id)
                candidates.extend(await adapter.collect(source_request))

        bundle = await self.resolver.resolve(replace(request, candidates=tuple(candidates)))
        existing = self.repository.get_by_digest(bundle.digest)
        if existing is not None:
            return existing
        return self.repository.put(bundle)


def context_window_requirement(bundle: ContextBundle, *, output_reserve_tokens: int = 0) -> int:
    """Provider-neutral minimum input+reserved-output context requirement for #10 routing."""

    if output_reserve_tokens < 0:
        raise ValueError("output_reserve_tokens must be >= 0")
    return bundle.usage.estimated_tokens + output_reserve_tokens
