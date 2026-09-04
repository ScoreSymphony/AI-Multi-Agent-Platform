"""Persistence boundary and deterministic reference repository for Templates."""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .models import TemplateDefinition, TemplateInstantiation, TemplateRevision


class TemplateRepository(Protocol):
    def create_template(
        self, definition: TemplateDefinition, revision: TemplateRevision
    ) -> None: ...

    def append_revision(
        self, definition: TemplateDefinition, revision: TemplateRevision
    ) -> None: ...

    def get_template(self, template_id: str) -> TemplateDefinition: ...

    def list_templates(self) -> tuple[TemplateDefinition, ...]: ...

    def get_revision(self, template_id: str, revision: int) -> TemplateRevision: ...

    def list_revisions(self, template_id: str) -> tuple[TemplateRevision, ...]: ...

    def record_instantiation(self, instantiation: TemplateInstantiation) -> None: ...

    def get_instantiation(self, instance_id: str) -> TemplateInstantiation: ...

    def list_instantiations(
        self,
        template_id: str | None = None,
    ) -> tuple[TemplateInstantiation, ...]: ...


class InMemoryTemplateRepository:
    """Reference repository preserving every immutable Template revision and instance."""

    def __init__(self) -> None:
        self._templates: dict[str, TemplateDefinition] = {}
        self._revisions: dict[tuple[str, int], TemplateRevision] = {}
        self._instantiations: dict[str, TemplateInstantiation] = {}

    def create_template(self, definition: TemplateDefinition, revision: TemplateRevision) -> None:
        if definition.template_id in self._templates:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"template already exists: {definition.template_id}",
            )
        if definition.current_revision != 1 or revision.revision != 1:
            raise ContractError(ErrorCode.CONFLICT, "new template must start at revision 1")
        self._validate_pair(definition, revision)
        self._templates[definition.template_id] = definition
        self._revisions[(revision.template_id, revision.revision)] = revision

    def append_revision(self, definition: TemplateDefinition, revision: TemplateRevision) -> None:
        current = self.get_template(definition.template_id)
        expected_revision = current.current_revision + 1
        if (
            definition.current_revision != expected_revision
            or revision.revision != expected_revision
        ):
            raise ContractError(
                ErrorCode.CONFLICT,
                "template revision must increase exactly by one",
                details={
                    "current_revision": current.current_revision,
                    "new_revision": revision.revision,
                },
            )
        self._validate_pair(definition, revision)
        key = (revision.template_id, revision.revision)
        if key in self._revisions:
            raise ContractError(ErrorCode.CONFLICT, "template revision already exists")
        self._revisions[key] = revision
        self._templates[definition.template_id] = definition

    def get_template(self, template_id: str) -> TemplateDefinition:
        try:
            return self._templates[template_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"template not found: {template_id}") from exc

    def list_templates(self) -> tuple[TemplateDefinition, ...]:
        return tuple(self._templates[key] for key in sorted(self._templates))

    def get_revision(self, template_id: str, revision: int) -> TemplateRevision:
        try:
            return self._revisions[(template_id, revision)]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"template revision not found: {template_id}@{revision}",
            ) from exc

    def list_revisions(self, template_id: str) -> tuple[TemplateRevision, ...]:
        self.get_template(template_id)
        revisions = [
            item for (current_id, _), item in self._revisions.items() if current_id == template_id
        ]
        return tuple(sorted(revisions, key=lambda item: item.revision))

    def record_instantiation(self, instantiation: TemplateInstantiation) -> None:
        if instantiation.instance_id in self._instantiations:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"template instantiation already exists: {instantiation.instance_id}",
            )
        self.get_revision(instantiation.source.template_id, instantiation.source.revision)
        self._instantiations[instantiation.instance_id] = instantiation

    def get_instantiation(self, instance_id: str) -> TemplateInstantiation:
        try:
            return self._instantiations[instance_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"template instantiation not found: {instance_id}",
            ) from exc

    def list_instantiations(
        self,
        template_id: str | None = None,
    ) -> tuple[TemplateInstantiation, ...]:
        values = self._instantiations.values()
        if template_id is not None:
            self.get_template(template_id)
            values = (
                item for item in values if item.source.template_id == template_id
            )
        return tuple(sorted(values, key=lambda item: (item.created_at, item.instance_id)))

    @staticmethod
    def _validate_pair(definition: TemplateDefinition, revision: TemplateRevision) -> None:
        if definition.template_id != revision.template_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "template definition/revision ID mismatch",
            )
        if definition.current_revision != revision.revision:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "template definition does not point at supplied revision",
            )
        if (
            definition.owner_ref != revision.owner_ref
            or definition.project_id != revision.project_id
            or definition.organization_id != revision.organization_id
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "template definition ownership scope must match latest revision snapshot",
            )
        if definition.latest_published_revision is not None:
            if definition.latest_published_revision > revision.revision:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "latest published template revision cannot exceed current revision",
                )
