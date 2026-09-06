"""Durable reference persistence for canonical model-routing profiles."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, cast
from uuid import uuid4

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef, Provenance

from .routing_profiles import (
    ModelRoutingProfileDefinition,
    ModelRoutingProfilePolicy,
    ModelRoutingProfileRef,
    ModelRoutingProfileRevision,
    RoutingProfileFallbackPolicy,
)
from .types import RoutingRequirements

ROUTING_PROFILE_STORE_SCHEMA_VERSION = "1.0"


class ModelRoutingProfileRepository(Protocol):
    def create_profile(
        self,
        definition: ModelRoutingProfileDefinition,
        revision: ModelRoutingProfileRevision,
    ) -> None: ...

    def update_profile(
        self,
        definition: ModelRoutingProfileDefinition,
        revision: ModelRoutingProfileRevision,
    ) -> None: ...

    def get_definition(self, profile_id: str) -> ModelRoutingProfileDefinition: ...

    def get_revision(self, ref: ModelRoutingProfileRef) -> ModelRoutingProfileRevision: ...

    def list_definitions(self) -> tuple[ModelRoutingProfileDefinition, ...]: ...

    def list_revisions(self, profile_id: str) -> tuple[ModelRoutingProfileRevision, ...]: ...

    def set_enabled(self, profile_id: str, enabled: bool) -> ModelRoutingProfileDefinition: ...

    def delete_profile(self, profile_id: str) -> None: ...


class JsonModelRoutingProfileRepository:
    """Dependency-free, atomic JSON reference store for routing-profile revisions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._definitions: dict[str, ModelRoutingProfileDefinition] = {}
        self._revisions: dict[tuple[str, int], ModelRoutingProfileRevision] = {}
        self._load()

    def create_profile(
        self,
        definition: ModelRoutingProfileDefinition,
        revision: ModelRoutingProfileRevision,
    ) -> None:
        if definition.profile_id in self._definitions:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"routing profile already exists: {definition.profile_id}",
            )
        self._validate_pair(definition, revision, expected_revision=1)
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
        definition: ModelRoutingProfileDefinition,
        revision: ModelRoutingProfileRevision,
    ) -> None:
        current = self.get_definition(definition.profile_id)
        expected_revision = current.current_revision + 1
        self._validate_pair(definition, revision, expected_revision=expected_revision)
        if (revision.profile_id, revision.revision) in self._revisions:
            raise ContractError(
                ErrorCode.CONFLICT,
                "routing profile revision already exists",
                details={"profile_id": revision.profile_id, "revision": revision.revision},
            )
        if definition.created_at != current.created_at:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "routing profile stable identity metadata cannot rewrite created_at",
            )
        if definition.owner_ref != current.owner_ref or definition.project_id != current.project_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "routing profile stable identity scope cannot be rewritten",
                details={
                    "profile_id": definition.profile_id,
                    "current_project_id": current.project_id,
                    "requested_project_id": definition.project_id,
                },
            )
        self._definitions[definition.profile_id] = definition
        self._revisions[(revision.profile_id, revision.revision)] = revision
        try:
            self._persist()
        except Exception:
            self._definitions[definition.profile_id] = current
            self._revisions.pop((revision.profile_id, revision.revision), None)
            raise

    def get_definition(self, profile_id: str) -> ModelRoutingProfileDefinition:
        try:
            return self._definitions[profile_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"routing profile not found: {profile_id}",
            ) from exc

    def get_revision(self, ref: ModelRoutingProfileRef) -> ModelRoutingProfileRevision:
        try:
            return self._revisions[(ref.profile_id, ref.revision)]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"routing profile revision not found: {ref.canonical_ref}",
            ) from exc

    def list_definitions(self) -> tuple[ModelRoutingProfileDefinition, ...]:
        return tuple(self._definitions[key] for key in sorted(self._definitions))

    def list_revisions(self, profile_id: str) -> tuple[ModelRoutingProfileRevision, ...]:
        definition = self.get_definition(profile_id)
        return tuple(
            self._revisions[(profile_id, revision)]
            for revision in range(1, definition.current_revision + 1)
        )

    def set_enabled(self, profile_id: str, enabled: bool) -> ModelRoutingProfileDefinition:
        current = self.get_definition(profile_id)
        if current.enabled is enabled:
            return current
        updated = replace(current, enabled=enabled, updated_at=datetime.now(UTC))
        self._definitions[profile_id] = updated
        try:
            self._persist()
        except Exception:
            self._definitions[profile_id] = current
            raise
        return updated

    def delete_profile(self, profile_id: str) -> None:
        definition = self.get_definition(profile_id)
        revisions = self.list_revisions(profile_id)
        del self._definitions[profile_id]
        for revision_number in range(1, definition.current_revision + 1):
            self._revisions.pop((profile_id, revision_number), None)
        try:
            self._persist()
        except Exception:
            self._definitions[profile_id] = definition
            for stored_revision in revisions:
                self._revisions[(profile_id, stored_revision.revision)] = stored_revision
            raise

    def _validate_pair(
        self,
        definition: ModelRoutingProfileDefinition,
        revision: ModelRoutingProfileRevision,
        *,
        expected_revision: int,
    ) -> None:
        if definition.profile_id != revision.profile_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "routing profile definition/revision identity mismatch",
            )
        if (
            definition.current_revision != expected_revision
            or revision.revision != expected_revision
        ):
            raise ContractError(
                ErrorCode.CONFLICT,
                "routing profile revision must advance exactly once",
                details={"expected_revision": expected_revision},
            )
        if (
            definition.owner_ref != revision.owner_ref
            or definition.project_id != revision.project_id
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "routing profile revision scope must match stable definition scope",
            )

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = cast(object, json.loads(self.path.read_text(encoding="utf-8")))
        if not isinstance(raw, dict):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION, "routing profile store must be an object"
            )
        if raw.get("schema_version") != ROUTING_PROFILE_STORE_SCHEMA_VERSION:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "unsupported routing profile store schema version",
            )
        profiles = raw.get("profiles")
        if not isinstance(profiles, list):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION, "routing profile store profiles must be a list"
            )
        for raw_profile in profiles:
            if not isinstance(raw_profile, dict):
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION, "routing profile entry must be an object"
                )
            definition = _definition_from_json(raw_profile.get("definition"))
            raw_revisions = raw_profile.get("revisions")
            if not isinstance(raw_revisions, list) or not raw_revisions:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION, "routing profile requires revisions"
                )
            revisions = tuple(_revision_from_json(item) for item in raw_revisions)
            if revisions[-1].revision != definition.current_revision:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "routing profile current_revision does not match persisted history",
                )
            if tuple(item.revision for item in revisions) != tuple(
                range(1, definition.current_revision + 1)
            ):
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "routing profile revisions must be contiguous from revision 1",
                )
            if definition.profile_id in self._definitions:
                raise ContractError(ErrorCode.CONFLICT, "duplicate routing profile in store")
            self._definitions[definition.profile_id] = definition
            for revision in revisions:
                self._validate_loaded_scope(definition, revision)
                self._revisions[(revision.profile_id, revision.revision)] = revision

    def _validate_loaded_scope(
        self,
        definition: ModelRoutingProfileDefinition,
        revision: ModelRoutingProfileRevision,
    ) -> None:
        if (
            revision.profile_id != definition.profile_id
            or revision.owner_ref != definition.owner_ref
            or revision.project_id != definition.project_id
        ):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "persisted routing profile revision has inconsistent identity/scope",
            )

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        profiles: list[JsonValue] = []
        for profile_id in sorted(self._definitions):
            definition = self._definitions[profile_id]
            revisions = [
                self._revisions[(profile_id, revision)]
                for revision in range(1, definition.current_revision + 1)
            ]
            profiles.append(
                {
                    "definition": _definition_to_json(definition),
                    "revisions": [_revision_to_json(item) for item in revisions],
                }
            )
        payload: dict[str, JsonValue] = {
            "schema_version": ROUTING_PROFILE_STORE_SCHEMA_VERSION,
            "profiles": profiles,
        }
        encoded = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, self.path)


