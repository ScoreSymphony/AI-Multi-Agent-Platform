"""Durable, authorized lifecycle for provider-neutral egress profiles.

The repository stores policy history only. Provider/model/capability/connector identity
continues to be owned by the existing domain registries referenced by ``target_id``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

from ai_multi_agent_platform.contracts import (
    AuthorizationProvider,
    ContractError,
    DataClassification,
    EGRESS_PROFILE_SCHEMA_VERSION,
    EgressCostClass,
    EgressProfile,
    EgressProfileTrust,
    EgressTargetKind,
    EgressTargetPosture,
    ErrorCode,
    JsonValue,
    OperationContext,
)
from ai_multi_agent_platform.contracts.authorization import (
    AuthorizationRequest,
    normalize_authorization_decision,
)
from ai_multi_agent_platform.domain import OwnerRef

from .authorization import AuthorizationAction

EGRESS_PROFILE_STORE_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class EgressProfileDefinition:
    """Stable identity/scope around an immutable contiguous EgressProfile history."""

    profile_id: str
    target_kind: EgressTargetKind
    target_id: str
    owner_ref: OwnerRef
    current_revision: int
    project_id: str | None = None
    enabled: bool = True
    created_at: datetime = datetime.min.replace(tzinfo=UTC)
    updated_at: datetime = datetime.min.replace(tzinfo=UTC)
    schema_version: str = EGRESS_PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.profile_id.strip() or not self.target_id.strip():
            raise ValueError("egress profile definition identity must not be blank")
        if self.current_revision < 1:
            raise ValueError("egress profile current_revision must be at least 1")
        if self.project_id is not None and not self.project_id.strip():
            raise ValueError("egress profile project_id must not be blank when provided")
        for value, name in (
            (self.created_at, "created_at"),
            (self.updated_at, "updated_at"),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"egress profile {name} must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("egress profile updated_at cannot precede created_at")
        if self.schema_version != EGRESS_PROFILE_SCHEMA_VERSION:
            raise ValueError("unsupported egress profile schema version")

    @property
    def current_ref(self) -> str:
        return f"{self.profile_id}@{self.current_revision}"


class EgressProfileRepository(Protocol):
    def create_profile(
        self,
        definition: EgressProfileDefinition,
        revision: EgressProfile,
    ) -> None: ...

    def update_profile(
        self,
        definition: EgressProfileDefinition,
        revision: EgressProfile,
    ) -> None: ...

    def get_definition(self, profile_id: str) -> EgressProfileDefinition: ...

    def get_revision(self, profile_id: str, revision: int) -> EgressProfile: ...

    def list_definitions(self) -> tuple[EgressProfileDefinition, ...]: ...

    def list_revisions(self, profile_id: str) -> tuple[EgressProfile, ...]: ...

    def set_enabled(self, profile_id: str, enabled: bool) -> EgressProfileDefinition: ...

    def resolve(
        self,
        target_kind: EgressTargetKind,
        target_id: str,
        project_id: str | None = None,
    ) -> EgressProfile | None: ...

    def compensate_profile_creation(
        self,
        profile_id: str,
        *,
        expected_current_revision: int,
    ) -> None: ...


class JsonEgressProfileRepository:
    """Atomic JSON reference persistence with exact immutable revision recovery."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._definitions: dict[str, EgressProfileDefinition] = {}
        self._revisions: dict[tuple[str, int], EgressProfile] = {}
        self._load()

    def create_profile(
        self,
        definition: EgressProfileDefinition,
        revision: EgressProfile,
    ) -> None:
        if definition.profile_id in self._definitions:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"egress profile already exists: {definition.profile_id}",
            )
        self._validate_pair(definition, revision, expected_revision=1)
        for existing in self._definitions.values():
            if (
                existing.target_kind is definition.target_kind
                and existing.target_id == definition.target_id
                and existing.project_id == definition.project_id
            ):
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "an egress profile already owns this target/scope",
                    details={
                        "existing_profile_id": existing.profile_id,
                        "target_kind": definition.target_kind.value,
                        "target_id": definition.target_id,
                        "project_id": definition.project_id,
                    },
                )
        self._definitions[definition.profile_id] = definition
        self._revisions[(revision.profile_id, revision.revision)] = revision
        try:
            self._persist()
        except Exception:
            self._definitions.pop(definition.profile_id, None)
            self._revisions.pop((revision.profile_id, revision.revision), None)
            raise

    def update_profile(
        self,
        definition: EgressProfileDefinition,
        revision: EgressProfile,
    ) -> None:
        current = self.get_definition(definition.profile_id)
        expected_revision = current.current_revision + 1
        self._validate_pair(definition, revision, expected_revision=expected_revision)
        if (
            definition.target_kind is not current.target_kind
            or definition.target_id != current.target_id
            or definition.owner_ref != current.owner_ref
            or definition.project_id != current.project_id
            or definition.created_at != current.created_at
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "egress profile stable identity, target and scope cannot be rewritten",
                details={"profile_id": definition.profile_id},
            )
        if (revision.profile_id, revision.revision) in self._revisions:
            raise ContractError(ErrorCode.CONFLICT, "egress profile revision already exists")
        if definition.updated_at < current.updated_at:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "egress profile updated_at cannot move backwards",
            )
        self._definitions[definition.profile_id] = definition
        self._revisions[(revision.profile_id, revision.revision)] = revision
        try:
            self._persist()
        except Exception:
            self._definitions[definition.profile_id] = current
            self._revisions.pop((revision.profile_id, revision.revision), None)
            raise

    def get_definition(self, profile_id: str) -> EgressProfileDefinition:
        try:
            return self._definitions[profile_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"egress profile not found: {profile_id}",
            ) from exc

    def get_revision(self, profile_id: str, revision: int) -> EgressProfile:
        if revision < 1:
            raise ContractError(ErrorCode.INVALID_REQUEST, "egress profile revision must be positive")
        try:
            return self._revisions[(profile_id, revision)]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"egress profile revision not found: {profile_id}@{revision}",
            ) from exc

    def list_definitions(self) -> tuple[EgressProfileDefinition, ...]:
        return tuple(self._definitions[key] for key in sorted(self._definitions))

    def list_revisions(self, profile_id: str) -> tuple[EgressProfile, ...]:
        definition = self.get_definition(profile_id)
        return tuple(
            self._revisions[(profile_id, number)]
            for number in range(1, definition.current_revision + 1)
        )

    def set_enabled(self, profile_id: str, enabled: bool) -> EgressProfileDefinition:
        current = self.get_definition(profile_id)
        if current.enabled is enabled:
            return current
        updated = replace(current, enabled=enabled, updated_at=max(datetime.now(UTC), current.updated_at))
        self._definitions[profile_id] = updated
        try:
            self._persist()
        except Exception:
            self._definitions[profile_id] = current
            raise
        return updated

    def resolve(
        self,
        target_kind: EgressTargetKind,
        target_id: str,
        project_id: str | None = None,
    ) -> EgressProfile | None:
        """Resolve one enabled target profile with project scope taking precedence."""

        candidates = tuple(
            definition
            for definition in self._definitions.values()
            if definition.enabled
            and definition.target_kind is target_kind
            and definition.target_id == target_id
        )
        scoped = tuple(
            item for item in candidates if project_id is not None and item.project_id == project_id
        )
        selected = scoped or tuple(item for item in candidates if item.project_id is None)
        if not selected:
            return None
        if len(selected) != 1:
            raise ContractError(
                ErrorCode.CONFLICT,
                "multiple egress profiles resolve to the same target/scope",
                details={
                    "target_kind": target_kind.value,
                    "target_id": target_id,
                    "project_id": project_id,
                },
            )
        definition = selected[0]
        return self.get_revision(definition.profile_id, definition.current_revision)

    def compensate_profile_creation(
        self,
        profile_id: str,
        *,
        expected_current_revision: int,
    ) -> None:
        definition = self.get_definition(profile_id)
        if definition.current_revision != expected_current_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "egress profile compensation refused because history advanced",
                details={
                    "profile_id": profile_id,
                    "expected_current_revision": expected_current_revision,
                    "current_revision": definition.current_revision,
                },
            )
        revisions = self.list_revisions(profile_id)
        del self._definitions[profile_id]
        for revision in revisions:
            self._revisions.pop((profile_id, revision.revision), None)
        try:
            self._persist()
        except Exception:
            self._definitions[profile_id] = definition
            for revision in revisions:
                self._revisions[(profile_id, revision.revision)] = revision
            raise

    def _validate_pair(
        self,
        definition: EgressProfileDefinition,
        revision: EgressProfile,
        *,
        expected_revision: int,
    ) -> None:
        if definition.profile_id != revision.profile_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "egress profile definition/revision identity mismatch",
            )
        if definition.current_revision != expected_revision or revision.revision != expected_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "egress profile revision must advance contiguously",
                details={"expected_revision": expected_revision},
            )
        if (
            definition.target_kind is not revision.target_kind
            or definition.target_id != revision.target_id
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "egress profile target identity must remain stable",
            )
        if definition.schema_version != revision.schema_version:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "egress profile definition/revision schema versions must match",
            )

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = cast(object, json.loads(self.path.read_text(encoding="utf-8")))
        if not isinstance(raw, dict):
            raise ContractError(ErrorCode.INVALID_CONFIGURATION, "egress profile store must be an object")
        if raw.get("schema_version") != EGRESS_PROFILE_STORE_SCHEMA_VERSION:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "unsupported egress profile store schema version",
            )
        profiles = raw.get("profiles")
        if not isinstance(profiles, list):
            raise ContractError(ErrorCode.INVALID_CONFIGURATION, "egress profile store profiles must be a list")
        for raw_profile in profiles:
            if not isinstance(raw_profile, dict):
                raise ContractError(ErrorCode.INVALID_CONFIGURATION, "egress profile entry must be an object")
            definition = _definition_from_json(raw_profile.get("definition"))
            raw_revisions = raw_profile.get("revisions")
            if not isinstance(raw_revisions, list) or not raw_revisions:
                raise ContractError(ErrorCode.INVALID_CONFIGURATION, "egress profile requires revision history")
            revisions = tuple(egress_profile_from_json(item) for item in raw_revisions)
            expected_numbers = tuple(range(1, definition.current_revision + 1))
            if tuple(item.revision for item in revisions) != expected_numbers:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "persisted egress profile revisions must be contiguous from revision 1",
                )
            for revision in revisions:
                self._validate_loaded_revision(definition, revision)
            if definition.profile_id in self._definitions:
                raise ContractError(ErrorCode.CONFLICT, "duplicate egress profile in store")
            self._definitions[definition.profile_id] = definition
            for revision in revisions:
                self._revisions[(revision.profile_id, revision.revision)] = revision
        self._validate_unique_target_scopes()

    @staticmethod
    def _validate_loaded_revision(
        definition: EgressProfileDefinition,
        revision: EgressProfile,
    ) -> None:
        if (
            revision.profile_id != definition.profile_id
            or revision.target_kind is not definition.target_kind
            or revision.target_id != definition.target_id
            or revision.schema_version != definition.schema_version
        ):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "persisted egress profile history has inconsistent identity or target",
            )

    def _validate_unique_target_scopes(self) -> None:
        seen: set[tuple[EgressTargetKind, str, str | None]] = set()
        for definition in self._definitions.values():
            key = (definition.target_kind, definition.target_id, definition.project_id)
            if key in seen:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "persisted egress profiles contain duplicate target/scope ownership",
                )
            seen.add(key)

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        profiles: list[JsonValue] = []
        for profile_id in sorted(self._definitions):
            definition = self._definitions[profile_id]
            profiles.append(
                {
                    "definition": _definition_to_json(definition),
                    "revisions": [
                        egress_profile_to_json(self._revisions[(profile_id, number)])
                        for number in range(1, definition.current_revision + 1)
                    ],
                }
            )
        payload: dict[str, JsonValue] = {
            "schema_version": EGRESS_PROFILE_STORE_SCHEMA_VERSION,
            "profiles": profiles,
        }
        encoded = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, self.path)


