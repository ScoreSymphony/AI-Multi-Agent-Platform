"""Canonical data-boundary models for files, scoped memory and knowledge."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext
from ai_multi_agent_platform.domain import new_id, validate_id

SHORT_TERM_MAX_LIFETIME = timedelta(hours=24)


class FileState(StrEnum):
    PENDING = "pending"
    READY = "ready"
    TOMBSTONED = "tombstoned"


class MemoryScope(StrEnum):
    SHORT_TERM = "short_term"
    TASK = "task"
    AGENT = "agent"
    WORKSPACE = "workspace"
    USER = "user"
    HISTORICAL = "historical"
    ORGANIZATION = "organization"


class MemoryOrigin(StrEnum):
    """Canonical provenance class for Memory content."""

    USER_AUTHORED = "user-authored"
    AGENT_DERIVED = "agent-derived"
    IMPORTED = "imported"


class RetentionPolicy(StrEnum):
    EPHEMERAL = "ephemeral"
    TASK_LIFETIME = "task_lifetime"
    PROJECT_LIFETIME = "project_lifetime"
    USER_LIFETIME = "user_lifetime"
    DURABLE = "durable"
    UNTIL = "until"


class KnowledgeStatus(StrEnum):
    REGISTERED = "registered"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"
    REMOVED = "removed"


class KnowledgeSearchMode(StrEnum):
    KEYWORD = "keyword"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class MemoryAccessPolicy:
    """Provider-neutral access semantics that #15/#33 resolve to authorization decisions.

    Values in ``readers`` and ``writers`` are policy subjects, not backend ACL IDs. This
    keeps memory scope semantics explicit without coupling the data contract to one IAM
    implementation.
    """

    readers: tuple[str, ...]
    writers: tuple[str, ...]
    agent_revision_access: str
    team_access: str
    task_inheritance: str
    cross_project_access: str

    def __post_init__(self) -> None:
        if not self.readers:
            raise ValueError("memory access policy requires at least one reader subject")
        if not self.writers:
            raise ValueError("memory access policy requires at least one writer subject")
        for subject in (*self.readers, *self.writers):
            _require_nonblank(subject, "memory access subject")
        _require_nonblank(self.agent_revision_access, "agent_revision_access")
        _require_nonblank(self.team_access, "team_access")
        _require_nonblank(self.task_inheritance, "task_inheritance")
        _require_nonblank(self.cross_project_access, "cross_project_access")


@dataclass(frozen=True, slots=True)
class DataAccessContext:
    """Authorization/audit context preserved across data-provider calls."""

    operation: OperationContext
    actor_ref: str
    task_id: str | None = None
    run_id: str | None = None
    agent_id: str | None = None
    classification: str | None = None
    audit_metadata: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonblank(self.actor_ref, "actor_ref")
        if self.task_id is not None:
            validate_id(self.task_id, "task")
        if self.run_id is not None:
            validate_id(self.run_id, "run")
        if self.agent_id is not None:
            validate_id(self.agent_id, "agent")
        if self.classification is not None:
            _require_nonblank(self.classification, "classification")

    @property
    def project_id(self) -> str | None:
        return self.operation.project_id


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Provider-neutral provenance link back to canonical/source evidence."""

    kind: str
    ref: str
    location: str | None = None
    revision: str | None = None
    checksum: str | None = None

    def __post_init__(self) -> None:
        _require_nonblank(self.kind, "source kind")
        _require_nonblank(self.ref, "source ref")
        if self.location is not None:
            _require_nonblank(self.location, "source location")
        if self.revision is not None:
            _require_nonblank(self.revision, "source revision")
        if self.checksum is not None:
            _validate_sha256(self.checksum)


@dataclass(frozen=True, slots=True)
class FileRecord:
    file_id: str
    project_id: str | None
    owner_ref: str
    created_by: str
    created_at: datetime
    size_bytes: int
    sha256: str
    state: FileState
    content_type: str | None = None
    artifact_ids: tuple[str, ...] = ()
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.file_id, "file")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        _require_nonblank(self.owner_ref, "owner_ref")
        _require_nonblank(self.created_by, "created_by")
        _require_aware(self.created_at, "created_at")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")
        _validate_sha256(self.sha256)
        if self.content_type is not None:
            _require_nonblank(self.content_type, "content_type")
        for artifact_id in self.artifact_ids:
            validate_id(artifact_id, "artifact")


