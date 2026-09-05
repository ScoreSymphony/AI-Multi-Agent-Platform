"""Durable JSON persistence for canonical authorization policy profiles."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef

from .authorization import ActorType, AuthorizationAction, ResourceType
from .policy_profiles import (
    AuthorizationPolicyAssignment,
    AuthorizationPolicyConditions,
    AuthorizationPolicyProfileContent,
    AuthorizationPolicyProfileDefinition,
    AuthorizationPolicyProfileRef,
    AuthorizationPolicyProfileRevision,
    AuthorizationPolicyProvenance,
    AuthorizationPolicyScopeConstraints,
    InMemoryAuthorizationPolicyProfileRepository,
)

POLICY_PROFILE_REPOSITORY_SCHEMA_VERSION = "1"

OwnerType = Literal["user", "organization", "team", "service"]
_OWNER_TYPES = frozenset({"user", "organization", "team", "service"})


class JsonAuthorizationPolicyProfileRepository(InMemoryAuthorizationPolicyProfileRepository):
    """Persist complete immutable policy histories and exact-revision assignments atomically."""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        if self.path.exists():
            self._restore()

    def create_profile(
        self,
        definition: AuthorizationPolicyProfileDefinition,
        revision: AuthorizationPolicyProfileRevision,
    ) -> None:
        super().create_profile(definition, revision)
        self._save()

    def append_revision(
        self,
        definition: AuthorizationPolicyProfileDefinition,
        revision: AuthorizationPolicyProfileRevision,
    ) -> None:
        super().append_revision(definition, revision)
        self._save()

    def set_enabled(self, definition: AuthorizationPolicyProfileDefinition) -> None:
        super().set_enabled(definition)
        self._save()

    def create_assignment(self, assignment: AuthorizationPolicyAssignment) -> None:
        super().create_assignment(assignment)
        self._save()

    def _save(self) -> None:
        document: dict[str, JsonValue] = {
            "schema_version": POLICY_PROFILE_REPOSITORY_SCHEMA_VERSION,
            "profiles": [_definition_to_json(item) for item in self.list_profiles()],
            "revisions": [
                policy_profile_revision_to_json(revision)
                for definition in self.list_profiles()
                for revision in self.list_revisions(definition.policy_profile_id)
            ],
            "assignments": [_assignment_to_json(item) for item in self.list_assignments()],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def _restore(self) -> None:
        raw: object = json.loads(self.path.read_text(encoding="utf-8"))
        document = _object(raw, "policy profile repository document")
        version = _required_string(document, "schema_version")
        if version != POLICY_PROFILE_REPOSITORY_SCHEMA_VERSION:
            raise ValueError(
                "unsupported authorization policy profile repository schema version: "
                f"{version!r}; expected {POLICY_PROFILE_REPOSITORY_SCHEMA_VERSION!r}"
            )

        definitions = tuple(_definition(item) for item in _required_array(document, "profiles"))
        revisions = tuple(
            policy_profile_revision_from_json(item)
            for item in _required_array(document, "revisions")
        )
        assignments = tuple(_assignment(item) for item in _required_array(document, "assignments"))

        histories: dict[str, list[AuthorizationPolicyProfileRevision]] = {}
        for revision in revisions:
            histories.setdefault(revision.policy_profile_id, []).append(revision)

        for definition in definitions:
            history = sorted(
                histories.pop(definition.policy_profile_id, []),
                key=lambda item: item.revision,
            )
            self._restore_profile(definition, history)
        if histories:
            raise ValueError("policy profile repository contains revisions without definitions")

        for assignment in assignments:
            self._restore_assignment(assignment)

    def _restore_profile(
        self,
        definition: AuthorizationPolicyProfileDefinition,
        history: list[AuthorizationPolicyProfileRevision],
    ) -> None:
        if not history or history[-1].revision != definition.current_revision:
            raise ValueError("policy profile definition does not match persisted revision history")
        expected = list(range(1, definition.current_revision + 1))
        if [item.revision for item in history] != expected:
            raise ValueError("policy profile revision history is not contiguous")
        if any(item.policy_profile_id != definition.policy_profile_id for item in history):
            raise ValueError("policy profile revision history contains mismatched profile IDs")

        for index, revision in enumerate(history):
            interim = replace(
                definition,
                current_revision=revision.revision,
                enabled=True,
                updated_at=max(definition.created_at, revision.created_at),
            )
            if index == 0:
                InMemoryAuthorizationPolicyProfileRepository.create_profile(
                    self,
                    interim,
                    revision,
                )
            else:
                InMemoryAuthorizationPolicyProfileRepository.append_revision(
                    self,
                    interim,
                    revision,
                )

        restored = self.get_profile(definition.policy_profile_id)
        if restored.current_revision != definition.current_revision:
            raise ValueError("restored policy profile revision metadata is inconsistent")
        InMemoryAuthorizationPolicyProfileRepository.set_enabled(self, definition)

    def _restore_assignment(self, assignment: AuthorizationPolicyAssignment) -> None:
        """Restore historical assignment even when its profile is now disabled."""

        self.get_profile(assignment.profile_ref.policy_profile_id)
        self.get_revision(
            assignment.profile_ref.policy_profile_id,
            assignment.profile_ref.revision,
        )
        if assignment.assignment_id in self._assignments:
            raise ValueError("policy profile repository contains duplicate assignment IDs")
        key = (assignment.principal_ref, assignment.profile_ref)
        if any((item.principal_ref, item.profile_ref) == key for item in self._assignments.values()):
            raise ValueError("policy profile repository contains duplicate exact assignments")
        self._assignments[assignment.assignment_id] = assignment


def policy_profile_revision_to_json(
    item: AuthorizationPolicyProfileRevision,
) -> dict[str, JsonValue]:
    """Canonical provider-neutral serialization of one immutable policy revision."""

    content = item.content
    scope = content.scope_constraints
    conditions = content.conditions
    provenance = content.provenance
    return {
        "policy_profile_id": item.policy_profile_id,
        "revision": item.revision,
        "owner_ref": _owner_to_json(item.owner_ref),
        "project_id": item.project_id,
        "organization_id": item.organization_id,
        "team_id": item.team_id,
        "created_at": item.created_at.isoformat(),
        "content": {
            "name": content.name,
            "description": content.description,
            "allowed_actions": [value.value for value in content.allowed_actions],
            "approval_required_actions": [
                value.value for value in content.approval_required_actions
            ],
            "resource_types": [value.value for value in content.resource_types],
            "scope_constraints": {
                "project_ids": list(scope.project_ids),
                "organization_ids": list(scope.organization_ids),
                "team_ids": list(scope.team_ids),
                "workspace_ids": list(scope.workspace_ids),
                "resource_ids": list(scope.resource_ids),
            },
            "conditions": {
                "required_security_labels": list(conditions.required_security_labels),
                "allowed_node_ids": list(conditions.allowed_node_ids),
                "allowed_side_effects": list(conditions.allowed_side_effects),
            },
            "provenance": {
                "created_by": provenance.created_by,
                "source": provenance.source,
                "source_reference": provenance.source_reference,
                "imported": provenance.imported,
                "trusted": provenance.trusted,
            },
            "schema_version": content.schema_version,
        },
    }


def policy_profile_revision_from_json(value: object) -> AuthorizationPolicyProfileRevision:
    item = _object(value, "policy profile revision")
    content = _object(_required(item, "content"), "policy profile content")
    scope = _object(_required(content, "scope_constraints"), "scope_constraints")
    conditions = _object(_required(content, "conditions"), "conditions")
    provenance = _object(_required(content, "provenance"), "provenance")
    return AuthorizationPolicyProfileRevision(
        policy_profile_id=_required_string(item, "policy_profile_id"),
        revision=_required_int(item, "revision"),
        owner_ref=_owner(_required(item, "owner_ref")),
        project_id=_optional_string(item, "project_id"),
        organization_id=_optional_string(item, "organization_id"),
        team_id=_optional_string(item, "team_id"),
        created_at=_datetime(_required_string(item, "created_at")),
        content=AuthorizationPolicyProfileContent(
            name=_required_string(content, "name"),
            description=_required_string_allow_blank(content, "description"),
            allowed_actions=tuple(
                AuthorizationAction(value)
                for value in _string_array(content, "allowed_actions")
            ),
            approval_required_actions=tuple(
                AuthorizationAction(value)
                for value in _string_array(content, "approval_required_actions")
            ),
            resource_types=tuple(
                ResourceType(value) for value in _string_array(content, "resource_types")
            ),
            scope_constraints=AuthorizationPolicyScopeConstraints(
                project_ids=_string_array(scope, "project_ids"),
                organization_ids=_string_array(scope, "organization_ids"),
                team_ids=_string_array(scope, "team_ids"),
                workspace_ids=_string_array(scope, "workspace_ids"),
                resource_ids=_string_array(scope, "resource_ids"),
            ),
            conditions=AuthorizationPolicyConditions(
                required_security_labels=_string_array(
                    conditions,
                    "required_security_labels",
                ),
                allowed_node_ids=_string_array(conditions, "allowed_node_ids"),
                allowed_side_effects=_string_array(conditions, "allowed_side_effects"),
            ),
            provenance=AuthorizationPolicyProvenance(
                created_by=_required_string(provenance, "created_by"),
                source=_required_string(provenance, "source"),
                source_reference=_optional_string(provenance, "source_reference"),
                imported=_required_bool(provenance, "imported"),
                trusted=_required_bool(provenance, "trusted"),
            ),
            schema_version=_required_string(content, "schema_version"),
        ),
    )


def _definition_to_json(item: AuthorizationPolicyProfileDefinition) -> dict[str, JsonValue]:
    return {
        "policy_profile_id": item.policy_profile_id,
        "owner_ref": _owner_to_json(item.owner_ref),
        "current_revision": item.current_revision,
        "enabled": item.enabled,
        "project_id": item.project_id,
        "organization_id": item.organization_id,
        "team_id": item.team_id,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def _definition(value: object) -> AuthorizationPolicyProfileDefinition:
    item = _object(value, "policy profile definition")
    return AuthorizationPolicyProfileDefinition(
        policy_profile_id=_required_string(item, "policy_profile_id"),
        owner_ref=_owner(_required(item, "owner_ref")),
        current_revision=_required_int(item, "current_revision"),
        enabled=_required_bool(item, "enabled"),
        project_id=_optional_string(item, "project_id"),
        organization_id=_optional_string(item, "organization_id"),
        team_id=_optional_string(item, "team_id"),
        created_at=_datetime(_required_string(item, "created_at")),
        updated_at=_datetime(_required_string(item, "updated_at")),
    )


def _assignment_to_json(item: AuthorizationPolicyAssignment) -> dict[str, JsonValue]:
    return {
        "assignment_id": item.assignment_id,
        "profile_ref": {
            "policy_profile_id": item.profile_ref.policy_profile_id,
            "revision": item.profile_ref.revision,
        },
        "principal_ref": item.principal_ref,
        "actor_types": [value.value for value in item.actor_types],
        "assigned_by": item.assigned_by,
        "project_id": item.project_id,
        "organization_id": item.organization_id,
        "team_id": item.team_id,
        "created_at": item.created_at.isoformat(),
    }


def _assignment(value: object) -> AuthorizationPolicyAssignment:
    item = _object(value, "policy profile assignment")
    profile_ref = _object(_required(item, "profile_ref"), "profile_ref")
    return AuthorizationPolicyAssignment(
        assignment_id=_required_string(item, "assignment_id"),
        profile_ref=AuthorizationPolicyProfileRef(
            policy_profile_id=_required_string(profile_ref, "policy_profile_id"),
            revision=_required_int(profile_ref, "revision"),
        ),
        principal_ref=_required_string(item, "principal_ref"),
        actor_types=tuple(ActorType(value) for value in _string_array(item, "actor_types")),
        assigned_by=_required_string(item, "assigned_by"),
        project_id=_optional_string(item, "project_id"),
        organization_id=_optional_string(item, "organization_id"),
        team_id=_optional_string(item, "team_id"),
        created_at=_datetime(_required_string(item, "created_at")),
    )


def _owner_to_json(item: OwnerRef) -> dict[str, JsonValue]:
    return {"type": item.type, "id": item.id}


def _owner(value: object) -> OwnerRef:
    item = _object(value, "owner_ref")
    owner_type = _required_string(item, "type")
    if owner_type not in _OWNER_TYPES:
        raise ValueError("owner_ref.type is invalid")
    return OwnerRef(
        type=cast(OwnerType, owner_type),
        id=_required_string(item, "id"),
    )


def _object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{name} keys must be strings")
    return cast(dict[str, object], value)


def _required(item: Mapping[str, object], key: str) -> object:
    if key not in item:
        raise ValueError(f"missing required field: {key}")
    return item[key]


def _required_string(item: Mapping[str, object], key: str) -> str:
    value = _required(item, key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return value


def _required_string_allow_blank(item: Mapping[str, object], key: str) -> str:
    value = _required(item, key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_string(item: Mapping[str, object], key: str) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string or null")
    return value


def _required_int(item: Mapping[str, object], key: str) -> int:
    value = _required(item, key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value


def _required_bool(item: Mapping[str, object], key: str) -> bool:
    value = _required(item, key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _required_array(item: Mapping[str, object], key: str) -> list[object]:
    value = _required(item, key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be an array")
    return value


def _string_array(item: Mapping[str, object], key: str) -> tuple[str, ...]:
    values = _required_array(item, key)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"{key} must contain non-blank strings")
    return tuple(cast(str, value) for value in values)


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("persisted timestamps must be timezone-aware")
    return parsed


__all__ = [
    "POLICY_PROFILE_REPOSITORY_SCHEMA_VERSION",
    "JsonAuthorizationPolicyProfileRepository",
    "policy_profile_revision_from_json",
    "policy_profile_revision_to_json",
]
