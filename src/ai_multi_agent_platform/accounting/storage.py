"""Progressive #13 FileProvider storage measurements for Issue #76."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.data import DataAccessContext, FileProvider, FileState

from .models import (
    AggregationMode,
    MeasurementQuality,
    UsageRecord,
    UsageScope,
    utc_now,
)
from .service import AccountingService

FILE_STORAGE_METRIC = "storage.file.bytes.current"


class FileStorageAccounting:
    """Reconcile current durable FileProvider bytes into a project-level physical gauge.

    Workspace snapshots reference canonical File records and can share the same physical
    bytes. This reconciler therefore refuses Workspace attribution; #171 exposes separate
    logical Workspace/Snapshot footprint metrics instead of duplicating physical storage.
    """

    def __init__(self, accounting: AccountingService, provider: FileProvider) -> None:
        self._accounting = accounting
        self._provider = provider

    async def reconcile(
        self,
        context: DataAccessContext,
        *,
        scope: UsageScope | None = None,
        observed_at: datetime | None = None,
        quality: MeasurementQuality = MeasurementQuality.REPORTED,
    ) -> UsageRecord:
        if quality not in {MeasurementQuality.MEASURED, MeasurementQuality.REPORTED}:
            raise ValueError("FileProvider storage must be measured or provider-reported")
        resolved_scope = _storage_scope(context, scope)
        provider_id = self._provider.descriptor.provider_id
        timestamp = observed_at or utc_now()
        try:
            files = await self._provider.list_files(context)
        except ContractError:
            self._accounting.record_unavailable(
                metric_type=FILE_STORAGE_METRIC,
                unit="bytes",
                source="file-provider",
                scope=resolved_scope,
                provider=provider_id,
                correlation_id=context.operation.correlation_id,
                causation_id=context.operation.causation_id,
                aggregation_mode=AggregationMode.LATEST,
            )
            raise

        for record in files:
            if record.project_id != context.project_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "FileProvider returned a record outside the requested project scope",
                )
        ready = tuple(record for record in files if record.state is FileState.READY)
        quantity = float(sum(record.size_bytes for record in ready))
        identity = json.dumps(
            {
                "provider_id": provider_id,
                "scope": resolved_scope.fields(),
                "observed_at": timestamp.isoformat(),
                "files": [
                    [record.file_id, record.size_bytes, record.sha256, record.state.value]
                    for record in sorted(ready, key=lambda item: item.file_id)
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        usage = UsageRecord(
            id=f"usage_{uuid5(NAMESPACE_URL, identity)}",
            metric_type=FILE_STORAGE_METRIC,
            unit="bytes",
            quality=quality,
            source="file-provider",
            quantity=quantity,
            scope=resolved_scope,
            aggregation_mode=AggregationMode.LATEST,
            timestamp=timestamp,
            provider=provider_id,
            correlation_id=context.operation.correlation_id,
            causation_id=context.operation.causation_id,
            provenance={
                "provider_type": self._provider.descriptor.provider_type,
                "ready_file_count": len(ready),
                "listed_file_count": len(files),
                "storage_semantics": "physical_project_bytes",
            },
        )
        self._accounting.record(usage)
        return usage


def _storage_scope(context: DataAccessContext, scope: UsageScope | None) -> UsageScope:
    resolved = scope or UsageScope()
    if resolved.workspace_id is not None:
        raise ValueError(
            "FileProvider physical storage cannot be attributed to a Workspace; "
            "use logical Workspace/Snapshot accounting instead"
        )
    if resolved.project_id is not None and resolved.project_id != context.project_id:
        raise ValueError("storage accounting scope must match the FileProvider project scope")
    if context.project_id is None:
        if resolved.project_id is not None:
            raise ValueError("unscoped FileProvider context cannot claim project usage")
        return resolved
    if resolved.project_id is None:
        return replace(resolved, project_id=context.project_id)
    return resolved
