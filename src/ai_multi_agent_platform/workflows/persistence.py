"""Restart-safe JSON persistence for canonical reusable workflows."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.agents import AgentRevisionRef, AgentTeamRevisionRef
from ai_multi_agent_platform.contracts.types import FrozenJsonValue, JsonValue
from ai_multi_agent_platform.domain import OwnerRef

from .models import (
    WorkflowCapabilityRequirement,
    WorkflowCompatibility,
    WorkflowContent,
    WorkflowDefinition,
    WorkflowParameter,
    WorkflowProvenance,
    WorkflowRevision,
    WorkflowStage,
)
from .repository import InMemoryWorkflowRepository

WORKFLOW_REPOSITORY_SCHEMA_VERSION = "1"


class JsonWorkflowRepository(InMemoryWorkflowRepository):
    """Persist complete immutable workflow histories using atomic file replacement."""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        if self.path.exists():
            self._restore()

    def create_workflow(
        self,
        definition: WorkflowDefinition,
        revision: WorkflowRevision,
    ) -> None:
        super().create_workflow(definition, revision)
        self._save()

    def append_revision(
        self,
        definition: WorkflowDefinition,
        revision: WorkflowRevision,
    ) -> None:
        super().append_revision(definition, revision)
        self._save()

    def _save(self) -> None:
        document: dict[str, JsonValue] = {
            "schema_version": WORKFLOW_REPOSITORY_SCHEMA_VERSION,
            "workflows": [_definition_to_json(item) for item in self.list_workflows()],
            "revisions": [
                _revision_to_json(revision)
                for workflow in self.list_workflows()
                for revision in self.list_revisions(workflow.workflow_id)
            ],
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
        document = _object(raw, "workflow repository document")
        version = _required_string(document, "schema_version")
        if version != WORKFLOW_REPOSITORY_SCHEMA_VERSION:
            raise ValueError(
                "unsupported workflow repository schema version: "
                f"{version!r}; expected {WORKFLOW_REPOSITORY_SCHEMA_VERSION!r}"
            )
        definitions = tuple(_definition(item) for item in _array(document, "workflows"))
        revisions = tuple(_revision(item) for item in _array(document, "revisions"))
        histories: dict[str, list[WorkflowRevision]] = {}
        for revision in revisions:
            histories.setdefault(revision.workflow_id, []).append(revision)

        for definition in definitions:
            history = sorted(
                histories.pop(definition.workflow_id, []),
                key=lambda item: item.revision,
            )
            self._restore_workflow(definition, history)
        if histories:
            raise ValueError("workflow repository contains revisions without definitions")

    def _restore_workflow(
        self,
        definition: WorkflowDefinition,
        history: list[WorkflowRevision],
    ) -> None:
        if not history or history[-1].revision != definition.current_revision:
            raise ValueError("workflow definition does not match persisted revision history")
        expected = list(range(1, definition.current_revision + 1))
        if [item.revision for item in history] != expected:
            raise ValueError("workflow revision history is not contiguous")
        for index, revision in enumerate(history):
            interim = replace(definition, current_revision=revision.revision)
            if index == 0:
                InMemoryWorkflowRepository.create_workflow(self, interim, revision)
            else:
                InMemoryWorkflowRepository.append_revision(self, interim, revision)
        if self.get_workflow(definition.workflow_id) != definition:
            raise ValueError("workflow definition metadata does not match revision history")


def _definition_to_json(item: WorkflowDefinition) -> dict[str, JsonValue]:
    return {
        "workflow_id": item.workflow_id,
        "owner_ref": _owner_to_json(item.owner_ref),
        "current_revision": item.current_revision,
        "project_id": item.project_id,
        "organization_id": item.organization_id,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def _revision_to_json(item: WorkflowRevision) -> dict[str, JsonValue]:
    return {
        "workflow_id": item.workflow_id,
        "revision": item.revision,
        "owner_ref": _owner_to_json(item.owner_ref),
        "content": _content_to_json(item.content),
        "project_id": item.project_id,
        "organization_id": item.organization_id,
        "created_at": item.created_at.isoformat(),
    }


def _content_to_json(item: WorkflowContent) -> dict[str, JsonValue]:
    return {
        "name": item.name,
        "description": item.description,
        "stages": [_stage_to_json(stage) for stage in item.stages],
        "parameters": [
            {
                "name": parameter.name,
                "required": parameter.required,
                "secret_reference": parameter.secret_reference,
                "description": parameter.description,
            }
            for parameter in item.parameters
        ],
        "provenance": {
            "creator": item.provenance.creator,
            "source": item.provenance.source,
            "metadata": _thaw(item.provenance.metadata),
        },
        "compatibility": {
            "schema_version": item.compatibility.schema_version,
            "platform_version_range": item.compatibility.platform_version_range,
            "contract_versions": dict(item.compatibility.contract_versions),
            "provider_agnostic": item.compatibility.provider_agnostic,
            "orchestrator_agnostic": item.compatibility.orchestrator_agnostic,
            "metadata": _thaw(item.compatibility.metadata),
        },
        "metadata": _thaw(item.metadata),
    }


def _stage_to_json(item: WorkflowStage) -> dict[str, JsonValue]:
    agent: JsonValue = None
    if item.agent is not None:
        agent = {"agent_id": item.agent.agent_id, "revision": item.agent.revision}
    team: JsonValue = None
    if item.team is not None:
        team = {"team_id": item.team.team_id, "revision": item.team.revision}
    return {
        "stage_id": item.stage_id,
        "title": item.title,
        "description": item.description,
        "depends_on": list(item.depends_on),
        "parameter_refs": list(item.parameter_refs),
        "capabilities": [
            {
                "capability_id": capability.capability_id,
                "optional": capability.optional,
                "version_constraint": capability.version_constraint,
            }
            for capability in item.capabilities
        ],
        "tool_ids": list(item.tool_ids),
        "agent": agent,
        "team": team,
        "model_routing_policy_ref": item.model_routing_policy_ref,
        "permission_actions": list(item.permission_actions),
        "metadata": _thaw(item.metadata),
    }


def _definition(value: object) -> WorkflowDefinition:
    item = _object(value, "workflow definition")
    return WorkflowDefinition(
        workflow_id=_required_string(item, "workflow_id"),
        owner_ref=_owner(_required(item, "owner_ref")),
        current_revision=_required_int(item, "current_revision"),
        project_id=_optional_string(item, "project_id"),
        organization_id=_optional_string(item, "organization_id"),
        created_at=_datetime(_required_string(item, "created_at")),
        updated_at=_datetime(_required_string(item, "updated_at")),
    )


def _revision(value: object) -> WorkflowRevision:
    item = _object(value, "workflow revision")
    return WorkflowRevision(
        workflow_id=_required_string(item, "workflow_id"),
        revision=_required_int(item, "revision"),
        owner_ref=_owner(_required(item, "owner_ref")),
        content=_content(_required(item, "content")),
        project_id=_optional_string(item, "project_id"),
        organization_id=_optional_string(item, "organization_id"),
        created_at=_datetime(_required_string(item, "created_at")),
    )


def _content(value: object) -> WorkflowContent:
    item = _object(value, "workflow content")
    provenance_data = _object(_required(item, "provenance"), "workflow provenance")
    compatibility_data = _object(
        _required(item, "compatibility"),
        "workflow compatibility",
    )
    contract_versions_data = _object(
        _required(compatibility_data, "contract_versions"),
        "workflow contract versions",
    )
    contract_versions: dict[str, str] = {}
    for key, raw_value in contract_versions_data.items():
        if not isinstance(raw_value, str):
            raise ValueError("workflow contract version values must be strings")
        contract_versions[key] = raw_value
    return WorkflowContent(
        name=_required_string(item, "name"),
        description=_required_string(item, "description"),
        stages=tuple(_stage(stage) for stage in _array(item, "stages")),
        parameters=tuple(_parameter(parameter) for parameter in _array(item, "parameters")),
        provenance=WorkflowProvenance(
            creator=_required_string(provenance_data, "creator"),
            source=_required_string(provenance_data, "source"),
            metadata=_frozen_mapping(_required(provenance_data, "metadata")),
        ),
        compatibility=WorkflowCompatibility(
            schema_version=_required_string(compatibility_data, "schema_version"),
            platform_version_range=_optional_string(
                compatibility_data,
                "platform_version_range",
            ),
            contract_versions=contract_versions,
            provider_agnostic=_required_bool(compatibility_data, "provider_agnostic"),
            orchestrator_agnostic=_required_bool(
                compatibility_data,
                "orchestrator_agnostic",
            ),
            metadata=_frozen_mapping(_required(compatibility_data, "metadata")),
        ),
        metadata=_frozen_mapping(_required(item, "metadata")),
    )


def _parameter(value: object) -> WorkflowParameter:
    item = _object(value, "workflow parameter")
    return WorkflowParameter(
        name=_required_string(item, "name"),
        required=_required_bool(item, "required"),
        secret_reference=_required_bool(item, "secret_reference"),
        description=_required_string(item, "description"),
    )


def _stage(value: object) -> WorkflowStage:
    item = _object(value, "workflow stage")
    agent_data = item.get("agent")
    agent = None
    if agent_data is not None:
        parsed = _object(agent_data, "workflow stage Agent reference")
        agent = AgentRevisionRef(
            agent_id=_required_string(parsed, "agent_id"),
            revision=_required_int(parsed, "revision"),
        )
    team_data = item.get("team")
    team = None
    if team_data is not None:
        parsed_team = _object(team_data, "workflow stage Agent Team reference")
        team = AgentTeamRevisionRef(
            team_id=_required_string(parsed_team, "team_id"),
            revision=_required_int(parsed_team, "revision"),
        )
    return WorkflowStage(
        stage_id=_required_string(item, "stage_id"),
        title=_required_string(item, "title"),
        description=_required_string(item, "description"),
        depends_on=_string_tuple(item, "depends_on"),
        parameter_refs=_string_tuple(item, "parameter_refs"),
        capabilities=tuple(_capability(capability) for capability in _array(item, "capabilities")),
        tool_ids=_string_tuple(item, "tool_ids"),
        agent=agent,
        team=team,
        model_routing_policy_ref=_optional_string(item, "model_routing_policy_ref"),
        permission_actions=_string_tuple(item, "permission_actions"),
        metadata=_frozen_mapping(_required(item, "metadata")),
    )


def _capability(value: object) -> WorkflowCapabilityRequirement:
    item = _object(value, "workflow capability requirement")
    return WorkflowCapabilityRequirement(
        capability_id=_required_string(item, "capability_id"),
        optional=_required_bool(item, "optional"),
        version_constraint=_optional_string(item, "version_constraint"),
    )


def _owner_to_json(item: OwnerRef) -> dict[str, JsonValue]:
    return {"type": item.type, "id": item.id}


def _owner(value: object) -> OwnerRef:
    item = _object(value, "workflow owner")
    return OwnerRef(type=_required_string(item, "type"), id=_required_string(item, "id"))


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _required(item: Mapping[str, object], key: str) -> object:
    if key not in item:
        raise ValueError(f"missing required workflow field: {key}")
    return item[key]


def _object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return cast(dict[str, object], value)


def _array(item: Mapping[str, object], key: str) -> list[object]:
    value = _required(item, key)
    if not isinstance(value, list):
        raise ValueError(f"workflow field {key} must be an array")
    return cast(list[object], value)


def _required_string(item: Mapping[str, object], key: str) -> str:
    value = _required(item, key)
    if not isinstance(value, str):
        raise ValueError(f"workflow field {key} must be a string")
    return value


def _optional_string(item: Mapping[str, object], key: str) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"workflow field {key} must be a string or null")
    return value


def _required_int(item: Mapping[str, object], key: str) -> int:
    value = _required(item, key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"workflow field {key} must be an integer")
    return value


def _required_bool(item: Mapping[str, object], key: str) -> bool:
    value = _required(item, key)
    if not isinstance(value, bool):
        raise ValueError(f"workflow field {key} must be a boolean")
    return value


def _string_tuple(item: Mapping[str, object], key: str) -> tuple[str, ...]:
    values = _array(item, key)
    if not all(isinstance(value, str) for value in values):
        raise ValueError(f"workflow field {key} must contain only strings")
    return tuple(cast(str, value) for value in values)


def _frozen_mapping(value: object) -> Mapping[str, FrozenJsonValue]:
    raw = _object(value, "workflow metadata")
    return {key: _frozen_value(item) for key, item in raw.items()}


def _frozen_value(value: object) -> FrozenJsonValue:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, list):
        return tuple(_frozen_value(item) for item in value)
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        mapping = cast(dict[str, object], value)
        return {key: _frozen_value(item) for key, item in mapping.items()}
    raise ValueError("workflow metadata contains unsupported JSON value")


def _thaw(value: FrozenJsonValue | Mapping[str, FrozenJsonValue]) -> JsonValue:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value
