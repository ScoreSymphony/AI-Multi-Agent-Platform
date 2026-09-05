"""Concrete reusable Template integration for canonical Projects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.service import ScopeStore
from ai_multi_agent_platform.domain import OwnerRef

from .application import ContextualTemplateHandlerRegistry, TemplateInstantiationContext
from .models import (
    TemplateConfiguration,
    TemplateContent,
    TemplateInstantiationProvenance,
    TemplateProvenance,
    TemplateRequirements,
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateTrust,
    TemplateType,
)
from .service import TemplateService


@dataclass(slots=True)
class ProjectTemplateHandler:
    """Instantiate an ordinary canonical Project through the platform ScopeStore."""

    scopes: ScopeStore
    template_type = TemplateType.PROJECT

    def preview(self, revision: TemplateRevision) -> tuple[TemplateResourceChange, ...]:
        payload = _payload(revision)
        name = _required_string(payload, "name")
        return (
            TemplateResourceChange(
                resource_type="project",
                action="create",
                description=(
                    f"Create Project {name!r} from {revision.template_id}@{revision.revision}"
                ),
            ),
        )

    async def instantiate(
        self,
        revision: TemplateRevision,
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> tuple[TemplateResourceRef, ...]:
        payload = _payload(revision)
        owner = provenance.applied_by
        project = self.scopes.create_project(
            key=f"template:{context.instance_id}:{revision.template_id}:{revision.revision}:project",
            name=_required_string(payload, "name"),
            owner_type=owner.type,
            owner_id=owner.id,
        )
        return (TemplateResourceRef(resource_type="project", resource_id=project.id),)


@dataclass(slots=True)
class ProjectTemplateExporter:
    """Create reusable Project configuration without carrying source identity or ID."""

    scopes: ScopeStore
    templates: TemplateService

    def create_from_project(
        self,
        project_id: str,
        *,
        owner_ref: OwnerRef,
        author: str,
        name: str | None = None,
    ) -> TemplateRevision:
        source = self.scopes.get_project(project_id)
        content = TemplateContent(
            name=name or source.name,
            description=f"Template exported from Project {source.id}",
            template_type=TemplateType.PROJECT,
            configuration=TemplateConfiguration(payload={"name": source.name}),
            requirements=TemplateRequirements(),
            provenance=TemplateProvenance(
                author=author,
                source="canonical-project-export",
                trust=TemplateTrust.LOCAL,
                metadata={
                    "source_resource_type": "project",
                    "source_resource_id": source.id,
                },
            ),
            tags=("project", "exported"),
        )
        return self.templates.create_draft(
            owner_ref=owner_ref,
            content=content,
        )


def register_project_template_handler(
    registry: ContextualTemplateHandlerRegistry,
    scopes: ScopeStore,
) -> None:
    registry.register(ProjectTemplateHandler(scopes))


def _payload(revision: TemplateRevision) -> Mapping[str, object]:
    payload = revision.content.configuration.payload
    if payload is None:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "canonical Project handler requires an inline Template payload",
        )
    return cast(Mapping[str, object], payload)


def _required_string(payload: Mapping[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"Project Template {name} must be a non-blank string",
        )
    return value
