"""#37 Workspace/Snapshot accounting without duplicating canonical physical storage."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data import DataAccessContext, FileProvider, FileState
from ai_multi_agent_platform.workspaces.models import Workspace, WorkspaceSnapshot, WorkspaceStatus

from .models import AggregationMode, MeasurementQuality, UsageRecord, UsageScope, utc_now
from .service import AccountingService

WORKSPACE_LOGICAL_BYTES_METRIC = "workspace.snapshot.logical_bytes.current"
WORKSPACE_FILE_REFERENCES_METRIC = "workspace.snapshot.file_references.current"


class WorkspaceSnapshotAccounting:
    """Measure the logical footprint of the current canonical Workspace snapshot.

    Logical bytes count every path reference in the snapshot. They are deliberately not
    physical/deduplicated storage bytes: snapshots may share canonical File IDs and the
    #13 FileProvider remains the sole source for project-level physical storage accounting.
    """

    def __init__(self, accounting: AccountingService, files: FileProvider) -> None:
        self._accounting = accounting
        self._files = files

    async def reconcile(
        self,
        workspace: Workspace,
        snapshot: WorkspaceSnapshot,
        context: DataAccessContext,
        *,
        observed_at: datetime | None = None,
    ) -> tuple[UsageRecord, UsageRecord]:
        self._validate_scope(workspace, snapshot, context)
        if workspace.status is WorkspaceStatus.DELETED:
            raise ValueError("deleted Workspaces must be retired instead of snapshot-reconciled")

        timestamp = observed_at or utc_now()
        scope = _workspace_scope(workspace)
        reference_record = self._record(
            metric_type=WORKSPACE_FILE_REFERENCES_METRIC,
            unit="count",
            quantity=float(len(snapshot.files)),
            quality=MeasurementQuality.MEASURED,
            scope=scope,
            snapshot=snapshot,
            timestamp=timestamp,
            provenance={
                "workspace_status": workspace.status.value,
                "logical_semantics": "snapshot_path_references",
            },
        )

        logical_bytes = 0
        unique_file_ids: set[str] = set()
        try:
            for reference in snapshot.files:
                file_record = await self._files.get_file(reference.file_id, context)
                if file_record.project_id != workspace.project_id:
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "Workspace snapshot FileRecord belongs to another Project",
                    )
                if file_record.state is not FileState.READY:
                    raise ContractError(
                        ErrorCode.UNAVAILABLE,
                        "Workspace snapshot references a non-ready FileRecord",
                    )
                if file_record.sha256 != reference.sha256:
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "Workspace snapshot checksum no longer matches its canonical FileRecord",
                    )
                logical_bytes += file_record.size_bytes
                unique_file_ids.add(file_record.file_id)
        except ContractError:
            unavailable = self._accounting.record_unavailable(
                metric_type=WORKSPACE_LOGICAL_BYTES_METRIC,
                unit="bytes",
                source="workspace-snapshot",
                scope=scope,
                provider=self._files.descriptor.provider_id,
                correlation_id=context.operation.correlation_id,
                causation_id=context.operation.causation_id,
                aggregation_mode=AggregationMode.LATEST,
            )
            raise WorkspaceSnapshotMeasurementError(reference_record, unavailable) from None

        bytes_record = self._record(
            metric_type=WORKSPACE_LOGICAL_BYTES_METRIC,
            unit="bytes",
            quantity=float(logical_bytes),
            quality=MeasurementQuality.REPORTED,
            scope=scope,
            snapshot=snapshot,
            timestamp=timestamp,
            provider=self._files.descriptor.provider_id,
            provenance={
                "workspace_status": workspace.status.value,
                "logical_semantics": "sum_per_snapshot_path_reference",
                "referenced_file_count": len(snapshot.files),
                "unique_file_count": len(unique_file_ids),
                "physical_storage_metric": "storage.file.bytes.current",
                "physical_storage_counted_here": False,
            },
        )
        return reference_record, bytes_record

    def retire(
        self,
        workspace: Workspace,
        *,
        observed_at: datetime | None = None,
    ) -> tuple[UsageRecord, UsageRecord]:
        """Zero current logical gauges only after canonical Workspace deletion.

        Archiving intentionally does not erase the last logical footprint. Deletion/cleanup
        is the lifecycle transition that retires current Workspace accounting state.
        """

        if workspace.status is not WorkspaceStatus.DELETED:
            raise ValueError("only a deleted Workspace can retire current snapshot gauges")
        timestamp = observed_at or utc_now()
        scope = _workspace_scope(workspace)
        common: dict[str, JsonValue] = {
            "workspace_status": workspace.status.value,
            "lifecycle_transition": "deleted",
        }
        references = self._record(
            metric_type=WORKSPACE_FILE_REFERENCES_METRIC,
            unit="count",
            quantity=0.0,
            quality=MeasurementQuality.MEASURED,
            scope=scope,
            snapshot=None,
            timestamp=timestamp,
            provenance=common,
        )
        logical_bytes = self._record(
            metric_type=WORKSPACE_LOGICAL_BYTES_METRIC,
            unit="bytes",
            quantity=0.0,
            quality=MeasurementQuality.MEASURED,
            scope=scope,
            snapshot=None,
            timestamp=timestamp,
            provenance=common,
        )
        return references, logical_bytes

    def _record(
        self,
        *,
        metric_type: str,
        unit: str,
        quantity: float,
        quality: MeasurementQuality,
        scope: UsageScope,
        snapshot: WorkspaceSnapshot | None,
        timestamp: datetime,
        provenance: dict[str, JsonValue],
        provider: str | None = None,
    ) -> UsageRecord:
        identity = json.dumps(
            {
                "metric_type": metric_type,
                "scope": scope.fields(),
                "snapshot_id": None if snapshot is None else snapshot.id,
                "snapshot_revision": None if snapshot is None else snapshot.revision,
                "timestamp": timestamp.isoformat(),
                "quantity": quantity,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        effective_provenance = dict(provenance)
        if snapshot is not None:
            effective_provenance.update(
                {
                    "snapshot_id": snapshot.id,
                    "snapshot_revision": snapshot.revision,
                    "snapshot_content_checksum": snapshot.content_checksum,
                }
            )
        record = UsageRecord(
            id=f"usage_{uuid5(NAMESPACE_URL, identity)}",
            metric_type=metric_type,
            unit=unit,
            quantity=quantity,
            quality=quality,
            source="workspace-snapshot",
            scope=scope,
            aggregation_mode=AggregationMode.LATEST,
            timestamp=timestamp,
            provider=provider,
            provenance=effective_provenance,
        )
        self._accounting.record(record)
        return record

    @staticmethod
    def _validate_scope(
        workspace: Workspace,
        snapshot: WorkspaceSnapshot,
        context: DataAccessContext,
    ) -> None:
        if snapshot.workspace_id != workspace.id:
            raise ValueError("WorkspaceSnapshot does not belong to the supplied Workspace")
        if context.project_id != workspace.project_id:
            raise ValueError("Workspace accounting context must match the Workspace Project")


class WorkspaceSnapshotMeasurementError(RuntimeError):
    """A logical-byte measurement failed after the exact reference count was recorded."""

    def __init__(self, references: UsageRecord, logical_bytes: UsageRecord) -> None:
        super().__init__("Workspace snapshot logical bytes are unavailable")
        self.references = references
        self.logical_bytes = logical_bytes


def _workspace_scope(workspace: Workspace) -> UsageScope:
    return UsageScope(
        project_id=workspace.project_id,
        workspace_id=workspace.id,
        owner_type=workspace.owner_ref.type,
        owner_id=workspace.owner_ref.id,
    )
