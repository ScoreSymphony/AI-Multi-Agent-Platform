"""Record-scoped Control Plane authorization for governed Learning (#595)."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Never, Protocol, cast

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    JsonValue,
    ProviderDescriptor,
)
from ai_multi_agent_platform.contracts.authorization import (
    AuthorizationDecision as CanonicalAuthorizationDecision,
)
from ai_multi_agent_platform.contracts.authorization import AuthorizationOutcome
from ai_multi_agent_platform.contracts.interfaces import AuthorizationProvider
from ai_multi_agent_platform.contracts.types import AuthorizationDecision, AuthorizationRequest
from ai_multi_agent_platform.control_plane.extensions import CommandHandler, ControlPlane
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .control_plane import (
    LEARNING_CANDIDATE_COLLECTION,
    LEARNING_COMMANDS,
    LEARNING_FEEDBACK_COLLECTION,
    LearningFeedbackResourceService,
    _candidate_resource,
    _feedback_resource,
    register_learning_control_plane,
)
from .control_plane import _target as _parse_target
from .models import LearningTarget
from .runtime import ObservedLearningService
from .runtime_control_plane import (
    LEARNING_POST_PROMOTION_COLLECTION,
    LearningPostPromotionResourceService,
    RuntimeAwareLearningCandidateResourceService,
    register_learning_runtime_control_plane,
)
from .runtime_control_plane import (
    _resource as _post_promotion_resource,
)

_RECORD_SCOPED_AUTHORIZATION = ContextVar(
    "learning_record_scoped_authorization",
    default=False,
)


class _ControlPlaneInternals(Protocol):
    _authorization: AuthorizationProvider | None
    _command_handlers: dict[str, CommandHandler]

    async def _authorize(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        owner_type: str | None = None,
        owner_id: str | None = None,
        project_id: str | None = None,
        request_payload_digest: str | None = None,
    ) -> None: ...

    async def _allowed(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        owner_type: str | None = None,
        owner_id: str | None = None,
        project_id: str | None = None,
        request_payload_digest: str | None = None,
    ) -> bool: ...


class DeferredLearningAuthorizationProvider(AuthorizationProvider):
    """Defer only Learning's coarse extension check to its record-aware service."""

    def __init__(self, inner: AuthorizationProvider) -> None:
        self.inner = inner

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self.inner.descriptor

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        if _is_learning_action(request.action) and not _RECORD_SCOPED_AUTHORIZATION.get():
            return CanonicalAuthorizationDecision(
                AuthorizationOutcome.ALLOW,
                reason="Learning authorization is deferred to canonical record scope",
                policy_id="learning:record-scope",
            )
        return await self.inner.authorize(request)


@dataclass(slots=True)
class LearningScopeAccess:
    """Evaluate Learning operations with the record's canonical project scope."""

    control_plane: ControlPlane

    async def authorize(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        project_id: str | None,
    ) -> None:
        internals = cast(_ControlPlaneInternals, self.control_plane)
        token = _RECORD_SCOPED_AUTHORIZATION.set(True)
        try:
            await internals._authorize(
                context,
                action,
                resource_ref,
                project_id=project_id,
            )
        finally:
            _RECORD_SCOPED_AUTHORIZATION.reset(token)

    async def allowed(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        project_id: str | None,
    ) -> bool:
        internals = cast(_ControlPlaneInternals, self.control_plane)
        token = _RECORD_SCOPED_AUTHORIZATION.set(True)
        try:
            return await internals._allowed(
                context,
                action,
                resource_ref,
                project_id=project_id,
            )
        finally:
            _RECORD_SCOPED_AUTHORIZATION.reset(token)