class EgressProfileService:
    """Authorized lifecycle boundary; ordinary writes cannot self-assert verified trust."""

    def __init__(
        self,
        repository: EgressProfileRepository,
        *,
        authorization: AuthorizationProvider | None = None,
    ) -> None:
        self.repository = repository
        self.authorization = authorization

    async def create_profile(
        self,
        profile: EgressProfile,
        *,
        owner_ref: OwnerRef,
        project_id: str | None,
        principal_ref: str,
        context: OperationContext,
        actor_type: str | None = None,
    ) -> EgressProfile:
        self._require_project_scope(project_id, context)
        self._reject_unverified_write_as_verified(profile)
        if profile.revision != 1:
            raise ContractError(ErrorCode.INVALID_REQUEST, "new egress profiles must start at revision 1")
        await self._authorize(
            principal_ref=principal_ref,
            context=context,
            actor_type=actor_type,
            action=AuthorizationAction.ADMINISTER,
            resource_ref=profile.profile_id,
            owner_ref=owner_ref,
        )
        now = datetime.now(UTC)
        definition = EgressProfileDefinition(
            profile_id=profile.profile_id,
            target_kind=profile.target_kind,
            target_id=profile.target_id,
            owner_ref=owner_ref,
            current_revision=1,
            project_id=project_id,
            created_at=now,
            updated_at=now,
        )
        self.repository.create_profile(definition, profile)
        return profile

    async def version_profile(
        self,
        profile: EgressProfile,
        *,
        expected_revision: int,
        principal_ref: str,
        context: OperationContext,
        actor_type: str | None = None,
    ) -> EgressProfile:
        current = self.repository.get_definition(profile.profile_id)
        self._require_project_scope(current.project_id, context)
        self._reject_unverified_write_as_verified(profile)
        await self._authorize(
            principal_ref=principal_ref,
            context=context,
            actor_type=actor_type,
            action=AuthorizationAction.ADMINISTER,
            resource_ref=profile.profile_id,
            owner_ref=current.owner_ref,
        )
        if expected_revision != current.current_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "egress profile was updated after the caller's base revision",
                details={
                    "expected_revision": expected_revision,
                    "current_revision": current.current_revision,
                },
            )
        if profile.revision != current.current_revision + 1:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "egress profile revision must be exactly current revision + 1",
            )
        definition = replace(
            current,
            current_revision=profile.revision,
            updated_at=max(datetime.now(UTC), current.updated_at),
        )
        self.repository.update_profile(definition, profile)
        return profile

    async def verify_profile(
        self,
        profile_id: str,
        *,
        expected_revision: int,
        verification_ref: str,
        principal_ref: str,
        context: OperationContext,
        actor_type: str | None = None,
    ) -> EgressProfile:
        if not verification_ref.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "verification_ref must not be blank")
        current = self.repository.get_definition(profile_id)
        self._require_project_scope(current.project_id, context)
        await self._authorize(
            principal_ref=principal_ref,
            context=context,
            actor_type=actor_type,
            action=AuthorizationAction.ADMINISTER,
            resource_ref=profile_id,
            owner_ref=current.owner_ref,
        )
        if expected_revision != current.current_revision:
            raise ContractError(ErrorCode.CONFLICT, "egress profile verification base revision is stale")
        previous = self.repository.get_revision(profile_id, current.current_revision)
        verified = replace(
            previous,
            revision=current.current_revision + 1,
            trust=EgressProfileTrust.VERIFIED,
            source_revision=verification_ref,
            metadata={
                **dict(previous.metadata),
                "verification_ref": verification_ref,
                "verified_by": principal_ref,
            },
        )
        definition = replace(
            current,
            current_revision=verified.revision,
            updated_at=max(datetime.now(UTC), current.updated_at),
        )
        self.repository.update_profile(definition, verified)
        return verified

    async def set_enabled(
        self,
        profile_id: str,
        enabled: bool,
        *,
        principal_ref: str,
        context: OperationContext,
        actor_type: str | None = None,
    ) -> EgressProfileDefinition:
        current = self.repository.get_definition(profile_id)
        self._require_project_scope(current.project_id, context)
        await self._authorize(
            principal_ref=principal_ref,
            context=context,
            actor_type=actor_type,
            action=AuthorizationAction.ADMINISTER,
            resource_ref=profile_id,
            owner_ref=current.owner_ref,
        )
        return self.repository.set_enabled(profile_id, enabled)

    async def get_revision(
        self,
        profile_id: str,
        revision: int,
        *,
        principal_ref: str,
        context: OperationContext,
        actor_type: str | None = None,
    ) -> EgressProfile:
        definition = self.repository.get_definition(profile_id)
        self._require_project_scope(definition.project_id, context)
        await self._authorize(
            principal_ref=principal_ref,
            context=context,
            actor_type=actor_type,
            action=AuthorizationAction.READ,
            resource_ref=f"{profile_id}@{revision}",
            owner_ref=definition.owner_ref,
        )
        return self.repository.get_revision(profile_id, revision)

    async def list_profiles(
        self,
        *,
        principal_ref: str,
        context: OperationContext,
        actor_type: str | None = None,
    ) -> tuple[EgressProfileDefinition, ...]:
        visible: list[EgressProfileDefinition] = []
        for definition in self.repository.list_definitions():
            if definition.project_id is not None and definition.project_id != context.project_id:
                continue
            try:
                await self._authorize(
                    principal_ref=principal_ref,
                    context=context,
                    actor_type=actor_type,
                    action=AuthorizationAction.READ,
                    resource_ref=definition.profile_id,
                    owner_ref=definition.owner_ref,
                )
            except ContractError as exc:
                if exc.code is ErrorCode.FORBIDDEN:
                    continue
                raise
            visible.append(definition)
        return tuple(visible)

    @staticmethod
    def _reject_unverified_write_as_verified(profile: EgressProfile) -> None:
        if profile.trust is EgressProfileTrust.VERIFIED:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "verified egress trust may only be created by the explicit verification transition",
            )

    @staticmethod
    def _require_project_scope(project_id: str | None, context: OperationContext) -> None:
        if project_id is not None and context.project_id != project_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "egress profile project scope does not match operation context",
                details={"project_id": project_id},
            )

    @staticmethod
    def _actor_type(context: OperationContext, explicit: str | None) -> str:
        if explicit is not None:
            if not explicit.strip():
                raise ContractError(ErrorCode.INVALID_REQUEST, "actor_type must not be blank")
            return explicit
        return "human" if context.owner_type in {"user", "organization", "team"} else "service"

    async def _authorize(
        self,
        *,
        principal_ref: str,
        context: OperationContext,
        actor_type: str | None,
        action: AuthorizationAction,
        resource_ref: str,
        owner_ref: OwnerRef,
    ) -> None:
        if not principal_ref.strip():
            raise ContractError(ErrorCode.UNAUTHORIZED, "principal_ref must not be blank")
        if self.authorization is None:
            if context.owner_type is not None and (
                context.owner_type != owner_ref.type or context.owner_id != owner_ref.id
            ):
                raise ContractError(ErrorCode.FORBIDDEN, "egress profile owner scope mismatch")
            return
        decision = normalize_authorization_decision(
            await self.authorization.authorize(
                AuthorizationRequest(
                    principal_ref=principal_ref,
                    actor_type=self._actor_type(context, actor_type),
                    action=action.value,
                    resource_type="provider_configuration",
                    resource_ref=resource_ref,
                    context=context,
                    organization_id=owner_ref.id if owner_ref.type == "organization" else None,
                    team_id=owner_ref.id if owner_ref.type == "team" else None,
                    trust_context={"policy_domain": "data_egress"},
                )
            )
        )
        if not decision.allowed:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                decision.reason or "egress profile authorization denied",
                details={"action": action.value, "resource_ref": resource_ref},
            )


