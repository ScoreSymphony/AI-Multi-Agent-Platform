"""Context-aware Template application and explicit reapply semantics.

The low-level TemplateService owns definition/revision validation and compatibility
preview. This module owns integrated instantiation where resources created by dependency
Templates can be referenced by later handlers in the same application transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef, new_id

from .models import (
    TemplateDependency,
    TemplateInstantiation,
    TemplateInstantiationProvenance,
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateRevisionRef,
    TemplateRevisionState,
    TemplateType,
)
from .repository import TemplateRepository
from .service import (
    TemplateEnvironment,
    TemplateHandlerRegistry,
    TemplatePreview,
    TemplateResourceHandler,
    TemplateService,
)


@dataclass(frozen=True, slots=True)
class TemplateInstantiationContext:
    """Resources created earlier while applying one dependency graph.

    Handlers use this mapping instead of copying source deployment IDs into a newly
    instantiated composite. For example an Agent Team handler can resolve the Agent ID
    created by an Agent dependency Template in the same application.
    """

    instance_id: str
    environment: TemplateEnvironment
    created_resources: dict[TemplateRevisionRef, tuple[TemplateResourceRef, ...]]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "created_resources",
            MappingProxyType(dict(self.created_resources)),
        )

    def resources_for(
        self,
        template_id: str,
        *,
        revision: int | None = None,
        resource_type: str | None = None,
    ) -> tuple[TemplateResourceRef, ...]:
        matches = [
            (source, resources)
            for source, resources in self.created_resources.items()
            if source.template_id == template_id
            and (revision is None or source.revision == revision)
        ]
        if not matches:
            suffix = "latest-applied" if revision is None else str(revision)
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"Template dependency has not produced resources: {template_id}@{suffix}",
            )
        if revision is None and len(matches) > 1:
            raise ContractError(
                ErrorCode.CONFLICT,
                "multiple applied revisions match the Template dependency; pin a revision",
                details={"template_id": template_id},
            )
        resources = matches[0][1]
        if resource_type is not None:
            resources = tuple(item for item in resources if item.resource_type == resource_type)
        if not resources:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "Template dependency did not produce the requested resource type",
                details={
                    "template_id": template_id,
                    "resource_type": resource_type or "",
                },
            )
        return resources

    def single_resource_for(
        self,
        template_id: str,
        *,
        revision: int | None = None,
        resource_type: str | None = None,
    ) -> TemplateResourceRef:
        resources = self.resources_for(
            template_id,
            revision=revision,
            resource_type=resource_type,
        )
        if len(resources) != 1:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Template dependency produced more than one matching resource",
                details={
                    "template_id": template_id,
                    "resource_type": resource_type or "",
                    "count": len(resources),
                },
            )
        return resources[0]


class ContextualTemplateResourceHandler(Protocol):
    template_type: TemplateType

    def preview(self, revision: TemplateRevision) -> tuple[TemplateResourceChange, ...]: ...

    async def instantiate(
        self,
        revision: TemplateRevision,
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> tuple[TemplateResourceRef, ...]: ...


@runtime_checkable
class CompensatingTemplateResourceHandler(Protocol):
    """Optional guarded rollback seam for resources created by one Template handler."""

    async def compensate(
        self,
        resources: tuple[TemplateResourceRef, ...],
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> None: ...


class _PreviewHandlerAdapter:
    """Expose only preview through the existing low-level handler contract."""

    def __init__(self, handler: ContextualTemplateResourceHandler) -> None:
        self._handler = handler
        self.template_type = handler.template_type

    def preview(self, revision: TemplateRevision) -> tuple[TemplateResourceChange, ...]:
        return self._handler.preview(revision)

    def instantiate(
        self,
        revision: TemplateRevision,
        provenance: TemplateInstantiationProvenance,
    ) -> tuple[TemplateResourceRef, ...]:
        del revision, provenance
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "context-aware Template handlers must be applied through TemplateApplicationService",
        )


class ContextualTemplateHandlerRegistry:
    """Registry shared by compatibility preview and integrated application."""

    def __init__(self) -> None:
        self._handlers: dict[TemplateType, ContextualTemplateResourceHandler] = {}
        self._preview_registry = TemplateHandlerRegistry()

    @property
    def preview_registry(self) -> TemplateHandlerRegistry:
        return self._preview_registry

    def register(self, handler: ContextualTemplateResourceHandler) -> None:
        if handler.template_type in self._handlers:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"contextual Template handler already registered: {handler.template_type.value}",
            )
        self._handlers[handler.template_type] = handler
        adapter: TemplateResourceHandler = _PreviewHandlerAdapter(handler)
        self._preview_registry.register(adapter)

    def get(self, template_type: TemplateType) -> ContextualTemplateResourceHandler | None:
        return self._handlers.get(template_type)


class CompositeTemplateHandler:
    """Composite roots only coordinate dependencies and create no private runtime object."""

    template_type = TemplateType.COMPOSITE

    def preview(self, revision: TemplateRevision) -> tuple[TemplateResourceChange, ...]:
        del revision
        return ()

    async def instantiate(
        self,
        revision: TemplateRevision,
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> tuple[TemplateResourceRef, ...]:
        del revision, provenance, context
        return ()

    async def compensate(
        self,
        resources: tuple[TemplateResourceRef, ...],
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> None:
        del resources, provenance, context


@dataclass(frozen=True, slots=True)
class _AppliedResources:
    handler: ContextualTemplateResourceHandler
    resources: tuple[TemplateResourceRef, ...]
    provenance: TemplateInstantiationProvenance
    context: TemplateInstantiationContext


class TemplateApplicationService:
    """Safely apply a complete Template dependency graph to canonical resource handlers."""

    def __init__(
        self,
        repository: TemplateRepository,
        handlers: ContextualTemplateHandlerRegistry | None = None,
    ) -> None:
        self.repository = repository
        self.handlers = handlers or ContextualTemplateHandlerRegistry()
        if self.handlers.get(TemplateType.COMPOSITE) is None:
            self.handlers.register(CompositeTemplateHandler())
        self.templates = TemplateService(repository, self.handlers.preview_registry)

    def preview(
        self,
        template_id: str,
        *,
        applied_by: OwnerRef,
        environment: TemplateEnvironment,
        revision: int | None = None,
        allow_draft: bool = False,
    ) -> TemplatePreview:
        return self.templates.preview(
            template_id,
            applied_by=applied_by,
            environment=environment,
            revision=revision,
            allow_draft=allow_draft,
        )

    async def apply(
        self,
        template_id: str,
        *,
        applied_by: OwnerRef,
        environment: TemplateEnvironment,
        revision: int | None = None,
        allow_draft: bool = False,
    ) -> TemplateInstantiation:
        preview = self.preview(
            template_id,
            applied_by=applied_by,
            environment=environment,
            revision=revision,
            allow_draft=allow_draft,
        )
        self._require_applicable(preview)

        root = self._get_revision(
            template_id,
            revision,
            published_only=not allow_draft,
        )
        dependency_order, _ = self._resolve_dependency_order(root)
        instance_id = new_id("template_instance")
        created_by_source: dict[TemplateRevisionRef, tuple[TemplateResourceRef, ...]] = {}
        resource_refs: list[TemplateResourceRef] = []
        applied_resources: list[_AppliedResources] = []

        try:
            for item in dependency_order:
                handler = self.handlers.get(item.content.template_type)
                if handler is None:
                    handler_type = item.content.template_type.value
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        f"Template handler disappeared during apply: {handler_type}",
                    )
                context = TemplateInstantiationContext(
                    instance_id=instance_id,
                    environment=environment,
                    created_resources=created_by_source,
                )
                provenance = TemplateInstantiationProvenance(
                    source=item.ref,
                    applied_by=applied_by,
                )
                created = await handler.instantiate(item, provenance, context)
                created_by_source[item.ref] = created
                resource_refs.extend(created)
                if created:
                    applied_resources.append(
                        _AppliedResources(
                            handler=handler,
                            resources=created,
                            provenance=provenance,
                            context=context,
                        )
                    )

            instantiation = TemplateInstantiation(
                source=root.ref,
                applied_by=applied_by,
                resource_refs=tuple(resource_refs),
                instance_id=instance_id,
            )
            self.repository.record_instantiation(instantiation)
            return instantiation
        except Exception as apply_error:
            await self._compensate_failed_apply(applied_resources, apply_error)
            raise

    async def _compensate_failed_apply(
        self,
        applied_resources: list[_AppliedResources],
        apply_error: Exception,
    ) -> None:
        failures: list[JsonValue] = []
        uncompensated: list[JsonValue] = []
        for applied in reversed(applied_resources):
            handler = applied.handler
            if not isinstance(handler, CompensatingTemplateResourceHandler):
                uncompensated.extend(
                    {
                        "resource_type": resource.resource_type,
                        "resource_id": resource.resource_id,
                    }
                    for resource in applied.resources
                )
                continue
            try:
                await handler.compensate(
                    applied.resources,
                    applied.provenance,
                    applied.context,
                )
            except Exception as compensation_error:
                failures.append(
                    {
                        "template_type": handler.template_type.value,
                        "error_type": type(compensation_error).__name__,
                        "error": str(compensation_error),
                    }
                )

        if failures or uncompensated:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "Template apply failed and created resources could not be fully compensated",
                details={
                    "apply_error_type": type(apply_error).__name__,
                    "apply_error": str(apply_error),
                    "compensation_failures": failures,
                    "uncompensated_resources": uncompensated,
                },
            ) from apply_error

    async def reapply(
        self,
        instance_id: str,
        *,
        applied_by: OwnerRef,
        environment: TemplateEnvironment,
        revision: int | None = None,
    ) -> TemplateInstantiation:
        """Create a new instance; never mutate resources from the previous instance."""

        previous = self.repository.get_instantiation(instance_id)
        return await self.apply(
            previous.source.template_id,
            applied_by=applied_by,
            environment=environment,
            revision=revision,
        )

    @staticmethod
    def _require_applicable(preview: TemplatePreview) -> None:
        if preview.ungrantable_permissions:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "Template requests permissions the applying actor cannot grant",
                details={"permissions": list(preview.ungrantable_permissions)},
            )
        if preview.applicable:
            return
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "Template is not compatible with the target environment",
            details={
                "missing_capabilities": list(preview.missing_required_capability_ids),
                "missing_plugins": list(preview.missing_plugin_ids),
                "missing_connectors": list(preview.missing_connector_ids),
                "missing_model_policies": list(preview.missing_model_policy_refs),
                "missing_workspace_prerequisites": list(preview.missing_workspace_prerequisites),
                "unresolved_placeholders": list(preview.unresolved_placeholders),
                "unresolved_secret_reference_placeholders": list(
                    preview.unresolved_secret_reference_placeholders
                ),
                "unvalidated_configuration_refs": list(preview.unvalidated_configuration_refs),
                "missing_handler_types": list(preview.missing_handler_types),
            },
        )

    def _get_revision(
        self,
        template_id: str,
        revision: int | None,
        *,
        published_only: bool,
    ) -> TemplateRevision:
        definition = self.repository.get_template(template_id)
        selected_revision = revision
        if selected_revision is None:
            selected_revision = (
                definition.latest_published_revision
                if published_only
                else definition.current_revision
            )
        if selected_revision is None:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"Template has no published revision: {template_id}",
            )
        item = self.repository.get_revision(template_id, selected_revision)
        if published_only and item.state is not TemplateRevisionState.PUBLISHED:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"Template revision is not published: {template_id}@{selected_revision}",
            )
        return item

    def _resolve_dependency_order(
        self,
        root: TemplateRevision,
    ) -> tuple[tuple[TemplateRevision, ...], tuple[str, ...]]:
        ordered: list[TemplateRevision] = []
        visiting: set[tuple[str, int]] = set()
        visited: set[tuple[str, int]] = set()
        missing_optional: set[str] = set()

        def visit(item: TemplateRevision) -> None:
            key = (item.template_id, item.revision)
            if key in visited:
                return
            if key in visiting:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "Template dependency cycle detected",
                    details={"template_id": item.template_id, "revision": item.revision},
                )
            visiting.add(key)
            for dependency in item.content.dependencies:
                target = self._resolve_dependency(dependency, missing_optional)
                if target is not None:
                    visit(target)
            visiting.remove(key)
            visited.add(key)
            ordered.append(item)

        visit(root)
        return tuple(ordered), tuple(sorted(missing_optional))

    def _resolve_dependency(
        self,
        dependency: TemplateDependency,
        missing_optional: set[str],
    ) -> TemplateRevision | None:
        try:
            return self._get_revision(
                dependency.template_id,
                dependency.revision,
                published_only=True,
            )
        except ContractError as exc:
            if dependency.optional and exc.code in {ErrorCode.NOT_FOUND, ErrorCode.CONFLICT}:
                suffix = "latest" if dependency.revision is None else str(dependency.revision)
                missing_optional.add(f"{dependency.template_id}@{suffix}")
                return None
            raise