@dataclass(frozen=True, slots=True)
class OrphanReport:
    missing_objects: tuple[str, ...] = ()
    unreferenced_objects: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for file_id in self.missing_objects:
            validate_id(file_id, "file")
        for file_id in self.unreferenced_objects:
            validate_id(file_id, "file")


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    memory_id: str
    scope: MemoryScope
    scope_id: str
    owner_ref: str
    created_by: str
    value: JsonValue
    created_at: datetime
    retention: RetentionPolicy
    origin: MemoryOrigin = MemoryOrigin.USER_AUTHORED
    expires_at: datetime | None = None
    provenance: tuple[SourceRef, ...] = ()
    supersedes_memory_id: str | None = None
    superseded_by_memory_id: str | None = None
    classification: str | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.memory_id, "memory")
        _validate_memory_scope_id(self.scope, self.scope_id)
        _require_nonblank(self.owner_ref, "owner_ref")
        _require_nonblank(self.created_by, "created_by")
        _require_aware(self.created_at, "created_at")
        if self.expires_at is not None:
            _require_aware(self.expires_at, "expires_at")
            if self.expires_at <= self.created_at:
                raise ValueError("expires_at must be after created_at")
        if self.scope is MemoryScope.SHORT_TERM:
            if self.expires_at is None:
                raise ValueError("short-term memory requires expires_at")
            if self.expires_at - self.created_at > SHORT_TERM_MAX_LIFETIME:
                raise ValueError("short-term memory exceeds maximum lifetime")
        if self.retention is RetentionPolicy.UNTIL and self.expires_at is None:
            raise ValueError("retention=until requires expires_at")
        if self.scope is MemoryScope.HISTORICAL and not self.provenance:
            raise ValueError("historical memory requires provenance")
        if self.scope is not MemoryScope.SHORT_TERM and not self.provenance:
            object.__setattr__(
                self,
                "provenance",
                (SourceRef(kind="memory_writer", ref=self.created_by),),
            )
        if self.supersedes_memory_id is not None:
            validate_id(self.supersedes_memory_id, "memory")
        if self.superseded_by_memory_id is not None:
            validate_id(self.superseded_by_memory_id, "memory")
        if self.classification is not None:
            _require_nonblank(self.classification, "classification")

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= datetime.now(UTC)

    @property
    def access_policy(self) -> MemoryAccessPolicy:
        """Canonical scope policy; #15/#33 decide whether a concrete actor matches it."""

        return memory_access_policy_for_scope(self.scope, self.owner_ref)

    @property
    def execution_ref(self) -> str | None:
        """Persisted execution/session identity for short-term context."""

        if self.scope is MemoryScope.SHORT_TERM:
            return self.scope_id
        return None


@dataclass(frozen=True, slots=True)
class MemoryQuery:
    scope: MemoryScope
    scope_id: str
    owner_ref: str | None = None
    include_expired: bool = False
    include_superseded: bool = False
    limit: int = 100

    def __post_init__(self) -> None:
        _validate_memory_scope_id(self.scope, self.scope_id)
        if self.owner_ref is not None:
            _require_nonblank(self.owner_ref, "owner_ref")
        if self.limit <= 0:
            raise ValueError("limit must be greater than zero")