def new_egress_profile_id() -> str:
    return f"egress_profile_{uuid4()}"


def egress_profile_to_json(profile: EgressProfile) -> dict[str, JsonValue]:
    return {
        "profile_id": profile.profile_id,
        "revision": profile.revision,
        "target_kind": profile.target_kind.value,
        "target_id": profile.target_id,
        "posture": profile.posture.value,
        "allowed_classifications": [item.value for item in profile.allowed_classifications],
        "denied_classifications": [item.value for item in profile.denied_classifications],
        "network_egress_required": profile.network_egress_required,
        "data_retention_policy": profile.data_retention_policy,
        "training_policy": profile.training_policy,
        "logging_policy": profile.logging_policy,
        "jurisdiction": profile.jurisdiction,
        "cost_class": profile.cost_class.value,
        "credential_required": profile.credential_required,
        "policy_source": profile.policy_source,
        "source_revision": profile.source_revision,
        "trust": profile.trust.value,
        "metadata": dict(profile.metadata),
        "schema_version": profile.schema_version,
    }


def egress_profile_from_json(value: object) -> EgressProfile:
    item = _object(value, "egress profile")
    try:
        return EgressProfile(
            profile_id=_string(item, "profile_id"),
            revision=_positive_int(item, "revision"),
            target_kind=EgressTargetKind(_string(item, "target_kind")),
            target_id=_string(item, "target_id"),
            posture=EgressTargetPosture(_string(item, "posture")),
            allowed_classifications=_classifications(item.get("allowed_classifications")),
            denied_classifications=_classifications(item.get("denied_classifications")),
            network_egress_required=_optional_bool(item, "network_egress_required"),
            data_retention_policy=_optional_string(item, "data_retention_policy"),
            training_policy=_optional_string(item, "training_policy"),
            logging_policy=_optional_string(item, "logging_policy"),
            jurisdiction=_optional_string(item, "jurisdiction"),
            cost_class=EgressCostClass(_string(item, "cost_class")),
            credential_required=_optional_bool(item, "credential_required"),
            policy_source=_string(item, "policy_source"),
            source_revision=_string(item, "source_revision"),
            trust=EgressProfileTrust(_string(item, "trust")),
            metadata=cast(dict[str, JsonValue], _object(item.get("metadata", {}), "egress metadata")),
            schema_version=_string(item, "schema_version"),
        )
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, f"invalid egress profile: {exc}") from exc


