"""Durable JSON repository for canonical Agents, Agent Teams and Agent runs."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal, cast

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data import MemoryScope
from ai_multi_agent_platform.domain import OwnerRef, Provenance
from ai_multi_agent_platform.models import RoutingRequirements

from .models import (
    AgentCapabilityPolicy,
    AgentDataAccess,
    AgentDefinition,
    AgentInstructions,
    AgentModelPolicy,
    AgentPolicyHooks,
    AgentProfile,
    AgentRevision,
    AgentRevisionRef,
    AgentRunRecord,
    AgentRunStatus,
    AgentTeamDefinition,
    AgentTeamMember,
    AgentTeamProfile,
    AgentTeamRevision,
    AgentTeamRevisionRef,
    AgentWorkspaceDefaults,
    CapabilityConstraint,
    InstructionSource,
    ModelFallbackPolicy,
    UnavailableMemberPolicy,
)
from .repository import InMemoryAgentRepository

AGENT_REPOSITORY_SCHEMA_VERSION = "2"
_LEGACY_AGENT_REPOSITORY_SCHEMA_VERSION = "1"


class JsonAgentRepository(InMemoryAgentRepository):
    """Durable reference repository using one atomically replaced JSON snapshot.

    Live objects use the same validation and revision rules as
    :class:`InMemoryAgentRepository`. Every successful mutation rewrites the canonical
    snapshot. Constructing a new repository for the same path restores stable identities,
    all immutable Agent/Team revisions and AgentRun revision pins.
    """

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        if self.path.exists():
            self._restore()

    def create_agent(self, definition: AgentDefinition, revision: AgentRevision) -> None:
        super().create_agent(definition, revision)
        self._save()

    def update_agent(self, definition: AgentDefinition, revision: AgentRevision) -> None:
        super().update_agent(definition, revision)
        self._save()

    def create_team(self, definition: AgentTeamDefinition, revision: AgentTeamRevision) -> None:
        super().create_team(definition, revision)
        self._save()

    def update_team(self, definition: AgentTeamDefinition, revision: AgentTeamRevision) -> None:
        super().update_team(definition, revision)
        self._save()

    def create_agent_run(self, record: AgentRunRecord) -> None:
        super().create_agent_run(record)
        self._save()

    def update_agent_run(self, record: AgentRunRecord) -> None:
        super().update_agent_run(record)
        self._save()

    def _save(self) -> None:
        document: dict[str, JsonValue] = {
            "schema_version": AGENT_REPOSITORY_SCHEMA_VERSION,
            "agents": [_encode(item) for item in self.list_agents()],
            "agent_revisions": [
                _encode(revision)
                for agent in self.list_agents()
                for revision in self.list_agent_revisions(agent.agent_id)
            ],
            "teams": [_encode(item) for item in self.list_teams()],
            "team_revisions": [
                _encode(revision)
                for team in self.list_teams()
                for revision in self.list_team_revisions(team.team_id)
            ],
            "agent_runs": [_encode(item) for item in self.list_agent_runs()],
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
        document = _json_object(raw, "agent repository document")
        version = _required_string(document, "schema_version")
        if version == _LEGACY_AGENT_REPOSITORY_SCHEMA_VERSION:
            document = _migrate_v1_to_v2(document)
        elif version != AGENT_REPOSITORY_SCHEMA_VERSION:
            raise ValueError(
                "unsupported Agent repository schema version: "
                f"{version!r}; expected {AGENT_REPOSITORY_SCHEMA_VERSION!r}"
            )

        definitions = tuple(_agent_definition(item) for item in _required_array(document, "agents"))
        revisions = tuple(
            _agent_revision(item) for item in _required_array(document, "agent_revisions")
        )
        team_definitions = tuple(
            _team_definition(item) for item in _required_array(document, "teams")
        )
        team_revisions = tuple(
            _team_revision(item) for item in _required_array(document, "team_revisions")
        )
        runs = tuple(_agent_run(item) for item in _required_array(document, "agent_runs"))

        revisions_by_agent: dict[str, list[AgentRevision]] = {}
        for agent_revision in revisions:
            revisions_by_agent.setdefault(agent_revision.agent_id, []).append(agent_revision)
        for agent_definition in definitions:
            agent_history = sorted(
                revisions_by_agent.pop(agent_definition.agent_id, []),
                key=lambda item: item.revision,
            )
            self._restore_agent(agent_definition, agent_history)
        if revisions_by_agent:
            raise ValueError("Agent repository contains revisions without Agent definitions")

        revisions_by_team: dict[str, list[AgentTeamRevision]] = {}
        for team_revision in team_revisions:
            revisions_by_team.setdefault(team_revision.team_id, []).append(team_revision)
        for team_definition in team_definitions:
            team_history = sorted(
                revisions_by_team.pop(team_definition.team_id, []),
                key=lambda item: item.revision,
            )
            self._restore_team(team_definition, team_history)
        if revisions_by_team:
            raise ValueError("Agent repository contains Team revisions without Team definitions")

        for record in runs:
            InMemoryAgentRepository.create_agent_run(self, record)

    def _restore_agent(
        self,
        definition: AgentDefinition,
        history: list[AgentRevision],
    ) -> None:
        if not history or history[-1].revision != definition.current_revision:
            raise ValueError("Agent definition does not match persisted revision history")
        if [item.revision for item in history] != list(range(1, definition.current_revision + 1)):
            raise ValueError("Agent revision history is not contiguous")

        first = history[0]
        initial = AgentDefinition(
            agent_id=definition.agent_id,
            owner_ref=first.owner_ref,
            current_revision=1,
            project_id=first.project_id,
            workspace_id=first.workspace_id,
            created_at=definition.created_at,
            updated_at=first.created_at,
        )
        InMemoryAgentRepository.create_agent(self, initial, first)
        for revision in history[1:]:
            is_current = revision.revision == definition.current_revision
            replayed = AgentDefinition(
                agent_id=definition.agent_id,
                owner_ref=revision.owner_ref,
                current_revision=revision.revision,
                project_id=revision.project_id,
                workspace_id=revision.workspace_id,
                created_at=definition.created_at,
                updated_at=definition.updated_at if is_current else revision.created_at,
            )
            InMemoryAgentRepository.update_agent(self, replayed, revision)

        if self.get_agent(definition.agent_id) != definition:
            raise ValueError(
                "restored Agent definition differs from persisted canonical definition"
            )

    def _restore_team(
        self,
        definition: AgentTeamDefinition,
        history: list[AgentTeamRevision],
    ) -> None:
        if not history or history[-1].revision != definition.current_revision:
            raise ValueError("Agent Team definition does not match persisted revision history")
        if [item.revision for item in history] != list(range(1, definition.current_revision + 1)):
            raise ValueError("Agent Team revision history is not contiguous")

        first = history[0]
        initial = AgentTeamDefinition(
            team_id=definition.team_id,
            owner_ref=first.owner_ref,
            current_revision=1,
            project_id=first.project_id,
            workspace_id=first.workspace_id,
            created_at=definition.created_at,
            updated_at=first.created_at,
        )
        InMemoryAgentRepository.create_team(self, initial, first)
        for revision in history[1:]:
            is_current = revision.revision == definition.current_revision
            replayed = AgentTeamDefinition(
                team_id=definition.team_id,
                owner_ref=revision.owner_ref,
                current_revision=revision.revision,
                project_id=revision.project_id,
                workspace_id=revision.workspace_id,
                created_at=definition.created_at,
                updated_at=definition.updated_at if is_current else revision.created_at,
            )
            InMemoryAgentRepository.update_team(self, replayed, revision)

        if self.get_team(definition.team_id) != definition:
            raise ValueError("restored Agent Team differs from persisted canonical definition")


def _migrate_v1_to_v2(document: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Add fields introduced by schema v2 without changing v1 semantic defaults."""

    migrated = dict(document)
    migrated_team_revisions: list[JsonValue] = []
    for raw_revision in _required_array(document, "team_revisions"):
        revision = _json_object(raw_revision, "Agent Team revision")
        profile = _json_object(revision.get("profile"), "Agent Team profile")
        migrated_profile = dict(profile)
        migrated_profile.setdefault("shared_resource_refs", [])
        migrated_team_revisions.append({**revision, "profile": migrated_profile})

    migrated_runs: list[JsonValue] = []
    for raw_run in _required_array(document, "agent_runs"):
        run = _json_object(raw_run, "Agent run")
        migrated_run = dict(run)
        migrated_run.setdefault("capability_versions", {})
        migrated_runs.append(migrated_run)

    migrated["team_revisions"] = migrated_team_revisions
    migrated["agent_runs"] = migrated_runs
    migrated["schema_version"] = AGENT_REPOSITORY_SCHEMA_VERSION
    return migrated


