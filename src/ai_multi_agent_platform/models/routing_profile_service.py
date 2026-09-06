"""Authorized lifecycle service for durable model-routing profiles."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from ai_multi_agent_platform.contracts import (
    AuthorizationProvider,
    ContractError,
    ErrorCode,
    OperationContext,
)
from ai_multi_agent_platform.contracts.authorization import (
    AuthorizationRequest,
    normalize_authorization_decision,
)
from ai_multi_agent_platform.domain import OwnerRef, Provenance

from .routing_profile_repository import ModelRoutingProfileRepository
from .routing_profiles import (
    ModelRoutingProfileDefinition,
    ModelRoutingProfilePolicy,
    ModelRoutingProfileRef,
    ModelRoutingProfileRevision,
    new_model_routing_profile_id,
)


class ModelRoutingProfileService:
    """Canonical management boundary; provider/gateway state is never mutated here."""

    def __init__(
        self,
        repository: ModelRoutingProfileRepository,
        *,
        authorization: AuthorizationProvider | None = None,
    ) -> None:
        self.repository = repository
        self.authorization = authorization

    async def create_profile(
        self,
        *,
        name: str,
        policy: ModelRoutingProfilePolicy,
        owner_ref: OwnerRef,
        principal_ref: str,
        context: OperationContext,
        actor_type: str | None = None,
        description: str = "",
        project_id: str | None = None,
        provenance: Provenance | None = None,
        profile_id: str | None = None,
    ) -> ModelRoutingProfileRevision:
        canonical_id = profile_id or new_model_routing_profile_id()
        self._require_project_scope(project_id, context)
        await self._authorize(
            principal_ref=principal_ref,
            context=context,
            actor_type=actor_type,
            action="model-routing-profile:create",
            resource_ref=canonical_id,
            owner_ref=owner_ref,
        )
        now = datetime.now(UTC)
        definition = ModelRoutingProfileDefinition(
            profile_id=canonical_id,
            owner_ref=owner_ref,
            current_revision=1,
            project_id=project_id,
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        revision = ModelRoutingProfileRevision(
            profile_id=canonical_id,
            revision=1,
            name=name,
            description=description,
            owner_ref=owner_ref,
            project_id=project_id,
            policy=policy,
            provenance=provenance,
            created_at=now,
        )
        self.repository.create_profile(definition, revision)
        return revision

    async def version_profile(
        self,
        profile_id: str,
        *,
        name: str,
        policy: ModelRoutingProfilePolicy,
        principal_ref: str,
        context: OperationContext,
        actor_type: str | None = None,
        description: str = "",
        expected_revision: int | None = None,
        provenance: Provenance | None = None,
    ) -> ModelRoutingProfileRevision:
        current = self.repository.get_definition(profile_id)
        self._require_project_scope(current.project_id, context)
        await self._authorize(
            principal_ref=principal_ref,
            context=context,
            actor_type=actor_type,
            action="model-routing-profile:version",
            resource_ref=profile_id,
            owner_ref=current.owner_ref,
        )
        if expected_revision is not None and expected_revision != current.current_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "routing profile was updated after the caller's base revision",
                details={
                    "expected_revision": expected_revision,
                    "current_revision": current.current_revision,
                },
            )
        next_revision = current.current_revision + 1
        now = datetime.now(UTC)
        definition = replace(current, current_revision=next_revision, updated_at=now)
        revision = ModelRoutingProfileRevision(
            profile_id=profile_id,
            revision=next_revision,
            name=name,
            description=description,
            owner_ref=current.owner_ref,
            project_id=current.project_id,
            policy=policy,
            provenance=provenance,
            created_at=now,
        )
        self.repository.update_profile(definition, revision)
        return revision

    async def get_revision(
        self,
        ref: ModelRoutingProfileRef,
        *,
        principal_ref: str,
        context: OperationContext,
        actor_type: str | None = None,
        require_enabled: bool = False,
    ) -> ModelRoutingProfileRevision:
        definition = self.repository.get_definition(ref.profile_id)
        self._require_project_scope(definition.project_id, context)
        await self._authorize(
            principal_ref=principal_ref,
            context=context,
            actor_type=actor_type,
            action="model-routing-profile:read",
            resource_ref=ref.canonical_ref,
            owner_ref=definition.owner_ref,
        )
        if require_enabled and not definition.enabled:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"routing profile is disabled: {ref.profile_id}",
            )
        return self.repository.get_revision(ref)

    async def set_enabled(
        self,
        profile_id: str,
        enabled: bool,
        *,
        principal_ref: str,
        context: OperationContext,
        actor_type: str | None = None,
    ) -> ModelRoutingProfileDefinition:
        current = self.repository.get_definition(profile_id)
        self._require_project_scope(current.project_id, context)
        await self._authorize(
            principal_ref=principal_ref,
            context=context,
            actor_type=actor_type,
            action="model-routing-profile:enable" if enabled else "model-routing-profile:disable",
            resource_ref=profile_id,
            owner_ref=current.owner_ref,
        )
        return self.repository.set_enabled(profile_id, enabled)

    async def list_profiles(
        self,
        *,
        principal_ref: str,
        context: OperationContext,
        actor_type: str | None = None,
    ) -> tuple[ModelRoutingProfileDefinition, ...]:
        definitions = tuple(
            item
            for item in self.repository.list_definitions()
            if item.project_id is None or item.project_id == context.project_id
        )
        visible: list[ModelRoutingProfileDefinition] = []
        for definition in definitions:
            try:
                await self._authorize(
                    principal_ref=principal_ref,
                    context=context,
                    actor_type=actor_type,
                    action="model-routing-profile:read",
                    resource_ref=definition.profile_id,
                    owner_ref=definition.owner_ref,
                )
            except ContractError as exc:
                if exc.code is ErrorCode.FORBIDDEN:
                    continue
                raise
            visible.append(definition)
        return tuple(visible)

    def compensate_created(
        self,
        profile_id: str,
        *,
        expected_owner_ref: OwnerRef,
        expected_source: str,
        expected_instance_id: str,
    ) -> None:
        """Remove only an untouched profile created by the exact failed Template apply."""

        definition = self.repository.get_definition(profile_id)
        revision = self.repository.get_revision(ModelRoutingProfileRef(profile_id, 1))
        provenance = revision.provenance
        if (
            definition.current_revision != 1
            or not definition.enabled
            or definition.updated_at != definition.created_at
            or definition.owner_ref != expected_owner_ref
            or revision.owner_ref != expected_owner_ref
            or provenance is None
            or provenance.source != expected_source
            or provenance.details.get("template_instance_id") != expected_instance_id
        ):
            raise ContractError(
                ErrorCode.CONFLICT,
                "routing profile is not eligible for Template compensation",
                details={"profile_id": profile_id},
            )
        self.repository.delete_profile(profile_id)

    @staticmethod
    def _require_project_scope(project_id: str | None, context: OperationContext) -> None:
        if project_id is not None and context.project_id != project_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "routing profile project scope does not match operation context",
                details={"project_id": project_id},
            )

    @staticmethod
    def _authorization_actor_type(context: OperationContext, actor_type: str | None) -> str:
        if actor_type is not None:
            if not actor_type.strip():
                raise ContractError(ErrorCode.INVALID_REQUEST, "actor_type must not be blank")
            return actor_type
        if context.owner_type in {"user", "organization", "team"}:
            return "human"
        return "service"

    async def _authorize(
        self,
        *,
        principal_ref: str,
        context: OperationContext,
        actor_type: str | None,
        action: str,
        resource_ref: str,
        owner_ref: OwnerRef,
    ) -> None:
        if not principal_ref.strip():
            raise ContractError(ErrorCode.UNAUTHORIZED, "principal_ref must not be blank")
        if self.authorization is None:
            if context.owner_type is not None and (
                context.owner_type != owner_ref.type or context.owner_id != owner_ref.id
            ):
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "routing profile owner scope does not match operation context",
                )
            return

        decision = normalize_authorization_decision(
            await self.authorization.authorize(
                AuthorizationRequest(
                    principal_ref=principal_ref,
                    action=action,
                    resource_ref=resource_ref,
                    context=context,
                    actor_type=self._authorization_actor_type(context, actor_type),
                    resource_type="model_routing_profile",
                    organization_id=owner_ref.id if owner_ref.type == "organization" else None,
                    team_id=owner_ref.id if owner_ref.type == "team" else None,
                )
            )
        )
        if not decision.allowed:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                decision.reason or "routing profile authorization denied",
                details={"action": action, "resource_ref": resource_ref},
            )
