"""Policy-controlled file/artifact export boundary."""

from __future__ import annotations

import hashlib

from ai_multi_agent_platform.contracts import (
    ContractError,
    DataClassification,
    EgressRequest,
    EgressTarget,
    EgressTargetKind,
    ErrorCode,
    strongest_classification,
)
from ai_multi_agent_platform.security.egress import EgressGate

from .contracts import FileProvider
from .models import DataAccessContext, FileRecord


class FileEgressExporter:
    """Read canonical file bytes only after their outbound target passes egress policy."""

    def __init__(self, provider: FileProvider, *, egress_gate: EgressGate | None = None) -> None:
        self.provider = provider
        self.egress_gate = egress_gate or EgressGate()

    async def export(
        self,
        file_id: str,
        context: DataAccessContext,
        *,
        target: EgressTarget,
    ) -> bytes:
        if target.kind not in {EgressTargetKind.FILE_EXPORT, EgressTargetKind.ARTIFACT_EXPORT}:
            raise ValueError("file export requires target kind file_export or artifact_export")
        record = await self.provider.get_file(file_id, context)
        classification = _file_classification(record, context)
        await self.egress_gate.enforce(
            EgressRequest(
                request_id=f"file:{record.file_id}:{target.target_id}",
                target=target,
                context=context.operation,
                classification=classification,
                resource_type=(
                    "artifact_export"
                    if target.kind is EgressTargetKind.ARTIFACT_EXPORT
                    else "file_export"
                ),
                payload_digest=record.sha256,
                task_id=context.task_id,
                run_id=context.run_id,
                policy_descriptors={
                    "file_id": record.file_id,
                    "artifact_count": len(record.artifact_ids),
                    "size_bytes": record.size_bytes,
                },
            )
        )
        data = bytearray()
        async for chunk in self.provider.stream_file(file_id, context):
            data.extend(chunk)
        exported = bytes(data)
        if hashlib.sha256(exported).hexdigest() != record.sha256:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "file content changed after egress policy evaluation",
                details={"file_id": record.file_id},
            )
        return exported


def _file_classification(
    record: FileRecord,
    context: DataAccessContext,
) -> DataClassification | None:
    values: list[DataClassification | None] = []
    if context.classification is not None:
        values.append(_parse_classification(context.classification, "data access context"))
    if record.classification is not None:
        values.append(_parse_classification(record.classification, "file record"))
    raw = record.metadata.get("data_classification")
    if raw is not None:
        if not isinstance(raw, str):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "file data_classification metadata must be a string",
                details={"file_id": record.file_id},
            )
        values.append(_parse_classification(raw, "file metadata"))
    return strongest_classification(*values)


def _parse_classification(value: DataClassification | str, source: str) -> DataClassification:
    try:
        return DataClassification(value)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            f"unknown data classification from {source}",
        ) from exc
