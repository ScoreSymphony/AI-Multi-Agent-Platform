"""File, Artifact and Result-backed canonical Context source projection."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from ai_multi_agent_platform.agents.execution_profile import (
    decode_agent_execution_binding,
    decode_agent_step_execution_binding,
)
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data.contracts import FileProvider
from ai_multi_agent_platform.data.models import DataAccessContext, FileRecord
from ai_multi_agent_platform.kernel.repository import RunRepository, TaskRepository

from .models import (
    ContextCandidate,
    ContextDataClassification,
    ContextEntryRole,
    ContextSourceRef,
    ContextSourceType,
    ContextTrust,
)
from .resolver import ContextSourceRequest
from .source_adapter_normalization import (
    canonical_json,
    content_digest,
    normalize_classification,
    operational_request,
)

_MAX_INLINE_FILE_BYTES = 64 * 1024


class FileArtifactResultContextSourceAdapter:
    adapter_id = "platform.file-artifact-result-context/v1"

    def __init__(
        self,
        files: FileProvider,
        tasks: TaskRepository,
        runs: RunRepository,
        *,
        max_inline_file_bytes: int = _MAX_INLINE_FILE_BYTES,
    ) -> None:
        self.files = files
        self.tasks = tasks
        self.runs = runs
        self.max_inline_file_bytes = max_inline_file_bytes

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        operation, actor_ref = operational_request(request)
        task = await self.tasks.get_task(request.task_id)
        run = await self.runs.get_run(request.task_id, request.run_id)
        access = DataAccessContext(
            operation=operation,
            actor_ref=actor_ref,
            task_id=request.task_id,
            run_id=request.run_id,
            agent_id=request.agent_id,
        )
        refs = (
            set(task.artifact_ids)
            | set(task.result_ids)
            | set(run.artifact_ids)
            | set(run.result_ids)
        )
        binding = (
            decode_agent_step_execution_binding(task.task.metadata, request.step_id)
            if request.step_id is not None
            else None
        ) or decode_agent_execution_binding(task.task.metadata)
        if binding is not None:
            refs.update(binding.input_refs)
            refs.update(binding.output_refs)
        files = await self.files.list_files(access)
        candidates = await self._file_candidates(files, refs, access)
        candidates.extend(self._artifact_candidates(files, refs, request))
        candidates.extend(self._result_candidates(refs, request))
        return tuple(candidates)

    async def _file_candidates(
        self,
        files: Sequence[FileRecord],
        refs: set[str],
        access: DataAccessContext,
    ) -> list[ContextCandidate]:
        candidates: list[ContextCandidate] = []
        for file in sorted(files, key=lambda item: item.file_id):
            candidate = await self._file_candidate(file, refs, access)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    async def _file_candidate(
        self,
        file: FileRecord,
        refs: set[str],
        access: DataAccessContext,
    ) -> ContextCandidate | None:
        if file.file_id not in refs and not set(file.artifact_ids).intersection(refs):
            return None
        classification = normalize_classification(file.classification)
        if classification is ContextDataClassification.SECRET_REFERENCE:
            return None
        body = await self._file_body(file, access)
        return ContextCandidate(
            source=ContextSourceRef(
                ContextSourceType.FILE,
                file.file_id,
                digest=file.sha256,
            ),
            role=ContextEntryRole.CONTEXT,
            selection_reason="Run/Task-referenced canonical File content or bounded metadata",
            inline_content=body,
            content_digest=content_digest(body),
            trust=ContextTrust.UNTRUSTED,
            data_classification=classification,
            priority=35,
            relevance=0.75,
            project_id=file.project_id,
        )

    async def _file_body(self, file: FileRecord, access: DataAccessContext) -> str:
        if file.size_bytes > self.max_inline_file_bytes:
            return self._file_metadata_body(file, payload="large-file-not-inlined")
        raw = bytearray()
        async for chunk in self.files.stream_file(file.file_id, access):
            raw.extend(chunk)
            if len(raw) > self.max_inline_file_bytes:
                break
        try:
            return bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            return self._file_metadata_body(file, payload="binary-not-inlined")

    @staticmethod
    def _file_metadata_body(file: FileRecord, *, payload: str) -> str:
        return canonical_json(
            {
                "file_id": file.file_id,
                "sha256": file.sha256,
                "size_bytes": file.size_bytes,
                "content_type": file.content_type,
                "artifact_ids": list(file.artifact_ids),
                "payload": payload,
            }
        )

    @classmethod
    def _artifact_candidates(
        cls,
        files: Sequence[FileRecord],
        refs: set[str],
        request: ContextSourceRequest,
    ) -> list[ContextCandidate]:
        return [
            cls._artifact_candidate(artifact_id, files, request)
            for artifact_id in sorted(ref for ref in refs if ref.startswith("artifact_"))
        ]

    @staticmethod
    def _artifact_candidate(
        artifact_id: str,
        files: Sequence[FileRecord],
        request: ContextSourceRequest,
    ) -> ContextCandidate:
        linked_files = sorted(file.file_id for file in files if artifact_id in file.artifact_ids)
        body = canonical_json(
            {
                "artifact_id": artifact_id,
                "file_ids": cast(JsonValue, linked_files),
            }
        )
        digest = content_digest(body)
        return ContextCandidate(
            source=ContextSourceRef(ContextSourceType.ARTIFACT, artifact_id, digest=digest),
            role=ContextEntryRole.EVIDENCE,
            selection_reason="canonical Artifact reference linked to this Task/Run",
            inline_content=body,
            content_digest=digest,
            trust=ContextTrust.TRUSTED,
            data_classification=ContextDataClassification.INTERNAL,
            priority=30,
            relevance=0.65,
            project_id=request.project_id,
        )

    @classmethod
    def _result_candidates(
        cls,
        refs: set[str],
        request: ContextSourceRequest,
    ) -> list[ContextCandidate]:
        return [
            cls._result_candidate(result_id, request)
            for result_id in sorted(ref for ref in refs if ref.startswith("result_"))
        ]

    @staticmethod
    def _result_candidate(result_id: str, request: ContextSourceRequest) -> ContextCandidate:
        body = canonical_json({"result_id": result_id, "run_id": request.run_id})
        digest = content_digest(body)
        return ContextCandidate(
            source=ContextSourceRef(ContextSourceType.RESULT, result_id, digest=digest),
            role=ContextEntryRole.EVIDENCE,
            selection_reason="canonical Result reference linked to this Task/Run",
            inline_content=body,
            content_digest=digest,
            trust=ContextTrust.TRUSTED,
            data_classification=ContextDataClassification.INTERNAL,
            priority=30,
            relevance=0.65,
            project_id=request.project_id,
        )