def _owner_to_json(owner: OwnerRef) -> dict[str, JsonValue]:
    return {"type": owner.type, "id": owner.id}


def _owner_from_json(value: object) -> OwnerRef:
    if not isinstance(value, dict):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION, "routing profile owner_ref must be an object"
        )
    owner_type = value.get("type")
    owner_id = value.get("id")
    if owner_type not in {"user", "organization", "team", "service"} or not isinstance(
        owner_id, str
    ):
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, "routing profile owner_ref is invalid")
    return OwnerRef(
        type=cast(Literal["user", "organization", "team", "service"], owner_type),
        id=owner_id,
    )


def _requirements_to_json(value: RoutingRequirements) -> dict[str, JsonValue]:
    return {
        "explicit_model_id": value.explicit_model_id,
        "min_context_window": value.min_context_window,
        "tool_calling": value.tool_calling,
        "structured_output": value.structured_output,
        "streaming": value.streaming,
        "modalities": list(value.modalities),
        "reasoning": list(value.reasoning),
        "local_only": value.local_only,
        "self_hosted_only": value.self_hosted_only,
    }


def _requirements_from_json(value: object) -> RoutingRequirements:
    if not isinstance(value, dict):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION, "routing requirements must be an object"
        )
    try:
        return RoutingRequirements(
            explicit_model_id=_optional_string(value, "explicit_model_id"),
            min_context_window=_optional_positive_int(value, "min_context_window"),
            tool_calling=_boolean(value, "tool_calling"),
            structured_output=_boolean(value, "structured_output"),
            streaming=_boolean(value, "streaming"),
            modalities=_string_tuple(value, "modalities"),
            reasoning=_string_tuple(value, "reasoning"),
            local_only=_boolean(value, "local_only"),
            self_hosted_only=_boolean(value, "self_hosted_only"),
        )
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION, f"invalid routing requirements: {exc}"
        ) from exc


