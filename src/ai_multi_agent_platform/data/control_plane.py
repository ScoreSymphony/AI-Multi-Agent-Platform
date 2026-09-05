"""Control Plane projections for canonical data resources."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    Capability,
    JsonValue,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.domain import validate_id

from .contracts import FileProvider, KnowledgeProvider, MemoryProvider
from .models import (
    DataAccessContext,
    FileRecord,
    KnowledgeDocument,
    KnowledgeSearchMode,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
    KnowledgeSource,
    KnowledgeStatus,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
)
from .registry import DataProviderSet

DATA_PROVIDER_COLLECTION = "data-providers"
FILE_COLLECTION = "files"
MEMORY_COLLECTION = "memory"
KNOWLEDGE_COLLECTION = "knowledge"
KNOWLEDGE_DOCUMENT_COLLECTION = "knowledge-documents"
KNOWLEDGE_RESULT_COLLECTION = "knowledge-results"
ProjectIdProvider = Callable[[], tuple[str, ...]]

_DISCOVERY_DEGRADED_CODES = frozenset(
    {
        ErrorCode.UNSUPPORTED_CAPABILITY,
        ErrorCode.UNAVAILABLE,
        ErrorCode.TRANSIENT_FAILURE,
        ErrorCode.BACKEND_ERROR,
    }
)


class DataProviderResourceService:
    """Administrative health/metadata inventory for File, Memory and Knowledge providers."""

    def __init__(self, providers: DataProviderSet) -> None:
        self._providers = providers
        _validate_unique_provider_ids(self._descriptors())

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(
            _provider_resource(role, descriptor) for role, descriptor in self._descriptors()
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        for role, descriptor in self._descriptors():
            if descriptor.provider_id == resource_id:
                return _provider_resource(role, descriptor)
        raise ContractError(
            ErrorCode.NOT_FOUND,
            f"data provider not found: {resource_id}",
        )

    def _descriptors(self) -> tuple[tuple[str, ProviderDescriptor], ...]:
        return (
            ("file", self._providers.files.descriptor),
            ("memory", self._providers.memory.descriptor),
            ("knowledge", self._providers.knowledge.descriptor),
        )


class FileResourceService:
    """Safe northbound metadata projection over the canonical #13 FileProvider.

    File bytes never enter this read model. Project-aware providers are enumerated once
    for the unscoped namespace and once for every canonical Project supplied by the
    composition root. Authorization-enforcing FileProvider decorators may reject an
    individual scope; such scopes are omitted rather than leaking their existence.
    """

    def __init__(
        self,
        files: FileProvider,
        *,
        project_ids: ProjectIdProvider | None = None,
    ) -> None:
        self._files = files
        self._project_ids = project_ids or (lambda: ())

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        requested_project = (query.filters or {}).get("project_id")
        if requested_project is not None:
            validate_id(requested_project, "project")
            scope_ids: tuple[str | None, ...] = (requested_project,)
        else:
            scope_ids = (None, *tuple(dict.fromkeys(self._project_ids())))

        resources: dict[str, dict[str, JsonValue]] = {}
        for project_id in scope_ids:
            try:
                records = await self._files.list_files(
                    _data_access_context(context, project_id=project_id)
                )
            except ContractError as exc:
                if exc.code in {ErrorCode.FORBIDDEN, ErrorCode.UNAUTHORIZED}:
                    continue
                raise
            for record in records:
                resources[record.file_id] = _file_resource(record)
        return tuple(resources[file_id] for file_id in sorted(resources))

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        validate_id(resource_id, "file")
        scope_ids: tuple[str | None, ...] = (None, *tuple(dict.fromkeys(self._project_ids())))
        for project_id in scope_ids:
            try:
                record = await self._files.get_file(
                    resource_id,
                    _data_access_context(context, project_id=project_id),
                )
            except ContractError as exc:
                if exc.code in {ErrorCode.NOT_FOUND, ErrorCode.FORBIDDEN, ErrorCode.UNAUTHORIZED}:
                    continue
                raise
            return _file_resource(record)
        raise ContractError(ErrorCode.NOT_FOUND, f"file not found: {resource_id}")

    def authorization_scope(
        self,
        resource: Mapping[str, JsonValue],
    ) -> tuple[str | None, str | None, str | None]:
        return _owner_project_scope(resource, resource_kind="file")


class MemoryResourceService:
    """Scoped Memory content plus a privacy-minimized global discovery projection."""

    def __init__(
        self,
        memory: MemoryProvider,
        *,
        project_ids: ProjectIdProvider | None = None,
    ) -> None:
        self._memory = memory
        self._project_ids = project_ids or (lambda: ())

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        scope, scope_id, project_id = _memory_scope_query(context, query)
        memory_query = MemoryQuery(
            scope=scope,
            scope_id=scope_id,
            owner_ref=(query.filters or {}).get("owner_ref"),
            include_expired=_boolean_filter(query, "include_expired"),
            include_superseded=_boolean_filter(query, "include_superseded"),
            limit=10_000,
        )
        access = _data_access_context(context, project_id=project_id)
        if query.search is not None:
            entries = await self._memory.search_entries(memory_query, query.search, access)
        else:
            entries = await self._memory.query_entries(memory_query, access)
        return tuple(_memory_resource(entry) for entry in entries)

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        """Enumerate current canonical Memory without values/private metadata."""

        try:
            entries = await self._memory.list_entries_for_discovery()
        except ContractError as exc:
            if exc.code in _DISCOVERY_DEGRADED_CODES:
                return ()
            raise
        return tuple(_memory_search_resource(entry) for entry in entries)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        validate_id(resource_id, "memory")
        for project_id in _candidate_project_ids(self._project_ids):
            try:
                entry = await self._memory.get_entry(
                    resource_id,
                    _data_access_context(context, project_id=project_id),
                )
            except ContractError as exc:
                if exc.code in {ErrorCode.NOT_FOUND, ErrorCode.FORBIDDEN, ErrorCode.UNAUTHORIZED}:
                    continue
                raise
            return _memory_resource(entry)
        raise ContractError(ErrorCode.NOT_FOUND, f"memory not found: {resource_id}")

    def authorization_scope(
        self,
        resource: Mapping[str, JsonValue],
    ) -> tuple[str | None, str | None, str | None]:
        return _owner_project_scope(resource, resource_kind="memory")


class KnowledgeResourceService:
    """Canonical Knowledge source metadata; provider-private index identity is excluded."""

    def __init__(
        self,
        knowledge: KnowledgeProvider,
        *,
        project_ids: ProjectIdProvider | None = None,
    ) -> None:
        self._knowledge = knowledge
        self._project_ids = project_ids or (lambda: ())

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        resources: dict[str, dict[str, JsonValue]] = {}
        for project_id in _requested_project_ids(query, self._project_ids):
            try:
                sources = await self._knowledge.list_sources(
                    _data_access_context(context, project_id=project_id)
                )
            except ContractError as exc:
                if exc.code in {ErrorCode.FORBIDDEN, ErrorCode.UNAUTHORIZED}:
                    continue
                raise
            for source in sources:
                resources[source.source_id] = _knowledge_source_resource(source)
        return tuple(resources[source_id] for source_id in sorted(resources))

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        try:
            sources = await self._knowledge.list_sources_for_discovery()
        except ContractError as exc:
            if exc.code in _DISCOVERY_DEGRADED_CODES:
                return ()
            raise
        return tuple(
            _knowledge_source_search_resource(source)
            for source in sources
            if source.status is not KnowledgeStatus.REMOVED
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        validate_id(resource_id, "knowledge_source")
        for project_id in _candidate_project_ids(self._project_ids):
            try:
                source = await self._knowledge.get_source(
                    resource_id,
                    _data_access_context(context, project_id=project_id),
                )
            except ContractError as exc:
                if exc.code in {ErrorCode.NOT_FOUND, ErrorCode.FORBIDDEN, ErrorCode.UNAUTHORIZED}:
                    continue
                raise
            return _knowledge_source_resource(source)
        raise ContractError(ErrorCode.NOT_FOUND, f"knowledge source not found: {resource_id}")

    def authorization_scope(
        self,
        resource: Mapping[str, JsonValue],
    ) -> tuple[str | None, str | None, str | None]:
        return _owner_project_scope(resource, resource_kind="knowledge source")


class KnowledgeDocumentResourceService:
    """Metadata-only read/discovery view over subordinate canonical Knowledge documents."""

    def __init__(self, knowledge: KnowledgeProvider) -> None:
        self._knowledge = knowledge

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context
        resources = await self._document_resources()
        requested_project = (query.filters or {}).get("project_id")
        if requested_project is not None:
            validate_id(requested_project, "project")
            resources = tuple(
                resource for resource in resources if resource.get("project_id") == requested_project
            )
        return resources

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        return await self._document_resources()

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        validate_id(resource_id, "knowledge_document")
        for resource in await self._document_resources():
            if resource.get("id") == resource_id:
                return resource
        raise ContractError(ErrorCode.NOT_FOUND, f"knowledge document not found: {resource_id}")

    def authorization_scope(
        self,
        resource: Mapping[str, JsonValue],
    ) -> tuple[str | None, str | None, str | None]:
        return _owner_project_scope(resource, resource_kind="knowledge document")

    async def _document_resources(self) -> tuple[dict[str, JsonValue], ...]:
        try:
            sources = await self._knowledge.list_sources_for_discovery()
            documents = await self._knowledge.list_documents_for_discovery()
        except ContractError as exc:
            if exc.code in _DISCOVERY_DEGRADED_CODES:
                return ()
            raise
        source_by_id = {
            source.source_id: source
            for source in sources
            if source.status is not KnowledgeStatus.REMOVED
        }
        resources: list[dict[str, JsonValue]] = []
        for document in documents:
            source = source_by_id.get(document.source_id)
            if source is None or document.revision != source.revision:
                continue
            resources.append(_knowledge_document_search_resource(document, source))
        resources.sort(key=lambda resource: str(resource["id"]))
        return tuple(resources)


class KnowledgeResultResourceService:
    """Authorized source-backed retrieval results with canonical citations."""

    search_indexable = False

    def __init__(
        self,
        knowledge: KnowledgeProvider,
        *,
        project_ids: ProjectIdProvider | None = None,
    ) -> None:
        self._knowledge = knowledge
        self._project_ids = project_ids or (lambda: ())

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        if query.search is None or not query.search.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "knowledge-results requires a non-blank search query",
            )
        filters = query.filters or {}
        raw_mode = filters.get("mode", KnowledgeSearchMode.KEYWORD.value)
        try:
            mode = KnowledgeSearchMode(raw_mode)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"unsupported knowledge search mode: {raw_mode}",
            ) from exc
        source_id = filters.get("source_id")
        source_ids: tuple[str, ...] = ()
        if source_id is not None:
            validate_id(source_id, "knowledge_source")
            source_ids = (source_id,)

        resources: dict[str, dict[str, JsonValue]] = {}
        for project_id in _requested_project_ids(query, self._project_ids):
            try:
                results = await self._knowledge.search(
                    KnowledgeSearchRequest(
                        query=query.search,
                        context=_data_access_context(context, project_id=project_id),
                        source_ids=source_ids,
                        mode=mode,
                        limit=10_000,
                    )
                )
            except ContractError as exc:
                if exc.code in {ErrorCode.FORBIDDEN, ErrorCode.UNAUTHORIZED}:
                    continue
                raise
            for result in results:
                resources[result.document_id] = _knowledge_result_resource(result)
        return tuple(
            sorted(
                resources.values(),
                key=lambda item: (
                    -float(score)
                    if isinstance((score := item.get("score")), (int, float))
                    else 0.0,
                    str(item["id"]),
                ),
            )
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context, resource_id
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "knowledge retrieval results are query-scoped and do not have a standalone read route",
        )

    def authorization_scope(
        self,
        resource: Mapping[str, JsonValue],
    ) -> tuple[str | None, str | None, str | None]:
        return _owner_project_scope(resource, resource_kind="knowledge result")


def data_resource_services(
    providers: DataProviderSet,
    *,
    project_ids: ProjectIdProvider | None = None,
) -> dict[
    str,
    DataProviderResourceService
    | FileResourceService
    | MemoryResourceService
    | KnowledgeResourceService
    | KnowledgeDocumentResourceService
    | KnowledgeResultResourceService,
]:
    return {
        DATA_PROVIDER_COLLECTION: DataProviderResourceService(providers),
        FILE_COLLECTION: FileResourceService(providers.files, project_ids=project_ids),
        MEMORY_COLLECTION: MemoryResourceService(providers.memory, project_ids=project_ids),
        KNOWLEDGE_COLLECTION: KnowledgeResourceService(
            providers.knowledge,
            project_ids=project_ids,
        ),
        KNOWLEDGE_DOCUMENT_COLLECTION: KnowledgeDocumentResourceService(providers.knowledge),
        KNOWLEDGE_RESULT_COLLECTION: KnowledgeResultResourceService(
            providers.knowledge,
            project_ids=project_ids,
        ),
    }


def _data_access_context(
    context: RequestContext,
    *,
    project_id: str | None,
) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id=context.correlation_id,
            owner_type=context.actor.owner_type,
            owner_id=context.actor.owner_id,
            project_id=project_id,
        ),
        actor_ref=context.actor.principal_ref,
    )


def _memory_scope_query(
    context: RequestContext,
    query: PageQuery,
) -> tuple[MemoryScope, str, str | None]:
    filters = query.filters or {}
    raw_scope = filters.get("scope")
    if raw_scope is None:
        if context.actor.owner_type == "user" and context.actor.owner_id is not None:
            scope = MemoryScope.USER
        else:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "memory list requires explicit scope and scope_id outside a user context",
            )
    else:
        try:
            scope = MemoryScope(raw_scope)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"unknown memory scope: {raw_scope}",
            ) from exc

    scope_id = filters.get("scope_id")
    if scope is MemoryScope.USER and scope_id is None:
        if context.actor.owner_type == "user" and context.actor.owner_id is not None:
            scope_id = context.actor.owner_id
    if scope is MemoryScope.WORKSPACE and scope_id is None:
        scope_id = filters.get("project_id")
    if scope_id is None or not scope_id.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "memory list requires a non-blank scope_id",
        )
    project_id = scope_id if scope is MemoryScope.WORKSPACE else filters.get("project_id")
    if project_id is not None:
        validate_id(project_id, "project")
    return scope, scope_id, project_id


def _boolean_filter(query: PageQuery, key: str) -> bool:
    raw = (query.filters or {}).get(key)
    if raw is None:
        return False
    normalized = raw.casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be true or false")


def _requested_project_ids(
    query: PageQuery,
    project_ids: ProjectIdProvider,
) -> tuple[str | None, ...]:
    requested_project = (query.filters or {}).get("project_id")
    if requested_project is not None:
        validate_id(requested_project, "project")
        return (requested_project,)
    return _candidate_project_ids(project_ids)


def _candidate_project_ids(project_ids: ProjectIdProvider) -> tuple[str | None, ...]:
    return (None, *tuple(dict.fromkeys(project_ids())))


def _file_resource(record: FileRecord) -> dict[str, JsonValue]:
    return {
        "id": record.file_id,
        "type": "file",
        "project_id": record.project_id,
        "owner_ref": record.owner_ref,
        "created_by": record.created_by,
        "created_at": record.created_at.isoformat(),
        "size_bytes": record.size_bytes,
        "sha256": record.sha256,
        "state": record.state.value,
        "content_type": record.content_type,
        "artifact_ids": list(record.artifact_ids),
        "metadata": dict(record.metadata),
    }


def _memory_resource(entry: MemoryEntry) -> dict[str, JsonValue]:
    provenance: list[JsonValue] = [
        {
            "kind": source.kind,
            "ref": source.ref,
            "location": source.location,
            "revision": source.revision,
            "checksum": source.checksum,
        }
        for source in entry.provenance
    ]
    project_id = entry.scope_id if entry.scope is MemoryScope.WORKSPACE else None
    return {
        "id": entry.memory_id,
        "type": "memory",
        "scope": entry.scope.value,
        "scope_id": entry.scope_id,
        "project_id": project_id,
        "owner_ref": entry.owner_ref,
        "created_by": entry.created_by,
        "created_at": entry.created_at.isoformat(),
        "value": entry.value,
        "origin": entry.origin.value,
        "retention": entry.retention.value,
        "expires_at": entry.expires_at.isoformat() if entry.expires_at is not None else None,
        "provenance": provenance,
        "supersedes_memory_id": entry.supersedes_memory_id,
        "superseded_by_memory_id": entry.superseded_by_memory_id,
        "classification": entry.classification,
        "metadata": dict(entry.metadata),
    }


def _memory_search_resource(entry: MemoryEntry) -> dict[str, JsonValue]:
    owner_ref = entry.owner_ref
    if entry.scope is MemoryScope.USER:
        owner_ref = f"user:{entry.scope_id}"
    elif entry.scope is MemoryScope.AGENT:
        owner_ref = f"agent:{entry.scope_id}"
    elif entry.scope is MemoryScope.ORGANIZATION:
        owner_ref = f"organization:{entry.scope_id}"
    project_id = entry.scope_id if entry.scope is MemoryScope.WORKSPACE else None
    resource: dict[str, JsonValue] = {
        "id": entry.memory_id,
        "type": "memory",
        "scope": entry.scope.value,
        "scope_id": entry.scope_id,
        "project_id": project_id,
        "owner_ref": owner_ref,
        "created_at": entry.created_at.isoformat(),
        "origin": entry.origin.value,
        "retention": entry.retention.value,
        "expires_at": entry.expires_at.isoformat() if entry.expires_at is not None else None,
        "provenance_refs": [source.ref for source in entry.provenance],
    }
    if entry.scope is MemoryScope.ORGANIZATION:
        resource["organization_id"] = entry.scope_id
    return resource


def _knowledge_source_resource(source: KnowledgeSource) -> dict[str, JsonValue]:
    return {
        "id": source.source_id,
        "type": "knowledge-source",
        "project_id": source.project_id,
        "owner_ref": source.owner_ref,
        "created_by": source.created_by,
        "title": source.title,
        "revision": source.revision,
        "status": source.status.value,
        "created_at": source.created_at.isoformat(),
        "updated_at": source.updated_at.isoformat(),
        "content_checksum": source.content_checksum,
        "metadata": dict(source.metadata),
    }


def _knowledge_source_search_resource(source: KnowledgeSource) -> dict[str, JsonValue]:
    return {
        "id": source.source_id,
        "type": "knowledge-source",
        "project_id": source.project_id,
        "owner_ref": source.owner_ref,
        "title": source.title,
        "revision": source.revision,
        "status": source.status.value,
        "created_at": source.created_at.isoformat(),
        "updated_at": source.updated_at.isoformat(),
    }


def _knowledge_document_search_resource(
    document: KnowledgeDocument,
    source: KnowledgeSource,
) -> dict[str, JsonValue]:
    return {
        "id": document.document_id,
        "type": "knowledge-document",
        "source_id": document.source_id,
        "project_id": source.project_id,
        "owner_ref": source.owner_ref,
        "title": f"{source.title} — revision {document.revision}",
        "revision": document.revision,
        "status": source.status.value,
        "created_at": document.created_at.isoformat(),
        "updated_at": source.updated_at.isoformat(),
    }


def _knowledge_result_resource(result: KnowledgeSearchResult) -> dict[str, JsonValue]:
    return {
        "id": result.document_id,
        "type": "knowledge-result",
        "source_id": result.source_id,
        "project_id": None,
        "owner_ref": None,
        "revision": result.revision,
        "content": result.content,
        "location": result.location,
        "score": result.score,
        "citation": {
            "kind": result.citation.kind,
            "ref": result.citation.ref,
            "location": result.citation.location,
            "revision": result.citation.revision,
            "checksum": result.citation.checksum,
        },
    }


def _owner_project_scope(
    resource: Mapping[str, JsonValue],
    *,
    resource_kind: str,
) -> tuple[str | None, str | None, str | None]:
    project_value = resource.get("project_id")
    if project_value is not None and not isinstance(project_value, str):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            f"canonical {resource_kind} project_id must be a string or null",
        )
    project_id = project_value if isinstance(project_value, str) else None

    owner_ref = resource.get("owner_ref")
    if owner_ref is None:
        return None, None, project_id
    if not isinstance(owner_ref, str) or ":" not in owner_ref:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            f"canonical {resource_kind} owner_ref must use type:id form",
        )
    owner_type, owner_id = owner_ref.split(":", 1)
    if not owner_type.strip() or not owner_id.strip():
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            f"canonical {resource_kind} owner_ref must use non-blank type:id values",
        )
    return owner_type, owner_id, project_id


def _provider_resource(role: str, descriptor: ProviderDescriptor) -> dict[str, JsonValue]:
    capabilities: list[JsonValue] = [
        _capability_resource(capability) for capability in descriptor.capabilities
    ]
    return {
        "id": descriptor.provider_id,
        "type": "data-provider",
        "role": role,
        "provider_type": descriptor.provider_type,
        "contract_version": descriptor.contract_version,
        "supported_operations": list(descriptor.supported_operations),
        "capabilities": capabilities,
        "health": descriptor.health.value,
        "available": descriptor.available,
        "limits": dict(descriptor.limits),
        "resources": dict(descriptor.resources),
    }


def _capability_resource(capability: Capability) -> dict[str, JsonValue]:
    return {
        "name": capability.name,
        "kind": capability.kind.value,
        "version": capability.version,
        "supported_operations": list(capability.supported_operations),
        "modalities": list(capability.modalities),
        "features": list(capability.features),
        "limits": dict(capability.limits),
        "attributes": dict(capability.attributes),
    }


def _validate_unique_provider_ids(
    descriptors: tuple[tuple[str, ProviderDescriptor], ...],
) -> None:
    provider_ids = [descriptor.provider_id for _, descriptor in descriptors]
    if len(set(provider_ids)) != len(provider_ids):
        raise ValueError("File, Memory and Knowledge providers must use unique provider IDs")
