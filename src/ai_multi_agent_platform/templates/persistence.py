"""Durable JSON persistence for canonical reusable Templates."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from ai_multi_agent_platform.contracts.types import FrozenJsonValue, JsonValue
from ai_multi_agent_platform.domain import OwnerRef

from .models import (
    CapabilityRequirement,
    TemplateCompatibility,
    TemplateConfiguration,
    TemplateContent,
    TemplateDefinition,
    TemplateDependency,
    TemplateInstantiation,
    TemplateProvenance,
    TemplateRequirements,
    TemplateResourceRef,
    TemplateRevision,
    TemplateRevisionRef,
    TemplateRevisionState,
    TemplateTrust,
    TemplateType,
)
from .repository import InMemoryTemplateRepository

TEMPLATE_REPOSITORY_SCHEMA_VERSION = "1"


class JsonTemplateRepository(InMemoryTemplateRepository):
    """Persist immutable Template histories and application records atomically."""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        if self.path.exists():
            self._restore()

    def create_template(self, definition: TemplateDefinition, revision: TemplateRevision) -> None:
        super().create_template(definition, revision)
        self._save()

    def append_revision(self, definition: TemplateDefinition, revision: TemplateRevision) -> None:
        super().append_revision(definition, revision)
        self._save()

    def record_instantiation(self, instantiation: TemplateInstantiation) -> None:
        super().record_instantiation(instantiation)
        self._save()

    def _save(self) -> None:
        document: dict[str, JsonValue] = {
            "schema_version": TEMPLATE_REPOSITORY_SCHEMA_VERSION,
            "templates": [_definition_to_json(item) for item in self.list_templates()],
            "revisions": [
                _revision_to_json(revision)
                for template in self.list_templates()
                for revision in self.list_revisions(template.template_id)
            ],
            "instantiations": [_instantiation_to_json(item) for item in self.list_instantiations()],
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
        document = _object(raw, "Template repository document")
        version = _required_string(document, "schema_version")
        if version != TEMPLATE_REPOSITORY_SCHEMA_VERSION:
            raise ValueError(
                "unsupported Template repository schema version: "
                f"{version!r}; expected {TEMPLATE_REPOSITORY_SCHEMA_VERSION!r}"
            )

        definitions = tuple(_definition(item) for item in _required_array(document, "templates"))
        revisions = tuple(_revision(item) for item in _required_array(document, "revisions"))
        instantiations = tuple(
            _instantiation(item) for item in _required_array(document, "instantiations")
        )

        histories: dict[str, list[TemplateRevision]] = {}
        for revision in revisions:
            histories.setdefault(revision.template_id, []).append(revision)

        for definition in definitions:
            history = sorted(
                histories.pop(definition.template_id, []),
                key=lambda item: item.revision,
            )
            self._restore_template(definition, history)
        if histories:
            raise ValueError("Template repository contains revisions without definitions")

        for instantiation in instantiations:
            InMemoryTemplateRepository.record_instantiation(self, instantiation)

    def _restore_template(
        self,
        definition: TemplateDefinition,
        history: list[TemplateRevision],
    ) -> None:
        if not history or history[-1].revision != definition.current_revision:
            raise ValueError("Template definition does not match persisted revision history")
        expected = list(range(1, definition.current_revision + 1))
        if [item.revision for item in history] != expected:
            raise ValueError("Template revision history is not contiguous")
        if any(item.template_id != definition.template_id for item in history):
            raise ValueError("Template revision history contains mismatched Template IDs")

        latest_published: int | None = None
        for index, revision in enumerate(history):
            if revision.state is TemplateRevisionState.PUBLISHED:
                latest_published = revision.revision
            interim = replace(
                definition,
                current_revision=revision.revision,
                latest_published_revision=latest_published,
            )
            if index == 0:
                InMemoryTemplateRepository.create_template(self, interim, revision)
            else:
                InMemoryTemplateRepository.append_revision(self, interim, revision)

        restored = self.get_template(definition.template_id)
        if restored != definition:
            raise ValueError("Template definition metadata does not match revision history")


def _definition_to_json(item: TemplateDefinition) -> dict[str, JsonValue]:
    return {
        "template_id": item.template_id,
        "owner_ref": _owner_to_json(item.owner_ref),
        "current_revision": item.current_revision,
        "latest_published_revision": item.latest_published_revision,
        "project_id": item.project_id,
        "organization_id": item.organization_id,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def _revision_to_json(item: TemplateRevision) -> dict[str, JsonValue]:
    return {
        "template_id": item.template_id,
        "revision": item.revision,
        "state": item.state.value,
        "owner_ref": _owner_to_json(item.owner_ref),
        "content": _content_to_json(item.content),
        "project_id": item.project_id,
        "organization_id": item.organization_id,
        "created_at": item.created_at.isoformat(),
    }


def _content_to_json(item: TemplateContent) -> dict[str, JsonValue]:
    return {
        "name": item.name,
        "description": item.description,
        "template_type": item.template_type.value,
        "configuration": _configuration_to_json(item.configuration),
        "dependencies": [
            {
                "template_id": dependency.template_id,
                "revision": dependency.revision,
                "optional": dependency.optional,
            }
            for dependency in item.dependencies
        ],
        "requirements": _requirements_to_json(item.requirements),
        "compatibility": _compatibility_to_json(item.compatibility),
        "provenance": _provenance_to_json(item.provenance),
        "tags": list(item.tags),
        "categories": list(item.categories),
    }


def _configuration_to_json(item: TemplateConfiguration) -> dict[str, JsonValue]:
    payload: JsonValue | None = None
    if item.payload is not None:
        payload = _thaw(item.payload)
    return {"payload": payload, "reference": item.reference}


def _requirements_to_json(item: TemplateRequirements) -> dict[str, JsonValue]:
    return {
        "capabilities": [
            {
                "capability_id": capability.capability_id,
                "optional": capability.optional,
                "version_constraint": capability.version_constraint,
                "privileged": capability.privileged,
            }
            for capability in item.capabilities
        ],
        "plugin_ids": list(item.plugin_ids),
        "connector_ids": list(item.connector_ids),
        "model_policy_refs": list(item.model_policy_refs),
        "permission_actions": list(item.permission_actions),
        "workspace_prerequisites": list(item.workspace_prerequisites),
        "placeholders": list(item.placeholders),
        "secret_reference_placeholders": list(item.secret_reference_placeholders),
    }


def _compatibility_to_json(item: TemplateCompatibility) -> dict[str, JsonValue]:
    return {
        "platform_version_range": item.platform_version_range,
        "contract_versions": dict(item.contract_versions),
        "orchestrator_agnostic": item.orchestrator_agnostic,
        "provider_agnostic": item.provider_agnostic,
        "metadata": _thaw(item.metadata),
    }


def _provenance_to_json(item: TemplateProvenance) -> dict[str, JsonValue]:
    source_template: JsonValue = None
    if item.source_template is not None:
        source_template = {
            "template_id": item.source_template.template_id,
            "revision": item.source_template.revision,
        }
    return {
        "author": item.author,
        "source": item.source,
        "trust": item.trust.value,
        "source_template": source_template,
        "metadata": _thaw(item.metadata),
    }


def _instantiation_to_json(item: TemplateInstantiation) -> dict[str, JsonValue]:
    return {
        "source": {
            "template_id": item.source.template_id,
            "revision": item.source.revision,
        },
        "applied_by": _owner_to_json(item.applied_by),
        "resource_refs": [
            {
                "resource_type": resource.resource_type,
                "resource_id": resource.resource_id,
            }
            for resource in item.resource_refs
        ],
        "instance_id": item.instance_id,
        "created_at": item.created_at.isoformat(),
    }


def _owner_to_json(item: OwnerRef) -> dict[str, JsonValue]:
    return {"type": item.type, "id": item.id}


def _definition(value: object) -> TemplateDefinition:
    item = _object(value, "Template definition")
    return TemplateDefinition(
        template_id=_required_string(item, "template_id"),
        owner_ref=_owner(_required(item, "owner_ref")),
        current_revision=_required_int(item, "current_revision"),
        latest_published_revision=_optional_int(item, "latest_published_revision"),
        project_id=_optional_string(item, "project_id"),
        organization_id=_optional_string(item, "organization_id"),
        created_at=_datetime(_required_string(item, "created_at")),
        updated_at=_datetime(_required_string(item, "updated_at")),
    )


def _revision(value: object) -> TemplateRevision:
    item = _object(value, "Template revision")
    return TemplateRevision(
        template_id=_required_string(item, "template_id"),
        revision=_required_int(item, "revision"),
        state=TemplateRevisionState(_required_string(item, "state")),
        owner_ref=_owner(_required(item, "owner_ref")),
        content=_content(_required(item, "content")),
        project_id=_optional_string(item, "project_id"),
        organization_id=_optional_string(item, "organization_id"),
        created_at=_datetime(_required_string(item, "created_at")),
    )


def _content(value: object) -> TemplateContent:
    item = _object(value, "Template content")
    return TemplateContent(
        name=_required_string(item, "name"),
        description=_required_string(item, "description"),
        template_type=TemplateType(_required_string(item, "template_type")),
        configuration=_configuration(_required(item, "configuration")),
        dependencies=tuple(_dependency(value) for value in _required_array(item, "dependencies")),
        requirements=_requirements(_required(item, "requirements")),
        compatibility=_compatibility(_required(item, "compatibility")),
        provenance=_provenance(_required(item, "provenance")),
        tags=_string_tuple(item, "tags"),
        categories=_string_tuple(item, "categories"),
    )


def _configuration(value: object) -> TemplateConfiguration:
    item = _object(value, "Template configuration")
    payload = item.get("payload")
    reference = _optional_string(item, "reference")
    if payload is not None:
        return TemplateConfiguration(
            payload=_frozen_object(payload, "Template configuration payload")
        )
    return TemplateConfiguration(reference=reference)


def _dependency(value: object) -> TemplateDependency:
    item = _object(value, "Template dependency")
    return TemplateDependency(
        template_id=_required_string(item, "template_id"),
        revision=_optional_int(item, "revision"),
        optional=_required_bool(item, "optional"),
    )


def _requirements(value: object) -> TemplateRequirements:
    item = _object(value, "Template requirements")
    capabilities = tuple(_capability(value) for value in _required_array(item, "capabilities"))
    return TemplateRequirements(
        capabilities=capabilities,
        plugin_ids=_string_tuple(item, "plugin_ids"),
        connector_ids=_string_tuple(item, "connector_ids"),
        model_policy_refs=_string_tuple(item, "model_policy_refs"),
        permission_actions=_string_tuple(item, "permission_actions"),
        workspace_prerequisites=_string_tuple(item, "workspace_prerequisites"),
        placeholders=_string_tuple(item, "placeholders"),
        secret_reference_placeholders=_string_tuple(
            item,
            "secret_reference_placeholders",
        ),
    )


def _capability(value: object) -> CapabilityRequirement:
    item = _object(value, "Capability requirement")
    return CapabilityRequirement(
        capability_id=_required_string(item, "capability_id"),
        optional=_required_bool(item, "optional"),
        version_constraint=_optional_string(item, "version_constraint"),
        privileged=_required_bool(item, "privileged"),
    )


def _compatibility(value: object) -> TemplateCompatibility:
    item = _object(value, "Template compatibility")
    versions_raw = _object(_required(item, "contract_versions"), "contract versions")
    versions = {
        key: _string(value, f"contract version {key}") for key, value in versions_raw.items()
    }
    metadata_raw = _required(item, "metadata")
    metadata = _frozen_object(metadata_raw, "compatibility metadata")
    return TemplateCompatibility(
        platform_version_range=_optional_string(item, "platform_version_range"),
        contract_versions=versions,
        orchestrator_agnostic=_required_bool(item, "orchestrator_agnostic"),
        provider_agnostic=_required_bool(item, "provider_agnostic"),
        metadata=metadata,
    )


def _provenance(value: object) -> TemplateProvenance:
    item = _object(value, "Template provenance")
    source_value = item.get("source_template")
    source_template = None
    if source_value is not None:
        source_item = _object(source_value, "source Template")
        source_template = TemplateRevisionRef(
            template_id=_required_string(source_item, "template_id"),
            revision=_required_int(source_item, "revision"),
        )
    return TemplateProvenance(
        author=_required_string(item, "author"),
        source=_required_string(item, "source"),
        trust=TemplateTrust(_required_string(item, "trust")),
        source_template=source_template,
        metadata=_frozen_object(_required(item, "metadata"), "provenance metadata"),
    )


def _instantiation(value: object) -> TemplateInstantiation:
    item = _object(value, "Template instantiation")
    source_item = _object(_required(item, "source"), "Template source")
    return TemplateInstantiation(
        source=TemplateRevisionRef(
            template_id=_required_string(source_item, "template_id"),
            revision=_required_int(source_item, "revision"),
        ),
        applied_by=_owner(_required(item, "applied_by")),
        resource_refs=tuple(
            _resource_ref(value) for value in _required_array(item, "resource_refs")
        ),
        instance_id=_required_string(item, "instance_id"),
        created_at=_datetime(_required_string(item, "created_at")),
    )


def _resource_ref(value: object) -> TemplateResourceRef:
    item = _object(value, "Template resource reference")
    return TemplateResourceRef(
        resource_type=_required_string(item, "resource_type"),
        resource_id=_required_string(item, "resource_id"),
    )


def _owner(value: object) -> OwnerRef:
    item = _object(value, "owner reference")
    owner_type = _required_string(item, "type")
    allowed = {"user", "organization", "team", "service"}
    if owner_type not in allowed:
        raise ValueError(f"unsupported owner type: {owner_type!r}")
    typed = cast(Literal["user", "organization", "team", "service"], owner_type)
    return OwnerRef(type=typed, id=_required_string(item, "id"))


def _thaw(value: FrozenJsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _frozen(value: object) -> FrozenJsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list):
        return tuple(_frozen(item) for item in value)
    if isinstance(value, dict):
        return {str(key): _frozen(item) for key, item in value.items()}
    raise ValueError(f"unsupported JSON value in Template repository: {type(value).__name__}")


def _frozen_object(value: object, label: str) -> Mapping[str, FrozenJsonValue]:
    frozen = _frozen(value)
    if not isinstance(frozen, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return frozen


def _object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return cast(Mapping[str, object], value)


def _required(item: Mapping[str, object], key: str) -> object:
    if key not in item:
        raise ValueError(f"missing required Template repository field: {key}")
    return item[key]


def _required_array(item: Mapping[str, object], key: str) -> list[object]:
    value = _required(item, key)
    if not isinstance(value, list):
        raise ValueError(f"Template repository field {key!r} must be an array")
    return cast(list[object], value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    return value


def _required_string(item: Mapping[str, object], key: str) -> str:
    return _string(_required(item, key), f"Template repository field {key!r}")


def _optional_string(item: Mapping[str, object], key: str) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    return _string(value, f"Template repository field {key!r}")


def _required_int(item: Mapping[str, object], key: str) -> int:
    value = _required(item, key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Template repository field {key!r} must be an integer")
    return value


def _optional_int(item: Mapping[str, object], key: str) -> int | None:
    value = item.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Template repository field {key!r} must be an integer")
    return value


def _required_bool(item: Mapping[str, object], key: str) -> bool:
    value = _required(item, key)
    if not isinstance(value, bool):
        raise ValueError(f"Template repository field {key!r} must be a boolean")
    return value


def _string_tuple(item: Mapping[str, object], key: str) -> tuple[str, ...]:
    return tuple(
        _string(value, f"Template repository field {key!r} item")
        for value in _required_array(item, key)
    )


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("persisted Template timestamps must be timezone-aware")
    return parsed