def _provenance_to_json(value: Provenance | None) -> JsonValue:
    if value is None:
        return None
    return {"source": value.source, "actor_ref": value.actor_ref, "details": dict(value.details)}


def _provenance_from_json(value: object) -> Provenance | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION, "routing profile provenance must be an object"
        )
    source = value.get("source")
    actor_ref = value.get("actor_ref")
    details = value.get("details", {})
    if (
        not isinstance(source, str)
        or (actor_ref is not None and not isinstance(actor_ref, str))
        or not isinstance(details, dict)
    ):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION, "routing profile provenance is invalid"
        )
    return Provenance(source=source, actor_ref=actor_ref, details=details)


def _definition_to_json(value: ModelRoutingProfileDefinition) -> dict[str, JsonValue]:
    return {
        "profile_id": value.profile_id,
        "owner_ref": _owner_to_json(value.owner_ref),
        "project_id": value.project_id,
        "current_revision": value.current_revision,
        "enabled": value.enabled,
        "created_at": value.created_at.isoformat(),
        "updated_at": value.updated_at.isoformat(),
        "schema_version": value.schema_version,
    }


def _definition_from_json(value: object) -> ModelRoutingProfileDefinition:
    if not isinstance(value, dict):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION, "routing profile definition must be an object"
        )
    try:
        return ModelRoutingProfileDefinition(
            profile_id=_required_string(value, "profile_id"),
            owner_ref=_owner_from_json(value.get("owner_ref")),
            project_id=_optional_string(value, "project_id"),
            current_revision=_required_positive_int(value, "current_revision"),
            enabled=_boolean(value, "enabled"),
            created_at=_datetime(value, "created_at"),
            updated_at=_datetime(value, "updated_at"),
            schema_version=_required_string(value, "schema_version"),
        )
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION, f"invalid routing profile definition: {exc}"
        ) from exc


def _revision_to_json(value: ModelRoutingProfileRevision) -> dict[str, JsonValue]:
    return {
        "profile_id": value.profile_id,
        "revision": value.revision,
        "name": value.name,
        "description": value.description,
        "owner_ref": _owner_to_json(value.owner_ref),
        "project_id": value.project_id,
        "policy": {
            "requirements": _requirements_to_json(value.policy.requirements),
            "preferred_model_ids": list(value.policy.preferred_model_ids),
            "fallback": value.policy.fallback.value,
        },
        "provenance": _provenance_to_json(value.provenance),
        "created_at": value.created_at.isoformat(),
        "schema_version": value.schema_version,
    }


def _revision_from_json(value: object) -> ModelRoutingProfileRevision:
    if not isinstance(value, dict):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION, "routing profile revision must be an object"
        )
    policy = value.get("policy")
    if not isinstance(policy, dict):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION, "routing profile policy must be an object"
        )
    try:
        fallback_raw = _required_string(policy, "fallback")
        return ModelRoutingProfileRevision(
            profile_id=_required_string(value, "profile_id"),
            revision=_required_positive_int(value, "revision"),
            name=_required_string(value, "name"),
            description=_optional_string(value, "description") or "",
            owner_ref=_owner_from_json(value.get("owner_ref")),
            project_id=_optional_string(value, "project_id"),
            policy=ModelRoutingProfilePolicy(
                requirements=_requirements_from_json(policy.get("requirements")),
                preferred_model_ids=_string_tuple(policy, "preferred_model_ids"),
                fallback=RoutingProfileFallbackPolicy(fallback_raw),
            ),
            provenance=_provenance_from_json(value.get("provenance")),
            created_at=_datetime(value, "created_at"),
            schema_version=_required_string(value, "schema_version"),
        )
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION, f"invalid routing profile revision: {exc}"
        ) from exc


def _required_string(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return item


def _optional_string(value: Mapping[str, object], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str):
        raise ValueError(f"{key} must be a string")
    return item


def _boolean(value: Mapping[str, object], key: str) -> bool:
    item = value.get(key)
    if not isinstance(item, bool):
        raise ValueError(f"{key} must be a boolean")
    return item


def _required_positive_int(value: Mapping[str, object], key: str) -> int:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, int) or item < 1:
        raise ValueError(f"{key} must be a positive integer")
    return item


def _optional_positive_int(value: Mapping[str, object], key: str) -> int | None:
    item = value.get(key)
    if item is None:
        return None
    if isinstance(item, bool) or not isinstance(item, int) or item < 1:
        raise ValueError(f"{key} must be a positive integer")
    return item


def _string_tuple(value: Mapping[str, object], key: str) -> tuple[str, ...]:
    item = value.get(key, [])
    if not isinstance(item, list) or any(not isinstance(entry, str) for entry in item):
        raise ValueError(f"{key} must be a list of strings")
    return tuple(cast(list[str], item))


def _datetime(value: Mapping[str, object], key: str) -> datetime:
    item = value.get(key)
    if not isinstance(item, str):
        raise ValueError(f"{key} must be an ISO-8601 timestamp")
    parsed = datetime.fromisoformat(item)
    if parsed.tzinfo is None:
        raise ValueError(f"{key} must include timezone information")
    return parsed
