"""Progressive Search integration for explicitly registered canonical domains (#45)."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.search import SearchDocument, SearchResult, document_from_resource

from .extensions import _singular, _validate_resources
from .models import API_VERSION, PageQuery, RequestContext
from .search_contract import ControlPlane as _BaseSearchControlPlane
from .search_contract import ControlPlaneHTTP, build_openapi
from .service import _project_resource, _run_resource, _task_resource, _workspace_resource

_CORE_SEARCH_TYPES = frozenset({"project", "workspace", "task", "run"})


class ControlPlane(_BaseSearchControlPlane):
    """Search Stage 1 plus safe discovery of registered canonical extensions.

    Registered ResourceServices remain the schema authorities for their domains. Search
    only derives documents from the same northbound resources and mirrors each
    collection's canonical list-authorization action before returning matches.
    """

    async def rebuild_search_index(self, *, correlation_id: str = "search-rebuild") -> int:
        """Rebuild core and currently registered canonical resources from source state."""

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

        binding_repository = self.run_workspace_bindings
        for task_id in await self._task_ids():
            task = await self._kernel.get_task(task_id)
            documents.append(document_from_resource(_task_resource(task)))
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

        (
            extension_documents,
            extension_authorization,
        ) = await self._registered_extension_search_documents(correlation_id)
        documents.extend(extension_documents)

        await self._search_provider.rebuild(
            tuple(documents),
            OperationContext(correlation_id=correlation_id),
        )
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
                if document.resource_type in _CORE_SEARCH_TYPES:
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "registered Search resource type conflicts with a core Search type",
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
        if result.resource_type in _CORE_SEARCH_TYPES:
            return await super()._search_result_allowed(context, result)

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


__all__ = ["ControlPlane", "ControlPlaneHTTP", "build_openapi"]