def _definition_to_json(definition: EgressProfileDefinition) -> dict[str, JsonValue]:
    return {
        "profile_id": definition.profile_id,
        "target_kind": definition.target_kind.value,
        "target_id": definition.target_id,
        "owner_ref": {"type": definition.owner_ref.type, "id": definition.owner_ref.id},
        "current_revision": definition.current_revision,
        "project_id": definition.project_id,
        "enabled": definition.enabled,
        "created_at": definition.created_at.isoformat(),
        "updated_at": definition.updated_at.isoformat(),
        "schema_version": definition.schema_version,
    }


def _definition_from_json(value: object) -> EgressProfileDefinition:
    item = _object(value, "egress profile definition")
    owner = _object(item.get("owner_ref"), "egress profile owner_ref")
    owner_type = _string(owner, "type")
    if owner_type not in {"user", "organization", "team", "service"}:
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, "unsupported egress profile owner type")
    return EgressProfileDefinition(
        profile_id=_string(item, "profile_id"),
        target_kind=EgressTargetKind(_string(item, "target_kind")),
        target_id=_string(item, "target_id"),
        owner_ref=OwnerRef(type=cast(object, owner_type), id=_string(owner, "id")),  # type: ignore[arg-type]
        current_revision=_positive_int(item, "current_revision"),
        project_id=_optional_string(item, "project_id"),
        enabled=_bool(item, "enabled"),
        created_at=_timestamp(item, "created_at"),
        updated_at=_timestamp(item, "updated_at"),
        schema_version=_string(item, "schema_version"),
    )


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, f"{label} must be an object")
    return dict(value)


