"""Durable JSON persistence for canonical authorization policy profiles."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef

from .authorization import ActorType, AuthorizationAction, ResourceType
from .policy_profiles import (
    POLICY_PROFILE_SCHEMA_VERSION,
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
    """Atomically persist profiles, immutable revisions and exact-revision assignments."""

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

    def import_profile(
        self,
        definition: AuthorizationPolicyProfileDefinition,
        revisions: tuple[AuthorizationPolicyProfileRevision, ...],
    ) -> None:
        """Persist one complete imported history with in-memory rollback on write failure."""

        super().import_profile(definition, revisions)
        try:
            self._save()
        except Exception:
            InMemoryAuthorizationPolicyProfileRepository.delete_profile(
                self,
                definition.policy_profile_id,
            )
            raise

    def delete_profile(self, policy_profile_id: str) -> None:
        definition = self.get_profile(policy_profile_id)
        revisions = self.list_revisions(policy_profile_id)
        super().delete_profile(policy_profile_id)
        try:
            self._save()
        except Exception:
            InMemoryAuthorizationPolicyProfileRepository.import_profile(
                self,
                definition,
                revisions,
            )
            raise

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
                _revision_to_json(revision)
                for profile in self.list_profiles()
                for revision in self.list_revisions(profile.policy_profile_id)
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
        revisions = tuple(_revision(item) for item in _required_array(document, "revisions"))
        assignments = tuple(_assignment(item) for item in _required_array(document, "assignments"))

        histories: dict[str, list[AuthorizationPolicyProfileRevision]] = {}
        for revision in revisions:
            histories.setdefault(revision.policy_profile_id, []).append(revision)

        for definition in definitions:
            history = tuple(
                sorted(
                    histories.pop(definition.policy_profile_id, []),
                    key=lambda item: item.revision,
                )
            )
            InMemoryAuthorizationPolicyProfileRepository.import_profile(
                self,
                definition,
                history,
            )
        if histories:
            raise ValueError("policy profile repository contains revisions without definitions")

        for assignment in assignments:
            self._restore_assignment(assignment)

    def _restore_assignment(self, assignment: AuthorizationPolicyAssignment) -> None:
        if assignment.assignment_id in self._assignments:
            raise ValueError("policy profile repository contains duplicate assignments")
        self.get_revision(
            assignment.profile_ref.policy_profile_id,
            assignment.profile_ref.revision,
        )
        key = (assignment.principal_ref, assignment.profile_ref)
        if any(
            (item.principal_ref, item.profile_ref) == key
            for item in self._assignments.values()
        ):
            raise ValueError("policy profile repository contains duplicate profile assignments")
        self._assignments[assignment.assignment_id] = assignment


def policy_profile_revision_to_json(
    revision: AuthorizationPolicyProfileRevision,
) -> dict[str, JsonValue]:
    """Canonical provider-neutral serialization used by persistence and portability."""

    return _revision_to_json(revision)


def policy_profile_revision_from_json(value: JsonValue) -> AuthorizationPolicyProfileRevision:
    """Decode only the canonical schema; provider-private policy objects are unsupported."""

    return _revision(value)


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


def _revision_to_json(item: AuthorizationPolicyProfileRevision) -> dict[str, JsonValue]:
    return {
        "schema_version": POLICY_PROFILE_SCHEMA_VERSION,
        "policy_profile_id": item.policy_profile_id,
        "revision": item.revision,
        "owner_ref": _owner_to_json(item.owner_ref),
        "content": _content_to_json(item.content),
        "project_id": item.project_id,
        "organization_id": item.organization_id,
        "team_id": item.team_id,
        "created_at": item.created_at.isoformat(),
    }


def _content_to_json(item: AuthorizationPolicyProfileContent) -> dict[str, JsonValue]:
    return {
        "name": item.name,
        "description": item.description,
        "allowed_actions": [value.value for value in item.allowed_actions],
        "approval_required_actions": [value.value for value in item.approval_required_actions],
        "resource_types": [value.value for value in item.resource_types],
        "scope_constraints": {
            "project_ids": list(item.scope_constraints.project_ids),
            "organization_ids": list(item.scope_constraints.organization_ids),
            "team_ids": list(item.scope_constraints.team_ids),
            "workspace_ids": list(item.scope_constraints.workspace_ids),
            "resource_ids": list(item.scope_constraints.resource_ids),
        },
        "conditions": {
            "required_security_labels": list(item.conditions.required_security_labels),
            "allowed_node_ids": list(item.conditions.allowed_node_ids),
            "allowed_side_effects": list(item.conditions.allowed_side_effects),
        },
        "provenance": {
            "created_by": item.provenance.created_by,
            "source": item.provenance.source,
            "source_reference": item.provenance.source_reference,
            "imported": item.provenance.imported,
            "trusted": item.provenance.trusted,
        },
        "schema_version": item.schema_version,
    }


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


def _revision(value: object) -> AuthorizationPolicyProfileRevision:
    item = _object(value, "policy profile revision")
    schema_version = _required_string(item, "schema_version")
    if schema_version != POLICY_PROFILE_SCHEMA_VERSION:
        raise ValueError(f"unsupported canonical policy profile schema version: {schema_version!r}")
    return AuthorizationPolicyProfileRevision(
        policy_profile_id=_required_string(item, "policy_profile_id"),
        revision=_required_int(item, "revision"),
        owner_ref=_owner(_required(item, "owner_ref")),
        content=_content(_required(item, "content")),
        project_id=_optional_string(item, "project_id"),
        organization_id=_optional_string(item, "organization_id"),
        team_id=_optional_string(item, "team_id"),
        created_at=_datetime(_required_string(item, "created_at")),
    )


def _content(value: object) -> AuthorizationPolicyProfileContent:
    item = _object(value, "policy profile content")
    scope = _object(_required(item, "scope_constraints"), "scope_constraints")
    conditions = _object(_required(item, "conditions"), "conditions")
    provenance = _object(_required(item, "provenance"), "provenance")
    source_reference = _optional_string(provenance, "source_reference")
    return AuthorizationPolicyProfileContent(
        name=_required_string(item, "name"),
        description=_required_string_allow_blank(item, "description"),
        allowed_actions=tuple(
            AuthorizationAction(value) for value in _string_tuple(item, "allowed_actions")
        ),
        approval_required_actions=tuple(
            AuthorizationAction(value)
            for value in _string_tuple(item, "approval_required_actions")
        ),
        resource_types=tuple(
            ResourceType(value) for value in _string_tuple(item, "resource_types")
        ),
        scope_constraints=AuthorizationPolicyScopeConstraints(
            project_ids=_string_tuple(scope, "project_ids"),
            organization_ids=_string_tuple(scope, "organization_ids"),
            team_ids=_string_tuple(scope, "team_ids"),
            workspace_ids=_string_tuple(scope, "workspace_ids"),
            resource_ids=_string_tuple(scope, "resource_ids"),
        ),
        conditions=AuthorizationPolicyConditions(
            required_security_labels=_string_tuple(conditions, "required_security_labels"),
            allowed_node_ids=_string_tuple(conditions, "allowed_node_ids"),
            allowed_side_effects=_string_tuple(conditions, "allowed_side_effects"),
        ),
        provenance=AuthorizationPolicyProvenance(
            created_by=_required_string(provenance, "created_by"),
            source=_required_string(provenance, "source"),
            source_reference=source_reference,
            imported=_required_bool(provenance, "imported"),
            trusted=_required_bool(provenance, "trusted"),
        ),
        schema_version=_required_string(item, "schema_version"),
    )


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
        actor_types=tuple(ActorType(value) for value in _string_tuple(item, "actor_types")),
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
    return OwnerRef(type=cast(OwnerType, owner_type), id=_required_string(item, "id"))


def _object(value: object, name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{name} keys must be strings")
    return cast(dict[str, JsonValue], value)


def _required(item: dict[str, JsonValue], field: str) -> JsonValue:
    if field not in item:
        raise ValueError(f"missing required field: {field}")
    return item[field]


def _required_array(item: dict[str, JsonValue], field: str) -> list[JsonValue]:
    value = _required(item, field)
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return value


def _required_string(item: dict[str, JsonValue], field: str) -> str:
    value = _required(item, field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    return value


def _required_string_allow_blank(item: dict[str, JsonValue], field: str) -> str:
    value = _required(item, field)
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _optional_string(item: dict[str, JsonValue], field: str) -> str | None:
    value = item.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string or null")
    return value


def _required_int(item: dict[str, JsonValue], field: str) -> int:
    value = _required(item, field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _required_bool(item: dict[str, JsonValue], field: str) -> bool:
    value = _required(item, field)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _string_tuple(item: dict[str, JsonValue], field: str) -> tuple[str, ...]:
    values = _required_array(item, field)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"{field} must contain only non-blank strings")
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
