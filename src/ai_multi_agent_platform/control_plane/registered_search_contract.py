"""Progressive Search integration for canonical and registered platform domains (#45)."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext
from ai_multi_agent_platform.kernel import TaskState
from ai_multi_agent_platform.search import SearchDocument, SearchResult, document_from_resource

from .extensions import _singular, _validate_resources
from .models import API_VERSION, PageQuery, RequestContext
from .search_contract import ControlPlane as _BaseSearchControlPlane
from .search_contract import ControlPlaneHTTP, build_openapi
from .service import (
    ReferenceCollection,
    _model_provider_resource,
    _model_resource,
    _project_resource,
    _references_for_task,
    _run_resource,
    _workspace_resource,
)

_FOUNDATION_SEARCH_TYPES = frozenset({"project", "workspace", "task", "run"})
_MODEL_SEARCH_AUTHORIZATION = {
    "model": ("models", "model:list"),
    "model-provider": ("model-providers", "model-provider:list"),
}
_REFERENCE_COLLECTIONS: tuple[ReferenceCollection, ...] = (
    "plans",
    "steps",
    "artifacts",
    "results",
)
_REFERENCE_SEARCH_AUTHORIZATION = {
    "plan": ("plans", "plans:list"),
    "step": ("steps", "steps:list"),
    "artifact": ("artifacts", "artifacts:list"),
    "result": ("results", "results:list"),
}
_REFERENCE_SEARCH_TYPES = frozenset(_REFERENCE_SEARCH_AUTHORIZATION)
_BUILTIN_SEARCH_TYPES = (
    _FOUNDATION_SEARCH_TYPES | frozenset(_MODEL_SEARCH_AUTHORIZATION) | _REFERENCE_SEARCH_TYPES
)

ReferenceKey = tuple[str, str]
ReferenceScope = tuple[str, str, str | None]


class ControlPlane(_BaseSearchControlPlane):
    """Search foundation plus progressive discovery of canonical platform resources.

    Built-in canonical domains are rebuilt directly from their owning platform source.
    Registered ResourceServices remain the schema authorities for extension domains.
    Search only derives documents from canonical northbound shapes and reuses each
    domain's existing authorization vocabulary before returning matches.
    """

    async def rebuild_search_index(self, *, correlation_id: str = "search-rebuild") -> int:
        """Rebuild all currently searchable canonical resources from source state."""

        documents: list[SearchDocument] = []
        for project in self._scopes.list_projects():
            documents.append(document_from_resource(_project_resource(project)))

        workspace_provider = self.workspace_provider
        if workspace_provider is None:
            for legacy_workspace in self._scopes.list_workspaces():
                documents.append(document_from_resource(_workspace_resource(legacy_workspace)))
        else:
            for canonical_workspace in await workspace_provider.list_workspaces():
                documents.append(
                    SearchDocument(
                        resource_type="workspace",
                        resource_id=canonical_workspace.id,
                        title=f"Workspace {canonical_workspace.id}",
                        project_id=canonical_workspace.project_id,
                        workspace_id=canonical_workspace.id,
                        owner_type=canonical_workspace.owner_ref.type,
                        owner_id=canonical_workspace.owner_ref.id,
                        keywords=(
                            "workspace",
                            canonical_workspace.id,
                            canonical_workspace.project_id,
                        ),
                        updated_at=canonical_workspace.updated_at.isoformat(),
                        canonical_ref=f"/api/{API_VERSION}/workspaces/{canonical_workspace.id}",
                        provenance={"indexed_from": "canonical-workspace-provider"},
                    )
                )

        reference_resources: dict[ReferenceKey, dict[str, JsonValue]] = {}
        reference_scopes: dict[ReferenceKey, list[ReferenceScope]] = {}
        ambiguous_reference_keys: set[ReferenceKey] = set()

        binding_repository = self.run_workspace_bindings
        for task_id in await self._task_ids():
            task = await self._kernel.get_task(task_id)
            documents.append(document_from_resource(await self._managed_task_resource(task)))
            _collect_reference_search_state(
                task,
                resources=reference_resources,
                scopes=reference_scopes,
                ambiguous_keys=ambiguous_reference_keys,
            )
            for run_id in task.run_ids:
                run = await self._kernel.get_run(task_id, run_id)
                run_document = document_from_resource(_run_resource(run))
                workspace_id = None
                if binding_repository is not None:
                    binding = await binding_repository.get(run_id)
                    if binding is not None:
                        workspace_id = binding.workspace_id
                documents.append(
                    replace(
                        run_document,
                        workspace_id=workspace_id,
                        owner_type=task.task.owner_ref.type,
                        owner_id=task.task.owner_ref.id,
                    )
                )

        reference_documents, reference_authorization = _reference_search_documents(
            reference_resources,
            reference_scopes,
            ambiguous_reference_keys,
        )
        documents.extend(reference_documents)

        model_registry = self._model_registry
        if model_registry is not None:
            for provider in model_registry.list_providers():
                documents.append(
                    document_from_resource(_model_provider_resource(model_registry, provider))
                )
            for model in model_registry.list_models():
                documents.append(document_from_resource(_model_resource(model_registry, model)))

        (
            extension_documents,
            extension_authorization,
        ) = await self._registered_extension_search_documents(correlation_id)
        documents.extend(extension_documents)

        await self._search_provider.rebuild(
            tuple(documents),
            OperationContext(correlation_id=correlation_id),
        )
        self._search_reference_authorization = reference_authorization
        self._search_extension_authorization = extension_authorization
        return len(documents)

    async def _registered_extension_search_documents(
        self,
        correlation_id: str,
    ) -> tuple[list[SearchDocument], dict[str, tuple[str, str]]]:
        """Derive documents from registered northbound resources, never backend state."""

        documents: list[SearchDocument] = []
        authorization: dict[str, tuple[str, str]] = {}
        context = RequestContext(
            request_id=f"request_{uuid4()}",
            correlation_id=correlation_id,
        )
        query = PageQuery(limit=200)

        for collection in self.registered_collections:
            service = self._registered_resource_service(collection)
            resources = list(await service.list_resources(context, query))
            _validate_resources(collection, resources)
            action = f"{_singular(collection)}:list"

            for resource in resources:
                document = document_from_resource(resource, collection=collection)
                if document.resource_type in _BUILTIN_SEARCH_TYPES:
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "registered Search resource type conflicts with a built-in Search type",
                        details={
                            "resource_type": document.resource_type,
                            "collection": collection,
                        },
                    )

                current = (collection, action)
                previous = authorization.get(document.resource_type)
                if previous is not None and previous != current:
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "registered Search resource type maps to multiple canonical collections",
                        details={
                            "resource_type": document.resource_type,
                            "collections": [previous[0], collection],
                        },
                    )
                authorization[document.resource_type] = current
                documents.append(document)

        return documents, authorization

    async def _search_result_allowed(
        self,
        context: RequestContext,
        result: SearchResult,
    ) -> bool:
        if result.resource_type in _FOUNDATION_SEARCH_TYPES:
            return await super()._search_result_allowed(context, result)

        reference_authorization = _REFERENCE_SEARCH_AUTHORIZATION.get(result.resource_type)
        if reference_authorization is not None:
            _, action = reference_authorization
            authorization = getattr(self, "_search_reference_authorization", {})
            scopes = authorization.get((result.resource_type, result.resource_id), ())
            for owner_type, owner_id, project_id in scopes:
                if await self._allowed(
                    context,
                    action,
                    result.resource_id,
                    owner_type=owner_type,
                    owner_id=owner_id,
                    project_id=project_id,
                ):
                    return True
            return False

        model_authorization = _MODEL_SEARCH_AUTHORIZATION.get(result.resource_type)
        if model_authorization is not None:
            collection, action = model_authorization
            return await self._allowed(context, action, collection)

        authorization = getattr(self, "_search_extension_authorization", {})
        extension = authorization.get(result.resource_type)
        if extension is None:
            return False
        collection, action = extension
        return await self._allowed(
            context,
            action,
            collection,
            owner_type=result.owner_type,
            owner_id=result.owner_id,
            project_id=result.project_id,
        )


def _collect_reference_search_state(
    task: TaskState,
    *,
    resources: dict[ReferenceKey, dict[str, JsonValue]],
    scopes: dict[ReferenceKey, list[ReferenceScope]],
    ambiguous_keys: set[ReferenceKey],
) -> None:
    """Collect canonical task references and every scope through which they are visible."""

    scope = (
        task.task.owner_ref.type,
        task.task.owner_ref.id,
        task.task.project_id,
    )
    for collection in _REFERENCE_COLLECTIONS:
        for resource in _references_for_task(task, collection):
            resource_type = resource.get("type")
            resource_id = resource.get("id")
            if not isinstance(resource_type, str) or not isinstance(resource_id, str):
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "canonical task reference is missing type or id",
                )
            key = (resource_type, resource_id)
            canonical_resource = dict(resource)
            previous = resources.get(key)
            if previous is not None and previous != canonical_resource:
                ambiguous_keys.add(key)
            else:
                resources.setdefault(key, canonical_resource)
            resource_scopes = scopes.setdefault(key, [])
            if scope not in resource_scopes:
                resource_scopes.append(scope)


def _reference_search_documents(
    resources: dict[ReferenceKey, dict[str, JsonValue]],
    scopes: dict[ReferenceKey, list[ReferenceScope]],
    ambiguous_keys: set[ReferenceKey],
) -> tuple[list[SearchDocument], dict[ReferenceKey, tuple[ReferenceScope, ...]]]:
    """Build privacy-safe documents plus multi-scope authorization metadata.

    A reference attached to multiple tasks is indexed without task, owner or Project
    relationship metadata. Authorization still succeeds when the caller can see at
    least one canonical attachment, matching the Control Plane reference-list semantics
    without leaking relationships from a different scope.
    """

    documents: list[SearchDocument] = []
    authorization: dict[ReferenceKey, tuple[ReferenceScope, ...]] = {}
    for key in sorted(resources):
        resource_type, resource_id = key
        resource_scopes = tuple(scopes.get(key, ()))
        if not resource_scopes:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "searchable task reference has no canonical authorization scope",
                details={"resource_type": resource_type, "resource_id": resource_id},
            )

        if key in ambiguous_keys or len(resource_scopes) > 1:
            searchable_resource: dict[str, JsonValue] = {
                "id": resource_id,
                "type": resource_type,
            }
        else:
            searchable_resource = dict(resources[key])
            owner_type, owner_id, project_id = resource_scopes[0]
            searchable_resource["owner"] = {"type": owner_type, "id": owner_id}
            searchable_resource["project_id"] = project_id

        documents.append(document_from_resource(searchable_resource))
        authorization[key] = resource_scopes

    return documents, authorization


__all__ = ["ControlPlane", "ControlPlaneHTTP", "build_openapi"]