def _string(item: Mapping[str, object], field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, f"{field} must be a non-blank string")
    return value


def _optional_string(item: Mapping[str, object], field: str) -> str | None:
    value = item.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, f"{field} must be non-blank when provided")
    return value


def _positive_int(item: Mapping[str, object], field: str) -> int:
    value = item.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, f"{field} must be a positive integer")
    return value


def _bool(item: Mapping[str, object], field: str) -> bool:
    value = item.get(field)
    if not isinstance(value, bool):
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, f"{field} must be a boolean")
    return value


def _optional_bool(item: Mapping[str, object], field: str) -> bool | None:
    value = item.get(field)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, f"{field} must be a boolean when provided")
    return value


def _timestamp(item: Mapping[str, object], field: str) -> datetime:
    raw = _string(item, field)
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, f"{field} is not an ISO timestamp") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, f"{field} must be timezone-aware")
    return value


def _classifications(value: object) -> tuple[DataClassification, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, "classification list must contain strings")
    try:
        return tuple(DataClassification(cast(str, item)) for item in value)
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, "unknown data classification") from exc


__all__ = [
    "EGRESS_PROFILE_STORE_SCHEMA_VERSION",
    "EgressProfileDefinition",
    "EgressProfileRepository",
    "EgressProfileService",
    "JsonEgressProfileRepository",
    "egress_profile_from_json",
    "egress_profile_to_json",
    "new_egress_profile_id",
]