def _encode(value: Any) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Enum):
        return _encode(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _encode(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        encoded: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("Agent persistence mappings require string keys")
            encoded[key] = _encode(item)
        return encoded
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_encode(item) for item in value]
    raise TypeError(f"unsupported Agent persistence value: {type(value).__name__}")


def _agent_definition(value: JsonValue) -> AgentDefinition:
    data = _json_object(value, "Agent definition")
    return AgentDefinition(
        agent_id=_required_string(data, "agent_id"),
        owner_ref=_owner_ref(data.get("owner_ref")),
        current_revision=_required_int(data, "current_revision"),
        project_id=_optional_string(data, "project_id"),
        workspace_id=_optional_string(data, "workspace_id"),
        created_at=_datetime(data.get("created_at"), "created_at"),
        updated_at=_datetime(data.get("updated_at"), "updated_at"),
    )


def _agent_revision(value: JsonValue) -> AgentRevision:
    data = _json_object(value, "Agent revision")
    return AgentRevision(
        agent_id=_required_string(data, "agent_id"),
        revision=_required_int(data, "revision"),
        profile=_agent_profile(data.get("profile")),
        owner_ref=_owner_ref(data.get("owner_ref")),
        project_id=_optional_string(data, "project_id"),
        workspace_id=_optional_string(data, "workspace_id"),
        created_at=_datetime(data.get("created_at"), "created_at"),
        provenance=_provenance(data.get("provenance")),
    )


def _agent_profile(value: JsonValue | None) -> AgentProfile:
    data = _json_object(value, "Agent profile")
    instructions = _json_object(data.get("instructions"), "Agent instructions")
    model = _json_object(data.get("model"), "Agent model policy")
    capabilities = _json_object(data.get("capabilities"), "Agent capability policy")
    data_access = _json_object(data.get("data_access"), "Agent data access")
    workspace = _json_object(data.get("workspace_defaults"), "Agent workspace defaults")
    hooks = _json_object(data.get("policy_hooks"), "Agent policy hooks")
    return AgentProfile(
        name=_required_string(data, "name"),
        role=_required_string(data, "role"),
        instructions=AgentInstructions(
            role=_instruction_source(instructions.get("role")),
            platform_constraint_refs=_string_tuple(
                instructions.get("platform_constraint_refs"), "platform_constraint_refs"
            ),
            project_instruction_refs=_string_tuple(
                instructions.get("project_instruction_refs"), "project_instruction_refs"
            ),
        ),
        description=_required_string_allow_empty(data, "description"),
        model=AgentModelPolicy(
            requirements=_routing_requirements(model.get("requirements")),
            routing_profile_ref=_optional_string(model, "routing_profile_ref"),
            allow_task_override=_required_bool(model, "allow_task_override"),
            fallback=ModelFallbackPolicy(_required_string(model, "fallback")),
        ),
        capabilities=AgentCapabilityPolicy(
            allowed=_string_tuple(capabilities.get("allowed"), "allowed"),
            denied=_string_tuple(capabilities.get("denied"), "denied"),
            constraints=tuple(
                _capability_constraint(item)
                for item in _required_array(capabilities, "constraints")
            ),
        ),
        data_access=AgentDataAccess(
            memory_scopes=tuple(
                MemoryScope(item)
                for item in _string_tuple(data_access.get("memory_scopes"), "memory_scopes")
            ),
            memory_config_refs=_string_tuple(
                data_access.get("memory_config_refs"), "memory_config_refs"
            ),
            knowledge_source_ids=_string_tuple(
                data_access.get("knowledge_source_ids"), "knowledge_source_ids"
            ),
            allow_user_memory=_required_bool(data_access, "allow_user_memory"),
        ),
        workspace_defaults=AgentWorkspaceDefaults(
            project_id=_optional_string(workspace, "project_id"),
            workspace_id=_optional_string(workspace, "workspace_id"),
        ),
        policy_hooks=AgentPolicyHooks(
            authorization_profile_ref=_optional_string(hooks, "authorization_profile_ref"),
            verification_policy_refs=_string_tuple(
                hooks.get("verification_policy_refs"), "verification_policy_refs"
            ),
        ),
        resource_hints=_json_object(data.get("resource_hints"), "resource_hints"),
        enabled=_required_bool(data, "enabled"),
        metadata=_json_object(data.get("metadata"), "metadata"),
    )


def _instruction_source(value: JsonValue | None) -> InstructionSource:
    data = _json_object(value, "instruction source")
    return InstructionSource(
        content=_optional_string(data, "content"),
        ref=_optional_string(data, "ref"),
        version=_optional_string(data, "version"),
    )


def _routing_requirements(value: JsonValue | None) -> RoutingRequirements:
    data = _json_object(value, "routing requirements")
    return RoutingRequirements(
        explicit_model_id=_optional_string(data, "explicit_model_id"),
        min_context_window=_optional_int(data, "min_context_window"),
        tool_calling=_required_bool(data, "tool_calling"),
        structured_output=_required_bool(data, "structured_output"),
        streaming=_required_bool(data, "streaming"),
        modalities=_string_tuple(data.get("modalities"), "modalities"),
        reasoning=_string_tuple(data.get("reasoning"), "reasoning"),
        local_only=_required_bool(data, "local_only"),
        self_hosted_only=_required_bool(data, "self_hosted_only"),
    )


def _capability_constraint(value: JsonValue) -> CapabilityConstraint:
    data = _json_object(value, "capability constraint")
    return CapabilityConstraint(
        capability_id=_required_string(data, "capability_id"),
        required=_required_bool(data, "required"),
        exact_version=_optional_string(data, "exact_version"),
        minimum_version=_optional_string(data, "minimum_version"),
        maximum_version=_optional_string(data, "maximum_version"),
        required_features=_string_tuple(data.get("required_features"), "required_features"),
        approval_ref=_optional_string(data, "approval_ref"),
    )


def _team_definition(value: JsonValue) -> AgentTeamDefinition:
    data = _json_object(value, "Agent Team definition")
    return AgentTeamDefinition(
        team_id=_required_string(data, "team_id"),
        owner_ref=_owner_ref(data.get("owner_ref")),
        current_revision=_required_int(data, "current_revision"),
        project_id=_optional_string(data, "project_id"),
        workspace_id=_optional_string(data, "workspace_id"),
        created_at=_datetime(data.get("created_at"), "created_at"),
        updated_at=_datetime(data.get("updated_at"), "updated_at"),
    )


def _team_revision(value: JsonValue) -> AgentTeamRevision:
    data = _json_object(value, "Agent Team revision")
    return AgentTeamRevision(
        team_id=_required_string(data, "team_id"),
        revision=_required_int(data, "revision"),
        profile=_team_profile(data.get("profile")),
        owner_ref=_owner_ref(data.get("owner_ref")),
        project_id=_optional_string(data, "project_id"),
        workspace_id=_optional_string(data, "workspace_id"),
        created_at=_datetime(data.get("created_at"), "created_at"),
        provenance=_provenance(data.get("provenance")),
    )


def _team_profile(value: JsonValue | None) -> AgentTeamProfile:
    data = _json_object(value, "Agent Team profile")
    return AgentTeamProfile(
        name=_required_string(data, "name"),
        members=tuple(_team_member(item) for item in _required_array(data, "members")),
        description=_required_string_allow_empty(data, "description"),
        coordination_policy_ref=_optional_string(data, "coordination_policy_ref"),
        leader_agent_id=_optional_string(data, "leader_agent_id"),
        shared_capability_ids=_string_tuple(
            data.get("shared_capability_ids"), "shared_capability_ids"
        ),
        shared_resource_refs=_string_tuple(
            data.get("shared_resource_refs"), "shared_resource_refs"
        ),
        max_parallel_agents=_optional_int(data, "max_parallel_agents"),
        max_steps=_optional_int(data, "max_steps"),
        unavailable_member_policy=UnavailableMemberPolicy(
            _required_string(data, "unavailable_member_policy")
        ),
        enabled=_required_bool(data, "enabled"),
        metadata=_json_object(data.get("metadata"), "metadata"),
    )


def _team_member(value: JsonValue) -> AgentTeamMember:
    data = _json_object(value, "Agent Team member")
    agent = _json_object(data.get("agent"), "Agent revision reference")
    return AgentTeamMember(
        agent=AgentRevisionRef(
            agent_id=_required_string(agent, "agent_id"),
            revision=_required_int(agent, "revision"),
        ),
        role=_required_string(data, "role"),
        required=_required_bool(data, "required"),
        can_delegate_to=_string_tuple(data.get("can_delegate_to"), "can_delegate_to"),
    )


def _agent_run(value: JsonValue) -> AgentRunRecord:
    data = _json_object(value, "Agent run")
    agent = _json_object(data.get("agent"), "Agent revision reference")
    raw_team = data.get("team")
    team: AgentTeamRevisionRef | None = None
    if raw_team is not None:
        team_data = _json_object(raw_team, "Agent Team revision reference")
        team = AgentTeamRevisionRef(
            team_id=_required_string(team_data, "team_id"),
            revision=_required_int(team_data, "revision"),
        )
    raw_finished = data.get("finished_at")
    return AgentRunRecord(
        agent_run_id=_required_string(data, "agent_run_id"),
        run_id=_required_string(data, "run_id"),
        task_id=_required_string(data, "task_id"),
        agent=AgentRevisionRef(
            agent_id=_required_string(agent, "agent_id"),
            revision=_required_int(agent, "revision"),
        ),
        status=AgentRunStatus(_required_string(data, "status")),
        team=team,
        selected_model_config_id=_optional_string(data, "selected_model_config_id"),
        selected_provider_id=_optional_string(data, "selected_provider_id"),
        capability_ids=_string_tuple(data.get("capability_ids"), "capability_ids"),
        capability_versions=_string_mapping(data.get("capability_versions"), "capability_versions"),
        orchestrator_adapter_id=_optional_string(data, "orchestrator_adapter_id"),
        orchestrator_runtime_ref=_optional_string(data, "orchestrator_runtime_ref"),
        artifact_ids=_string_tuple(data.get("artifact_ids"), "artifact_ids"),
        result_ids=_string_tuple(data.get("result_ids"), "result_ids"),
        model_call_refs=_string_tuple(data.get("model_call_refs"), "model_call_refs"),
        tool_invocation_refs=_string_tuple(
            data.get("tool_invocation_refs"), "tool_invocation_refs"
        ),
        error=_optional_string(data, "error"),
        telemetry=_json_object(data.get("telemetry"), "telemetry"),
        verification_context=_json_object(data.get("verification_context"), "verification_context"),
        started_at=_datetime(data.get("started_at"), "started_at"),
        finished_at=(None if raw_finished is None else _datetime(raw_finished, "finished_at")),
    )


def _owner_ref(value: JsonValue | None) -> OwnerRef:
    data = _json_object(value, "owner_ref")
    owner_type = _required_string(data, "type")
    if owner_type not in {"user", "organization", "team", "service"}:
        raise ValueError("owner_ref.type is invalid")
    return OwnerRef(
        type=cast(Literal["user", "organization", "team", "service"], owner_type),
        id=_required_string(data, "id"),
    )


def _provenance(value: JsonValue | None) -> Provenance | None:
    if value is None:
        return None
    data = _json_object(value, "provenance")
    return Provenance(
        source=_required_string(data, "source"),
        actor_ref=_optional_string(data, "actor_ref"),
        details=_json_object(data.get("details"), "provenance.details"),
    )


def _json_object(value: object, field_name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    if not all(isinstance(key, str) and _is_json_value(item) for key, item in value.items()):
        raise ValueError(f"{field_name} contains non-JSON values")
    return cast(dict[str, JsonValue], value)


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def _required_array(data: dict[str, JsonValue], key: str) -> list[JsonValue]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a JSON array")
    return value


def _required_string(data: dict[str, JsonValue], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return value


def _required_string_allow_empty(data: dict[str, JsonValue], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_string(data: dict[str, JsonValue], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string or null")
    return value


def _required_int(data: dict[str, JsonValue], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _optional_int(data: dict[str, JsonValue], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer or null")
    return value


def _required_bool(data: dict[str, JsonValue], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be boolean")
    return value


def _string_tuple(value: JsonValue | None, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list of strings")
    return tuple(cast(str, item) for item in value)


def _string_mapping(value: JsonValue | None, field_name: str) -> dict[str, str]:
    data = _json_object(value, field_name)
    if any(not isinstance(item, str) for item in data.values()):
        raise ValueError(f"{field_name} must map strings to strings")
    return {key: cast(str, item) for key, item in data.items()}


def _datetime(value: JsonValue | None, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed
