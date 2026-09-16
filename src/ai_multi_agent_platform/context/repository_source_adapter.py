"""Repository-backed canonical Context source projection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.kernel.repository import TaskRepository
from ai_multi_agent_platform.repositories.async_provenance import (
    AsyncRepositoryProvenanceReader,
    RepositoryProvenanceReader,
    as_async_repository_provenance_reader,
)
from ai_multi_agent_platform.repositories.models import RepositoryRunProvenance
from ai_multi_agent_platform.repositories.service import RepositoryCallContext, RepositoryService

from .models import (
    ContextCandidate,
    ContextDataClassification,
    ContextEntryRole,
    ContextSourceRef,
    ContextSourceType,
    ContextTrust,
)
from .resolver import ContextSourceRequest
from .source_adapter_normalization import canonical_json, content_digest, operational_request

_MAX_REPOSITORY_SLICE_LINES = 500
_MAX_REPOSITORY_TREE_ENTRIES = 5000
_MAX_REPOSITORY_TREE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _RepositorySliceSpec:
    repository_id: str
    path: str
    start_line: int
    end_line: int
    revision: str


class RepositoryContextSourceAdapter:
    adapter_id = "platform.repository-context/v1"

    def __init__(
        self,
        provenance: RepositoryProvenanceReader | AsyncRepositoryProvenanceReader,
        *,
        repositories: RepositoryService | None = None,
        tasks: TaskRepository | None = None,
    ) -> None:
        self.provenance = as_async_repository_provenance_reader(provenance)
        self.repositories = repositories
        self.tasks = tasks

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        provenance = tuple(
            sorted(
                await self.provenance.for_run(request.run_id),
                key=lambda item: item.repository_id,
            )
        )
        candidates = [self._provenance_candidate(request, record) for record in provenance]
        repositories = self.repositories
        tasks = self.tasks
        if repositories is None or tasks is None:
            return tuple(candidates)
        state = await tasks.get_task(request.task_id)
        raw_slices = state.task.metadata.get("context.repository_slices")
        if not isinstance(raw_slices, tuple | list):
            return tuple(candidates)
        operation, actor_ref = operational_request(request)
        by_repository = {record.repository_id: record for record in provenance}
        for raw in raw_slices:
            spec = self._slice_spec(raw, by_repository)
            candidates.append(
                await self._slice_candidate(
                    repositories,
                    request,
                    spec,
                    operation=operation,
                    actor_ref=actor_ref,
                )
            )
        return tuple(candidates)

    @staticmethod
    def _provenance_candidate(
        request: ContextSourceRequest,
        record: RepositoryRunProvenance,
    ) -> ContextCandidate:
        content = _provenance_content(record)
        digest = content_digest(content)
        return ContextCandidate(
            source=ContextSourceRef(
                ContextSourceType.REPOSITORY,
                record.repository_id,
                revision=record.input_revision,
                digest=digest,
            ),
            role=ContextEntryRole.CONTEXT,
            selection_reason="exact repository Run input provenance",
            inline_content=content,
            content_digest=digest,
            trust=ContextTrust.TRUSTED,
            data_classification=ContextDataClassification.INTERNAL,
            priority=60,
            relevance=0.7,
            project_id=request.project_id,
        )

    @classmethod
    def _slice_spec(
        cls,
        raw: JsonValue,
        by_repository: Mapping[str, RepositoryRunProvenance],
    ) -> _RepositorySliceSpec:
        if not isinstance(raw, Mapping):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "context.repository_slices entries must be objects",
            )
        values = cast(Mapping[str, JsonValue], raw)
        repository_id, path = cls._slice_identity(values)
        start_line, end_line = cls._slice_bounds(values)
        revision = cls._slice_revision(values, by_repository, repository_id)
        return _RepositorySliceSpec(repository_id, path, start_line, end_line, revision)

    @staticmethod
    def _slice_identity(raw: Mapping[str, JsonValue]) -> tuple[str, str]:
        repository_id = raw.get("repository_id")
        path = raw.get("path")
        if not isinstance(repository_id, str) or not repository_id.strip():
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "repository slice requires repository_id",
            )
        if not isinstance(path, str) or not path.strip():
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "repository slice requires path",
            )
        return repository_id, path

    @staticmethod
    def _slice_bounds(raw: Mapping[str, JsonValue]) -> tuple[int, int]:
        start_line = raw.get("start_line", 1)
        end_line = raw.get(
            "end_line",
            start_line + 199 if isinstance(start_line, int) else 200,
        )
        if isinstance(start_line, bool) or not isinstance(start_line, int) or start_line < 1:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "repository slice start_line invalid",
            )
        if isinstance(end_line, bool) or not isinstance(end_line, int) or end_line < start_line:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "repository slice end_line invalid",
            )
        if end_line - start_line + 1 > _MAX_REPOSITORY_SLICE_LINES:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "repository slice exceeds line limit",
            )
        return start_line, end_line

    @staticmethod
    def _slice_revision(
        raw: Mapping[str, JsonValue],
        by_repository: Mapping[str, RepositoryRunProvenance],
        repository_id: str,
    ) -> str:
        explicit_revision = raw.get("revision")
        if explicit_revision is not None:
            if not isinstance(explicit_revision, str) or not explicit_revision.strip():
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "repository slice revision invalid",
                )
            return explicit_revision
        bound = by_repository.get(repository_id)
        if bound is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "repository slice has no exact Run provenance or explicit immutable revision",
            )
        return bound.input_revision

    async def _slice_candidate(
        self,
        repositories: RepositoryService,
        request: ContextSourceRequest,
        spec: _RepositorySliceSpec,
        *,
        operation: OperationContext,
        actor_ref: str,
    ) -> ContextCandidate:
        selected, resolved_revision = await self._read_slice(
            repositories,
            request,
            spec,
            operation=operation,
            actor_ref=actor_ref,
        )
        digest = content_digest(selected)
        return ContextCandidate(
            source=ContextSourceRef(
                ContextSourceType.REPOSITORY,
                spec.repository_id,
                revision=resolved_revision,
                digest=digest,
                locator=f"{spec.path}:{spec.start_line}-{spec.end_line}",
            ),
            role=ContextEntryRole.CONTEXT,
            selection_reason="explicit exact Repository Intelligence source slice",
            inline_content=selected,
            content_digest=digest,
            trust=ContextTrust.UNTRUSTED,
            data_classification=ContextDataClassification.INTERNAL,
            priority=55,
            relevance=0.9,
            project_id=request.project_id,
        )

    @staticmethod
    async def _read_slice(
        repositories: RepositoryService,
        request: ContextSourceRequest,
        spec: _RepositorySliceSpec,
        *,
        operation: OperationContext,
        actor_ref: str,
    ) -> tuple[str, str]:
        tree = await repositories.read_tree(
            spec.repository_id,
            spec.revision,
            RepositoryCallContext(
                operation=operation,
                actor_ref=actor_ref,
                task_id=request.task_id,
                run_id=request.run_id,
                agent_id=request.agent_id,
            ),
            max_entries=_MAX_REPOSITORY_TREE_ENTRIES,
            max_total_bytes=_MAX_REPOSITORY_TREE_BYTES,
        )
        entry = next((item for item in tree.entries if item.relative_path == spec.path), None)
        if entry is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"repository source path not found: {spec.path}",
            )
        try:
            text = entry.data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "repository Context source slice must be UTF-8 text",
            ) from exc
        selected = "\n".join(text.splitlines()[spec.start_line - 1 : spec.end_line])
        if not selected:
            raise ContractError(ErrorCode.NOT_FOUND, "repository source slice is empty")
        return selected, tree.resolved_revision


def _provenance_content(record: RepositoryRunProvenance) -> str:
    return canonical_json(
        {
            "repository_id": record.repository_id,
            "input_revision": record.input_revision,
            "branch_ref": record.branch_ref,
            "output_revision": record.output_revision,
            "diff_artifact_ids": list(record.diff_artifact_ids),
            "provider_resource_ids": list(record.provider_resource_ids),
        }
    )
