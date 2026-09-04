"""Template creation, versioning, compatibility preview and instantiation engine."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import FrozenJsonValue
from ai_multi_agent_platform.domain import OwnerRef, new_id

from .models import (
    TemplateConfiguration,
    TemplateContent,
    TemplateDefinition,
    TemplateDependency,
    TemplateInstantiation,
    TemplateInstantiationProvenance,
    TemplateProvenance,
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateRevisionRef,
    TemplateRevisionState,
    TemplateType,
    utc_now,
)
from .repository import TemplateRepository


_FORBIDDEN_SECRET_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "secrets",
        "credential",
        "credentials",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "auth_token",
        "private_key",
        "session_token",
        "cookie",
    }
)
_FORBIDDEN_RUNTIME_KEYS = frozenset(
    {
        "runtime_state",
        "provider_session_id",
        "orchestrator_session_id",
        "backend_session_id",
        "active_run_id",
        "agent_run_id",
        "worker_job_id",
    }
)


def _normalize_key(key: str) -> str:
    return key.strip().lower().replace("-", "_").replace(" ", "_")


def _scan_safe_value(value: FrozenJsonValue, path: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = _normalize_key(key)
            current_path = f"{path}.{key}" if path else key
            if normalized in _FORBIDDEN_SECRET_KEYS:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "template configuration contains a plaintext-secret field",
                    details={"path": current_path},
                )
            if normalized in _FORBIDDEN_RUNTIME_KEYS:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "template configuration contains backend-private runtime state",
                    details={"path": current_path},
                )
            _scan_safe_value(item, current_path)
    elif isinstance(value, tuple):
        for index, item in enumerate(value):
            _scan_safe_value(item, f"{path}[{index}]")


def validate_template_configuration(configuration: TemplateConfiguration) -> None:
    """Reject known plaintext-secret and runtime-private fields before storage."""

    if configuration.payload is not None:
        _scan_safe_value(configuration.payload, "configuration")


@dataclass(frozen=True, slots=True)
class TemplateEnvironment:
    capability_ids: frozenset[str] = frozenset()
    plugin_ids: frozenset[str] = frozenset()
    connector_ids: frozenset[str] = frozenset()
    model_policy_refs: frozenset[str] = frozenset()
    grantable_permissions: frozenset[str] = frozenset()
    workspace_prerequisites: frozenset[str] = frozenset()
    resolved_placeholders: frozenset[str] = frozenset()
    resolved_secret_reference_placeholders: frozenset[str] = frozenset()
    validated_configuration_refs: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class TemplatePreview:
    source: TemplateRevisionRef
    dependency_order: tuple[TemplateRevisionRef, ...]
    missing_required_capability_ids: tuple[str, ...] = ()
    missing_optional_capability_ids: tuple[str, ...] = ()
    missing_plugin_ids: tuple[str, ...] = ()
    missing_connector_ids: tuple[str, ...] = ()
    missing_model_policy_refs: tuple[str, ...] = ()
    ungrantable_permissions: tuple[str, ...] = ()
    missing_workspace_prerequisites: tuple[str, ...] = ()
    unresolved_placeholders: tuple[str, ...] = ()
    unresolved_secret_reference_placeholders: tuple[str, ...] = ()
    unvalidated_configuration_refs: tuple[str, ...] = ()
    missing_optional_dependencies: tuple[str, ...] = ()
    missing_handler_types: tuple[str, ...] = ()
    privileged_capability_ids: tuple[str, ...] = ()
    resource_changes: tuple[TemplateResourceChange, ...] = ()

    @property
    def applicable(self) -> bool:
        return not any(
            (
                self.missing_required_capability_ids,
                self.missing_plugin_ids,
                self.missing_connector_ids,
                self.missing_model_policy_refs,
                self.ungrantable_permissions,
                self.missing_workspace_prerequisites,
                self.unresolved_placeholders,
                self.unresolved_secret_reference_placeholders,
                self.unvalidated_configuration_refs,
                self.missing_handler_types,
            )
        )


class TemplateResourceHandler(Protocol):
    """Creates ordinary canonical resources for one Template type."""

    template_type: TemplateType

    def preview(self, revision: TemplateRevision) -> tuple[TemplateResourceChange, ...]: ...

    def instantiate(
        self,
        revision: TemplateRevision,
        provenance: TemplateInstantiationProvenance,
    ) -> tuple[TemplateResourceRef, ...]: ...


class TemplateHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[TemplateType, TemplateResourceHandler] = {}

    def register(self, handler: TemplateResourceHandler) -> None:
        if handler.template_type in self._handlers:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"template handler already registered: {handler.template_type.value}",
            )
        self._handlers[handler.template_type] = handler

    def get(self, template_type: TemplateType) -> TemplateResourceHandler | None:
        return self._handlers.get(template_type)


class TemplateService:
    def __init__(
        self,
        repository: TemplateRepository,
        handlers: TemplateHandlerRegistry | None = None,
    ) -> None:
        self.repository = repository
        self.handlers = handlers or TemplateHandlerRegistry()

    def create_draft(
        self,
        *,
        owner_ref: OwnerRef,
        content: TemplateContent,
        project_id: str | None = None,
        organization_id: str | None = None,
        template_id: str | None = None,
    ) -> TemplateRevision:
        validate_template_configuration(content.configuration)
        stable_id = template_id or new_id("template")
        now = utc_now()
        definition = TemplateDefinition(
            template_id=stable_id,
            owner_ref=owner_ref,
            current_revision=1,
            project_id=project_id,
            organization_id=organization_id,
            created_at=now,
            updated_at=now,
        )
        revision = TemplateRevision(
            template_id=stable_id,
            revision=1,
            state=TemplateRevisionState.DRAFT,
            owner_ref=owner_ref,
            content=content,
            project_id=project_id,
            organization_id=organization_id,
            created_at=now,
        )
        self.repository.create_template(definition, revision)
        return revision

    def revise_draft(
        self,
        template_id: str,
        content: TemplateContent,
        *,
        expected_revision: int,
    ) -> TemplateRevision:
        validate_template_configuration(content.configuration)
        definition = self.repository.get_template(template_id)
        if definition.current_revision != expected_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "template changed since the requested revision",
                details={
                    "expected_revision": expected_revision,
                    "current_revision": definition.current_revision,
                },
            )
        next_revision = definition.current_revision + 1
        now = utc_now()
        revision = TemplateRevision(
            template_id=template_id,
            revision=next_revision,
            state=TemplateRevisionState.DRAFT,
            owner_ref=definition.owner_ref,
            content=content,
            project_id=definition.project_id,
            organization_id=definition.organization_id,
            created_at=now,
        )
        updated = replace(
            definition,
            current_revision=next_revision,
            updated_at=now,
        )
        self.repository.append_revision(updated, revision)
        return revision

    def publish(self, template_id: str, *, expected_revision: int) -> TemplateRevision:
        definition = self.repository.get_template(template_id)
        if definition.current_revision != expected_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "template changed since the requested revision",
                details={
                    "expected_revision": expected_revision,
                    "current_revision": definition.current_revision,
                },
            )
        current = self.repository.get_revision(template_id, definition.current_revision)
        if current.state is TemplateRevisionState.PUBLISHED:
            raise ContractError(
                ErrorCode.CONFLICT, "current template revision is already published"
            )
        validate_template_configuration(current.content.configuration)
        next_revision = definition.current_revision + 1
        now = utc_now()
        published = replace(
            current,
            revision=next_revision,
            state=TemplateRevisionState.PUBLISHED,
            created_at=now,
        )
        updated = replace(
            definition,
            current_revision=next_revision,
            latest_published_revision=next_revision,
            updated_at=now,
        )
        self.repository.append_revision(updated, published)
        return published

    def clone_template(
        self,
        template_id: str,
        *,
        owner_ref: OwnerRef,
        author: str,
        revision: int | None = None,
        name: str | None = None,
    ) -> TemplateRevision:
        source = self._get_revision(template_id, revision, published_only=False)
        provenance = replace(
            source.content.provenance,
            author=author,
            source=f"clone:{source.template_id}@{source.revision}",
            source_template=source.ref,
        )
        content = replace(
            source.content,
            name=name or source.content.name,
            provenance=provenance,
        )
        return self.create_draft(owner_ref=owner_ref, content=content)

    def fork_template(
        self,
        template_id: str,
        *,
        owner_ref: OwnerRef,
        author: str,
        revision: int | None = None,
        name: str | None = None,
    ) -> TemplateRevision:
        source = self._get_revision(template_id, revision, published_only=False)
        provenance = TemplateProvenance(
            author=author,
            source=f"fork:{source.template_id}@{source.revision}",
            trust=source.content.provenance.trust,
            source_template=source.ref,
            metadata=source.content.provenance.metadata,
        )
        content = replace(
            source.content,
            name=name or source.content.name,
            provenance=provenance,
        )
        return self.create_draft(owner_ref=owner_ref, content=content)

    def preview(
        self,
        template_id: str,
        *,
        applied_by: OwnerRef,
        environment: TemplateEnvironment,
        revision: int | None = None,
        allow_draft: bool = False,
    ) -> TemplatePreview:
        root = self._get_revision(
            template_id,
            revision,
            published_only=not allow_draft,
        )
        dependency_order, missing_optional_dependencies = self._resolve_dependency_order(root)

        missing_required_capabilities: set[str] = set()
        missing_optional_capabilities: set[str] = set()
        missing_plugins: set[str] = set()
        missing_connectors: set[str] = set()
        missing_models: set[str] = set()
        ungrantable_permissions: set[str] = set()
        missing_workspaces: set[str] = set()
        unresolved_placeholders: set[str] = set()
        unresolved_secret_placeholders: set[str] = set()
        unvalidated_configuration_refs: set[str] = set()
        missing_handler_types: set[str] = set()
        privileged_capabilities: set[str] = set()
        resource_changes: list[TemplateResourceChange] = []

        for item in dependency_order:
            requirements = item.content.requirements
            for capability in requirements.capabilities:
                if capability.privileged:
                    privileged_capabilities.add(capability.capability_id)
                if capability.capability_id not in environment.capability_ids:
                    target = (
                        missing_optional_capabilities
                        if capability.optional
                        else missing_required_capabilities
                    )
                    target.add(capability.capability_id)
            missing_plugins.update(set(requirements.plugin_ids) - environment.plugin_ids)
            missing_connectors.update(set(requirements.connector_ids) - environment.connector_ids)
            missing_models.update(
                set(requirements.model_policy_refs) - environment.model_policy_refs
            )
            ungrantable_permissions.update(
                set(requirements.permission_actions) - environment.grantable_permissions
            )
            missing_workspaces.update(
                set(requirements.workspace_prerequisites) - environment.workspace_prerequisites
            )
            unresolved_placeholders.update(
                set(requirements.placeholders) - environment.resolved_placeholders
            )
            unresolved_secret_placeholders.update(
                set(requirements.secret_reference_placeholders)
                - environment.resolved_secret_reference_placeholders
            )
            reference = item.content.configuration.reference
            if reference is not None and reference not in environment.validated_configuration_refs:
                unvalidated_configuration_refs.add(reference)

            handler = self.handlers.get(item.content.template_type)
            if handler is None:
                missing_handler_types.add(item.content.template_type.value)
            else:
                resource_changes.extend(handler.preview(item))

        del applied_by  # Reserved for concrete authorization-provider integration.
        return TemplatePreview(
            source=root.ref,
            dependency_order=tuple(item.ref for item in dependency_order),
            missing_required_capability_ids=tuple(sorted(missing_required_capabilities)),
            missing_optional_capability_ids=tuple(sorted(missing_optional_capabilities)),
            missing_plugin_ids=tuple(sorted(missing_plugins)),
            missing_connector_ids=tuple(sorted(missing_connectors)),
            missing_model_policy_refs=tuple(sorted(missing_models)),
            ungrantable_permissions=tuple(sorted(ungrantable_permissions)),
            missing_workspace_prerequisites=tuple(sorted(missing_workspaces)),
            unresolved_placeholders=tuple(sorted(unresolved_placeholders)),
            unresolved_secret_reference_placeholders=tuple(sorted(unresolved_secret_placeholders)),
            unvalidated_configuration_refs=tuple(sorted(unvalidated_configuration_refs)),
            missing_optional_dependencies=missing_optional_dependencies,
            missing_handler_types=tuple(sorted(missing_handler_types)),
            privileged_capability_ids=tuple(sorted(privileged_capabilities)),
            resource_changes=tuple(resource_changes),
        )

    def apply(
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
        if preview.ungrantable_permissions:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "template requests permissions the applying actor cannot grant",
                details={"permissions": list(preview.ungrantable_permissions)},
            )
        if not preview.applicable:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "template is not compatible with the target environment",
                details={
                    "missing_capabilities": list(preview.missing_required_capability_ids),
                    "missing_plugins": list(preview.missing_plugin_ids),
                    "missing_connectors": list(preview.missing_connector_ids),
                    "missing_model_policies": list(preview.missing_model_policy_refs),
                    "missing_workspace_prerequisites": list(
                        preview.missing_workspace_prerequisites
                    ),
                    "unresolved_placeholders": list(preview.unresolved_placeholders),
                    "unresolved_secret_reference_placeholders": list(
                        preview.unresolved_secret_reference_placeholders
                    ),
                    "unvalidated_configuration_refs": list(preview.unvalidated_configuration_refs),
                    "missing_handler_types": list(preview.missing_handler_types),
                },
            )

        root = self._get_revision(
            template_id,
            revision,
            published_only=not allow_draft,
        )
        dependency_order, _ = self._resolve_dependency_order(root)
        resource_refs: list[TemplateResourceRef] = []
        for item in dependency_order:
            handler = self.handlers.get(item.content.template_type)
            if handler is None:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    f"template handler disappeared during apply: {item.content.template_type.value}",
                )
            provenance = TemplateInstantiationProvenance(source=item.ref, applied_by=applied_by)
            resource_refs.extend(handler.instantiate(item, provenance))

        return TemplateInstantiation(
            source=root.ref,
            applied_by=applied_by,
            resource_refs=tuple(resource_refs),
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
                f"template has no published revision: {template_id}",
            )
        item = self.repository.get_revision(template_id, selected_revision)
        if published_only and item.state is not TemplateRevisionState.PUBLISHED:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"template revision is not published: {template_id}@{selected_revision}",
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
                    "template dependency cycle detected",
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