class ScopedRuntimeAwareLearningCandidateResourceService(
    RuntimeAwareLearningCandidateResourceService
):
    def __init__(self, learning: ObservedLearningService, access: LearningScopeAccess) -> None:
        super().__init__(learning)
        self._access = access

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        resources: list[dict[str, JsonValue]] = []
        for candidate in self._observed_learning.list_candidates():
            if not await self._access.allowed(
                context,
                "learning-candidate:list",
                candidate.learning_candidate_id,
                project_id=candidate.project_id,
            ):
                continue
            resource = _candidate_resource(self._observed_learning, candidate)
            resources.append(self._with_post_promotion_status(resource))
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        candidate = self._observed_learning.get_candidate(resource_id)
        if not await self._access.allowed(
            context,
            "learning-candidate:read",
            resource_id,
            project_id=candidate.project_id,
        ):
            _not_found("Learning Candidate")
        return self._with_post_promotion_status(
            _candidate_resource(self._observed_learning, candidate)
        )


class ScopedLearningFeedbackResourceService(LearningFeedbackResourceService):
    def __init__(self, learning: ObservedLearningService, access: LearningScopeAccess) -> None:
        super().__init__(learning)
        self._access = access

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        resources: list[dict[str, JsonValue]] = []
        for feedback in self._learning.repository.list_feedback():
            if await self._access.allowed(
                context,
                "learning-feedback:list",
                feedback.feedback_id,
                project_id=feedback.project_id,
            ):
                resources.append(_feedback_resource(feedback))
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        feedback = self._learning.repository.get_feedback(resource_id)
        if not await self._access.allowed(
            context,
            "learning-feedback:read",
            resource_id,
            project_id=feedback.project_id,
        ):
            _not_found("Learning feedback")
        return _feedback_resource(feedback)


class ScopedLearningPostPromotionResourceService(LearningPostPromotionResourceService):
    def __init__(self, learning: ObservedLearningService, access: LearningScopeAccess) -> None:
        super().__init__(learning)
        self._access = access

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        records: list[dict[str, JsonValue]] = []
        for candidate in self._learning.list_candidates():
            for record in self._learning.post_promotion_recorder.list_for_candidate(
                candidate.learning_candidate_id
            ):
                if not await self._access.allowed(
                    context,
                    "learning-post-promotion-evaluation:list",
                    record.record_id,
                    project_id=candidate.project_id,
                ):
                    continue
                records.append(_post_promotion_resource(record))
        return tuple(records)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        for candidate in self._learning.list_candidates():
            for record in self._learning.post_promotion_recorder.list_for_candidate(
                candidate.learning_candidate_id
            ):
                if record.record_id != resource_id:
                    continue
                if not await self._access.allowed(
                    context,
                    "learning-post-promotion-evaluation:read",
                    resource_id,
                    project_id=candidate.project_id,
                ):
                    _not_found("post-promotion Learning Evaluation record")
                return _post_promotion_resource(record)
        _not_found("post-promotion Learning Evaluation record")


class ScopedLearningCommand:
    def __init__(
        self,
        action: str,
        delegate: CommandHandler,
        learning: ObservedLearningService,
        access: LearningScopeAccess,
    ) -> None:
        self.action = action
        self.delegate = delegate
        self.learning = learning
        self.access = access

    async def __call__(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        await self._authorize(context, resource_ref, payload)
        return await self.delegate(context, resource_ref, payload)

    async def _authorize(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> None:
        if self.action == "learning.feedback.create":
            await self.access.authorize(
                context,
                self.action,
                resource_ref,
                project_id=_payload_project_id(payload),
            )
            return

        if self.action == "learning.propose":
            project_id = _payload_project_id(payload)
            self._require_supported_target_scope(_payload_target(payload), project_id)
            await self.access.authorize(
                context,
                self.action,
                resource_ref,
                project_id=project_id,
            )
            return

        if self.action == "learning.propose-from-feedback":
            feedback = self.learning.repository.get_feedback(resource_ref)
            self._require_supported_target_scope(_payload_target(payload), feedback.project_id)
            await self.access.authorize(
                context,
                self.action,
                resource_ref,
                project_id=feedback.project_id,
            )
            return

        candidate = self.learning.get_candidate(resource_ref)
        await self.access.authorize(
            context,
            self.action,
            resource_ref,
            project_id=candidate.project_id,
        )
        if self.action == "learning.promote":
            target_project_id = self.learning.promotion_registry.resolve_project_id(candidate.target)
            _require_matching_project_scope(candidate.project_id, target_project_id)
            await self.access.authorize(
                context,
                self.action,
                candidate.target.resource_id,
                project_id=target_project_id,
            )
            return
        if self.action != "learning.supersede":
            return

        replacement_id = _required_payload_string(payload, "superseded_by")
        replacement = self.learning.get_candidate(replacement_id)
        if not await self.access.allowed(
            context,
            "learning-candidate:read",
            replacement_id,
            project_id=replacement.project_id,
        ):
            _not_found("Learning Candidate")

    def _require_supported_target_scope(
        self,
        target: LearningTarget,
        candidate_project_id: str | None,
    ) -> None:
        registry = self.learning.promotion_registry
        if not registry.supports(target.resource_type):
            return
        target_project_id = registry.resolve_project_id(target)
        _require_matching_project_scope(candidate_project_id, target_project_id)


def register_scoped_learning_control_plane(
    control_plane: ControlPlane,
    learning: ObservedLearningService,
) -> None:
    """Register Learning with fail-closed per-record project authorization."""

    access = _install_deferred_authorization(control_plane)
    register_learning_control_plane(control_plane, learning)
    register_learning_runtime_control_plane(control_plane, learning)

    control_plane.register_resource_service(
        LEARNING_CANDIDATE_COLLECTION,
        ScopedRuntimeAwareLearningCandidateResourceService(learning, access),
    )
    control_plane.register_resource_service(
        LEARNING_FEEDBACK_COLLECTION,
        ScopedLearningFeedbackResourceService(learning, access),
    )
    control_plane.register_resource_service(
        LEARNING_POST_PROMOTION_COLLECTION,
        ScopedLearningPostPromotionResourceService(learning, access),
    )

    internals = cast(_ControlPlaneInternals, control_plane)
    for action in LEARNING_COMMANDS:
        delegate = internals._command_handlers[action]
        if isinstance(delegate, ScopedLearningCommand):
            continue
        control_plane.register_command(
            action,
            ScopedLearningCommand(action, delegate, learning, access),
        )


def _install_deferred_authorization(control_plane: ControlPlane) -> LearningScopeAccess:
    internals = cast(_ControlPlaneInternals, control_plane)
    current = internals._authorization
    if current is not None and not isinstance(current, DeferredLearningAuthorizationProvider):
        internals._authorization = DeferredLearningAuthorizationProvider(current)
    return LearningScopeAccess(control_plane)


def _is_learning_action(action: str) -> bool:
    return action.startswith(
        (
            "learning.",
            "learning-candidate:",
            "learning-feedback:",
            "learning-post-promotion-evaluation:",
        )
    )


def _payload_project_id(payload: dict[str, JsonValue]) -> str | None:
    value = payload.get("project_id")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, "project_id must be a non-blank string")
    return value


def _payload_target(payload: dict[str, JsonValue]) -> LearningTarget:
    value = payload.get("target")
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.INVALID_REQUEST, "target must be an object")
    return _parse_target(value)


def _require_matching_project_scope(
    candidate_project_id: str | None,
    target_project_id: str | None,
) -> None:
    if candidate_project_id != target_project_id:
        raise ContractError(
            ErrorCode.FORBIDDEN,
            "learning candidate project scope does not match the canonical target project",
        )


def _required_payload_string(payload: dict[str, JsonValue], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field} must be a non-blank string")
    return value


def _not_found(label: str) -> Never:
    raise ContractError(ErrorCode.NOT_FOUND, f"{label} was not found")


__all__ = ["register_scoped_learning_control_plane"]