@dataclass(frozen=True, slots=True)
class KnowledgeSource:
    source_id: str
    project_id: str | None
    owner_ref: str
    created_by: str
    title: str
    revision: str
    status: KnowledgeStatus
    created_at: datetime
    updated_at: datetime
    content_checksum: str | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.source_id, "knowledge_source")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        _require_nonblank(self.owner_ref, "owner_ref")
        _require_nonblank(self.created_by, "created_by")
        _require_nonblank(self.title, "title")
        _require_nonblank(self.revision, "revision")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.content_checksum is not None:
            _validate_sha256(self.content_checksum)


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    document_id: str
    source_id: str
    revision: str
    content: str
    location: str
    checksum: str
    created_at: datetime

    def __post_init__(self) -> None:
        validate_id(self.document_id, "knowledge_document")
        validate_id(self.source_id, "knowledge_source")
        _require_nonblank(self.revision, "revision")
        _require_nonblank(self.location, "location")
        _validate_sha256(self.checksum)
        _require_aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class IndexReference:
    index_id: str
    source_id: str
    revision: str
    status: KnowledgeStatus
    updated_at: datetime

    def __post_init__(self) -> None:
        validate_id(self.index_id, "knowledge_index")
        validate_id(self.source_id, "knowledge_source")
        _require_nonblank(self.revision, "revision")
        _require_aware(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class KnowledgeSearchRequest:
    query: str
    context: DataAccessContext
    source_ids: tuple[str, ...] = ()
    mode: KnowledgeSearchMode = KnowledgeSearchMode.KEYWORD
    limit: int = 20

    def __post_init__(self) -> None:
        _require_nonblank(self.query, "query")
        for source_id in self.source_ids:
            validate_id(source_id, "knowledge_source")
        if self.limit <= 0:
            raise ValueError("limit must be greater than zero")


@dataclass(frozen=True, slots=True)
class KnowledgeSearchResult:
    source_id: str
    document_id: str
    revision: str
    content: str
    location: str
    score: float | None
    citation: SourceRef

    def __post_init__(self) -> None:
        validate_id(self.source_id, "knowledge_source")
        validate_id(self.document_id, "knowledge_document")
        _require_nonblank(self.revision, "revision")
        _require_nonblank(self.location, "location")


def memory_access_policy_for_scope(scope: MemoryScope, owner_ref: str) -> MemoryAccessPolicy:
    """Return the canonical default access semantics for a memory scope.

    These policy subjects are intentionally provider-neutral. Authorization issue #15 and
    agent/team issue #33 resolve the symbolic subjects against concrete identities.
    """

    _require_nonblank(owner_ref, "owner_ref")
    if scope is MemoryScope.SHORT_TERM:
        return MemoryAccessPolicy(
            readers=(owner_ref, "active_context"),
            writers=(owner_ref, "active_context"),
            agent_revision_access="context_bound",
            team_access="deny_by_default",
            task_inheritance="none",
            cross_project_access="deny",
        )
    if scope is MemoryScope.TASK:
        return MemoryAccessPolicy(
            readers=(owner_ref, "authorized_task_participant"),
            writers=(owner_ref, "authorized_task_participant"),
            agent_revision_access="authorized_task_agent_revisions",
            team_access="policy_controlled",
            task_inheritance="same_task_only",
            cross_project_access="deny",
        )
    if scope is MemoryScope.AGENT:
        return MemoryAccessPolicy(
            readers=(owner_ref, "authorized_agent_revision"),
            writers=(owner_ref, "authorized_agent_revision"),
            agent_revision_access="same_agent_policy_controlled",
            team_access="explicit_policy_only",
            task_inheritance="explicit_only",
            cross_project_access="deny_by_default",
        )
    if scope is MemoryScope.WORKSPACE:
        return MemoryAccessPolicy(
            readers=(owner_ref, "authorized_workspace_member"),
            writers=(owner_ref, "authorized_workspace_member"),
            agent_revision_access="workspace_policy_controlled",
            team_access="workspace_policy_controlled",
            task_inheritance="explicit_only",
            cross_project_access="deny",
        )
    if scope is MemoryScope.USER:
        return MemoryAccessPolicy(
            readers=(owner_ref,),
            writers=(owner_ref,),
            agent_revision_access="explicit_user_grant_only",
            team_access="deny_by_default",
            task_inheritance="explicit_user_grant_only",
            cross_project_access="explicit_policy_only",
        )
    if scope is MemoryScope.ORGANIZATION:
        return MemoryAccessPolicy(
            readers=(owner_ref, "authorized_organization_member"),
            writers=(owner_ref, "authorized_organization_memory_writer"),
            agent_revision_access="organization_policy_controlled",
            team_access="organization_policy_controlled",
            task_inheritance="explicit_only",
            cross_project_access="organization_policy_controlled",
        )
    return MemoryAccessPolicy(
        readers=(owner_ref, "authorized_history_reader"),
        writers=(owner_ref, "authorized_history_maintainer"),
        agent_revision_access="history_policy_controlled",
        team_access="history_policy_controlled",
        task_inheritance="none",
        cross_project_access="explicit_policy_only",
    )


def new_file_id() -> str:
    return new_id("file")


def new_memory_id() -> str:
    return new_id("memory")


def new_knowledge_source_id() -> str:
    return new_id("knowledge_source")


def new_knowledge_document_id() -> str:
    return new_id("knowledge_document")


def new_knowledge_index_id() -> str:
    return new_id("knowledge_index")


def _validate_memory_scope_id(scope: MemoryScope, scope_id: str) -> None:
    if scope is MemoryScope.TASK:
        validate_id(scope_id, "task")
        return
    if scope is MemoryScope.AGENT:
        validate_id(scope_id, "agent")
        return
    if scope is MemoryScope.WORKSPACE:
        validate_id(scope_id, "project")
        return
    _require_nonblank(scope_id, "scope_id")


def _validate_sha256(value: str) -> None:
    if len(value) != 64:
        raise ValueError("sha256 must contain 64 hexadecimal characters")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError("sha256 must be hexadecimal") from exc


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _require_nonblank(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
