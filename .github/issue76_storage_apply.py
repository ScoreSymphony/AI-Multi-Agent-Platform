from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text()
    if text.count(old) != 1:
        raise RuntimeError(f"expected exactly one match in {path}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1))


models = "src/ai_multi_agent_platform/accounting/models.py"
replace_once(
    models,
    "class MeasurementQuality(StrEnum):\n",
    'class AggregationMode(StrEnum):\n    """How repeated records of one canonical metric combine."""\n\n    ADDITIVE = "additive"\n    LATEST = "latest"\n\n\nclass MeasurementQuality(StrEnum):\n',
)
replace_once(
    models,
    '    scope: UsageScope = field(default_factory=UsageScope)\n    id: str = field(default_factory=lambda: _new_id("usage"))\n',
    '    scope: UsageScope = field(default_factory=UsageScope)\n    aggregation_mode: AggregationMode = AggregationMode.ADDITIVE\n    id: str = field(default_factory=lambda: _new_id("usage"))\n',
)
replace_once(
    models,
    "    quality_counts: dict[MeasurementQuality, int]\n    start: datetime | None = None\n",
    "    quality_counts: dict[MeasurementQuality, int]\n    aggregation_mode: AggregationMode = AggregationMode.ADDITIVE\n    start: datetime | None = None\n",
)

store = "src/ai_multi_agent_platform/accounting/store.py"
replace_once(
    store,
    "from .models import (\n    BudgetAction,\n",
    "from .models import (\n    AggregationMode,\n    BudgetAction,\n",
)
replace_once(
    store,
    '    payload["quality"] = record.quality.value\n    payload["timestamp"] = record.timestamp.isoformat()\n',
    '    payload["quality"] = record.quality.value\n    payload["aggregation_mode"] = record.aggregation_mode.value\n    payload["timestamp"] = record.timestamp.isoformat()\n',
)
replace_once(
    store,
    '    quality = MeasurementQuality(str(payload.pop("quality")))\n    timestamp = datetime.fromisoformat(str(payload.pop("timestamp")))\n',
    '    quality = MeasurementQuality(str(payload.pop("quality")))\n    aggregation_mode = AggregationMode(\n        str(payload.pop("aggregation_mode", AggregationMode.ADDITIVE.value))\n    )\n    timestamp = datetime.fromisoformat(str(payload.pop("timestamp")))\n',
)
replace_once(
    store,
    "        quality=quality,\n        timestamp=timestamp,\n",
    "        quality=quality,\n        aggregation_mode=aggregation_mode,\n        timestamp=timestamp,\n",
)

service = "src/ai_multi_agent_platform/accounting/service.py"
replace_once(service, "from datetime import timedelta\n", "from datetime import datetime, timedelta\n")
replace_once(
    service,
    "from .models import (\n    BudgetState,\n",
    "from .models import (\n    AggregationMode,\n    BudgetState,\n",
)
replace_once(
    service,
    "        causation_id: str | None = None,\n    ) -> UsageRecord:\n",
    "        causation_id: str | None = None,\n        aggregation_mode: AggregationMode = AggregationMode.ADDITIVE,\n    ) -> UsageRecord:\n",
)
replace_once(
    service,
    "            causation_id=causation_id,\n        )\n",
    "            causation_id=causation_id,\n            aggregation_mode=aggregation_mode,\n        )\n",
)
replace_once(
    service,
    '''    def aggregate(self, query: UsageQuery) -> UsageAggregate:\n        records = self.store.query(query)\n        if query.metric_type is None or query.unit is None:\n            raise ValueError("aggregate query requires metric_type and unit")\n        values = [record.quantity for record in records if record.quantity is not None]\n        quality_counts = {quality: 0 for quality in MeasurementQuality}\n        for record in records:\n            quality_counts[record.quality] += 1\n        return UsageAggregate(\n            metric_type=query.metric_type,\n            unit=query.unit,\n            total=sum(values) if values else None,\n            record_count=len(records),\n            unavailable_count=quality_counts[MeasurementQuality.UNAVAILABLE],\n            quality_counts=quality_counts,\n            start=query.start,\n            end=query.end,\n        )\n''',
    '''    def aggregate(self, query: UsageQuery) -> UsageAggregate:\n        records = self.store.query(query)\n        if query.metric_type is None or query.unit is None:\n            raise ValueError("aggregate query requires metric_type and unit")\n        return aggregate_usage_records(\n            records,\n            metric_type=query.metric_type,\n            unit=query.unit,\n            start=query.start,\n            end=query.end,\n        )\n''',
)
replace_once(
    service,
    '''        records = self.store.query(query)\n        consumed = 0.0\n        for record in records:\n            if record.quantity is None:\n                continue\n            if record.quality is MeasurementQuality.ESTIMATED and not budget.include_estimated:\n                continue\n            consumed += record.quantity\n        fraction = consumed / budget.limit\n''',
    '''        records = tuple(\n            record\n            for record in self.store.query(query)\n            if record.quantity is not None\n            and (record.quality is not MeasurementQuality.ESTIMATED or budget.include_estimated)\n        )\n        consumed = _budget_quantity(records)\n        fraction = consumed / budget.limit\n''',
)
marker = "\n\ndef usage_from_metric(metric: MetricRecord) -> UsageRecord | None:\n"
helper = '''\n\ndef aggregate_usage_records(\n    records: tuple[UsageRecord, ...],\n    *,\n    metric_type: str,\n    unit: str,\n    start: datetime | None = None,\n    end: datetime | None = None,\n) -> UsageAggregate:\n    """Aggregate one canonical metric without summing point-in-time gauges."""\n\n    modes = {record.aggregation_mode for record in records}\n    if len(modes) > 1:\n        raise ValueError("one metric/unit query cannot mix aggregation modes")\n    mode = next(iter(modes), AggregationMode.ADDITIVE)\n    quality_counts = {quality: 0 for quality in MeasurementQuality}\n    for record in records:\n        quality_counts[record.quality] += 1\n\n    if mode is AggregationMode.LATEST and records:\n        latest = max(records, key=lambda record: (record.timestamp, record.id))\n        total = latest.quantity\n    else:\n        values = [record.quantity for record in records if record.quantity is not None]\n        total = sum(values) if values else None\n\n    return UsageAggregate(\n        metric_type=metric_type,\n        unit=unit,\n        total=total,\n        record_count=len(records),\n        unavailable_count=quality_counts[MeasurementQuality.UNAVAILABLE],\n        quality_counts=quality_counts,\n        aggregation_mode=mode,\n        start=start,\n        end=end,\n    )\n\n\ndef _budget_quantity(records: tuple[UsageRecord, ...]) -> float:\n    if not records:\n        return 0.0\n    modes = {record.aggregation_mode for record in records}\n    if len(modes) > 1:\n        raise ValueError("budget cannot mix aggregation modes for one metric/unit")\n    mode = next(iter(modes))\n    if mode is AggregationMode.LATEST:\n        latest = max(records, key=lambda record: (record.timestamp, record.id))\n        assert latest.quantity is not None\n        return latest.quantity\n    total = 0.0\n    for record in records:\n        assert record.quantity is not None\n        total += record.quantity\n    return total\n'''
replace_once(service, marker, helper + marker)

control = "src/ai_multi_agent_platform/accounting/control_plane.py"
replace_once(
    control,
    "from .service import AccountingService\n",
    "from .service import AccountingService, aggregate_usage_records\n",
)
replace_once(
    control,
    '''def _aggregate_visible(\n    records: tuple[UsageRecord, ...], metric_type: str, unit: str\n) -> UsageAggregate:\n    selected = tuple(\n        record for record in records if record.metric_type == metric_type and record.unit == unit\n    )\n    values = [record.quantity for record in selected if record.quantity is not None]\n    quality_counts = {quality: 0 for quality in MeasurementQuality}\n    for record in selected:\n        quality_counts[record.quality] += 1\n    return UsageAggregate(\n        metric_type=metric_type,\n        unit=unit,\n        total=sum(values) if values else None,\n        record_count=len(selected),\n        unavailable_count=quality_counts[MeasurementQuality.UNAVAILABLE],\n        quality_counts=quality_counts,\n    )\n''',
    '''def _aggregate_visible(\n    records: tuple[UsageRecord, ...], metric_type: str, unit: str\n) -> UsageAggregate:\n    selected = tuple(\n        record for record in records if record.metric_type == metric_type and record.unit == unit\n    )\n    return aggregate_usage_records(selected, metric_type=metric_type, unit=unit)\n''',
)
replace_once(
    control,
    '        "quality_counts": quality_counts,\n    }\n',
    '        "quality_counts": quality_counts,\n        "aggregation_mode": aggregate.aggregation_mode.value,\n    }\n',
)
replace_once(control, "    MeasurementQuality,\n", "")

init = "src/ai_multi_agent_platform/accounting/__init__.py"
replace_once(
    init,
    "from .models import (\n    BudgetAction,\n",
    "from .models import (\n    AggregationMode,\n    BudgetAction,\n",
)
replace_once(
    init,
    "from .service import AccountingService, ThresholdEventSink, usage_from_metric\n",
    "from .service import (\n    AccountingService,\n    ThresholdEventSink,\n    aggregate_usage_records,\n    usage_from_metric,\n)\nfrom .storage import FileStorageAccounting\n",
)
replace_once(
    init,
    '__all__ = [\n    "AccountingService",\n',
    '__all__ = [\n    "AccountingService",\n    "AggregationMode",\n',
)
replace_once(
    init,
    '    "BudgetThresholdEvent",\n',
    '    "BudgetThresholdEvent",\n    "FileStorageAccounting",\n',
)
replace_once(
    init,
    '    "accounting_resource_services",\n',
    '    "accounting_resource_services",\n    "aggregate_usage_records",\n',
)

Path("src/ai_multi_agent_platform/accounting/storage.py").write_text('''"""Progressive #13 FileProvider storage measurements for Issue #76."""\n\nfrom __future__ import annotations\n\nimport json\nfrom dataclasses import replace\nfrom datetime import datetime\nfrom uuid import NAMESPACE_URL, uuid5\n\nfrom ai_multi_agent_platform.contracts import ContractError, ErrorCode\nfrom ai_multi_agent_platform.data import DataAccessContext, FileProvider, FileState\n\nfrom .models import (\n    AggregationMode,\n    MeasurementQuality,\n    UsageRecord,\n    UsageScope,\n    utc_now,\n)\nfrom .service import AccountingService\n\nFILE_STORAGE_METRIC = "storage.file.bytes.current"\n\n\nclass FileStorageAccounting:\n    """Reconcile current durable FileProvider bytes into a point-in-time gauge."""\n\n    def __init__(self, accounting: AccountingService, provider: FileProvider) -> None:\n        self._accounting = accounting\n        self._provider = provider\n\n    async def reconcile(\n        self,\n        context: DataAccessContext,\n        *,\n        scope: UsageScope | None = None,\n        observed_at: datetime | None = None,\n        quality: MeasurementQuality = MeasurementQuality.REPORTED,\n    ) -> UsageRecord:\n        if quality not in {MeasurementQuality.MEASURED, MeasurementQuality.REPORTED}:\n            raise ValueError("FileProvider storage must be measured or provider-reported")\n        resolved_scope = _storage_scope(context, scope)\n        provider_id = self._provider.descriptor.provider_id\n        timestamp = observed_at or utc_now()\n        try:\n            files = await self._provider.list_files(context)\n        except ContractError:\n            self._accounting.record_unavailable(\n                metric_type=FILE_STORAGE_METRIC,\n                unit="bytes",\n                source="file-provider",\n                scope=resolved_scope,\n                provider=provider_id,\n                correlation_id=context.operation.correlation_id,\n                causation_id=context.operation.causation_id,\n                aggregation_mode=AggregationMode.LATEST,\n            )\n            raise\n\n        for record in files:\n            if record.project_id != context.project_id:\n                raise ContractError(\n                    ErrorCode.CONTRACT_VIOLATION,\n                    "FileProvider returned a record outside the requested project scope",\n                )\n        ready = tuple(record for record in files if record.state is FileState.READY)\n        quantity = float(sum(record.size_bytes for record in ready))\n        identity = json.dumps(\n            {\n                "provider_id": provider_id,\n                "scope": resolved_scope.fields(),\n                "observed_at": timestamp.isoformat(),\n                "files": [\n                    [record.file_id, record.size_bytes, record.sha256, record.state.value]\n                    for record in sorted(ready, key=lambda item: item.file_id)\n                ],\n            },\n            sort_keys=True,\n            separators=(",", ":"),\n        )\n        usage = UsageRecord(\n            id=f"usage_{uuid5(NAMESPACE_URL, identity)}",\n            metric_type=FILE_STORAGE_METRIC,\n            unit="bytes",\n            quality=quality,\n            source="file-provider",\n            quantity=quantity,\n            scope=resolved_scope,\n            aggregation_mode=AggregationMode.LATEST,\n            timestamp=timestamp,\n            provider=provider_id,\n            correlation_id=context.operation.correlation_id,\n            causation_id=context.operation.causation_id,\n            provenance={\n                "provider_type": self._provider.descriptor.provider_type,\n                "ready_file_count": len(ready),\n                "listed_file_count": len(files),\n            },\n        )\n        self._accounting.record(usage)\n        return usage\n\n\ndef _storage_scope(context: DataAccessContext, scope: UsageScope | None) -> UsageScope:\n    resolved = scope or UsageScope()\n    if resolved.project_id is not None and resolved.project_id != context.project_id:\n        raise ValueError("storage accounting scope must match the FileProvider project scope")\n    if context.project_id is None:\n        if resolved.project_id is not None:\n            raise ValueError("unscoped FileProvider context cannot claim project usage")\n        return resolved\n    if resolved.project_id is None:\n        return replace(resolved, project_id=context.project_id)\n    return resolved\n''')

Path("tests/test_issue76_storage_accounting.py").write_text('''from __future__ import annotations\n\nimport asyncio\nfrom datetime import UTC, datetime, timedelta\n\nimport pytest\n\nfrom ai_multi_agent_platform.accounting import (\n    AccountingService,\n    AggregationMode,\n    FileStorageAccounting,\n    InMemoryUsageStore,\n    MeasurementQuality,\n    SQLiteUsageStore,\n    ThresholdLevel,\n    UsageBudget,\n    UsageQuery,\n    UsageRecord,\n    UsageScope,\n)\nfrom ai_multi_agent_platform.contracts import OperationContext\nfrom ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider\nfrom ai_multi_agent_platform.domain import new_id\n\n\ndef _context(project_id: str) -> DataAccessContext:\n    operation = OperationContext(\n        correlation_id="storage-accounting",\n        owner_type="user",\n        owner_id="alice",\n        project_id=project_id,\n    )\n    return DataAccessContext(operation=operation, actor_ref="user:alice")\n\n\ndef test_current_file_storage_uses_latest_snapshot_not_sum(tmp_path) -> None:\n    project_id = new_id("project")\n    context = _context(project_id)\n    provider = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")\n    accounting = AccountingService(InMemoryUsageStore())\n    storage = FileStorageAccounting(accounting, provider)\n    first_file = asyncio.run(provider.create_file(b"a" * 10, context))\n    asyncio.run(provider.create_file(b"b" * 20, context))\n    first_time = datetime(2026, 9, 3, 2, 0, tzinfo=UTC)\n    first = asyncio.run(storage.reconcile(context, observed_at=first_time))\n    assert first.quantity == 30.0\n    assert first.aggregation_mode is AggregationMode.LATEST\n    assert first.quality is MeasurementQuality.REPORTED\n\n    asyncio.run(provider.delete_file(first_file.file_id, context))\n    second = asyncio.run(storage.reconcile(context, observed_at=first_time + timedelta(minutes=1)))\n    assert second.quantity == 20.0\n    aggregate = accounting.aggregate(\n        UsageQuery(\n            metric_type="storage.file.bytes.current",\n            unit="bytes",\n            scope=UsageScope(project_id=project_id),\n        )\n    )\n    assert aggregate.total == 20.0\n    assert aggregate.record_count == 2\n    assert aggregate.aggregation_mode is AggregationMode.LATEST\n\n\ndef test_latest_unavailable_measurement_is_not_fabricated_as_zero() -> None:\n    accounting = AccountingService(InMemoryUsageStore())\n    start = datetime(2026, 9, 3, 2, 0, tzinfo=UTC)\n    accounting.record(\n        UsageRecord(\n            metric_type="node.memory.bytes.current",\n            unit="bytes",\n            quality=MeasurementQuality.REPORTED,\n            source="node",\n            quantity=128.0,\n            aggregation_mode=AggregationMode.LATEST,\n            timestamp=start,\n        )\n    )\n    accounting.record(\n        UsageRecord(\n            metric_type="node.memory.bytes.current",\n            unit="bytes",\n            quality=MeasurementQuality.UNAVAILABLE,\n            source="node",\n            quantity=None,\n            aggregation_mode=AggregationMode.LATEST,\n            timestamp=start + timedelta(seconds=1),\n        )\n    )\n    aggregate = accounting.aggregate(UsageQuery(metric_type="node.memory.bytes.current", unit="bytes"))\n    assert aggregate.total is None\n    assert aggregate.unavailable_count == 1\n\n\ndef test_storage_budget_uses_current_value_and_can_recover_below_threshold(tmp_path) -> None:\n    project_id = new_id("project")\n    context = _context(project_id)\n    provider = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")\n    events = []\n    accounting = AccountingService(InMemoryUsageStore(), threshold_event_sink=events.append)\n    budget = UsageBudget(\n        metric_type="storage.file.bytes.current",\n        unit="bytes",\n        scope_type="project",\n        scope_id=project_id,\n        limit=30.0,\n    )\n    accounting.put_budget(budget)\n    storage = FileStorageAccounting(accounting, provider)\n    first = asyncio.run(provider.create_file(b"a" * 10, context))\n    asyncio.run(provider.create_file(b"b" * 20, context))\n    observed = datetime(2026, 9, 3, 2, 0, tzinfo=UTC)\n    asyncio.run(storage.reconcile(context, observed_at=observed))\n    assert [event.level for event in events] == [ThresholdLevel.EXCEEDED]\n\n    asyncio.run(provider.delete_file(first.file_id, context))\n    asyncio.run(storage.reconcile(context, observed_at=observed + timedelta(minutes=1)))\n    state = accounting.budget_state(budget.id)\n    assert state.consumed == 20.0\n    assert state.level is None\n\n\ndef test_storage_reconciliation_does_not_infer_usage_owner_from_measuring_actor(tmp_path) -> None:\n    project_id = new_id("project")\n    context = _context(project_id)\n    provider = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")\n    accounting = AccountingService(InMemoryUsageStore())\n    asyncio.run(provider.create_file(b"abc", context))\n    usage = asyncio.run(FileStorageAccounting(accounting, provider).reconcile(context))\n    assert usage.scope.project_id == project_id\n    assert usage.scope.owner_type is None\n    assert usage.scope.owner_id is None\n    assert usage.provenance["ready_file_count"] == 1\n\n\ndef test_storage_scope_can_be_explicitly_owner_attributed(tmp_path) -> None:\n    project_id = new_id("project")\n    context = _context(project_id)\n    provider = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")\n    accounting = AccountingService(InMemoryUsageStore())\n    usage = asyncio.run(\n        FileStorageAccounting(accounting, provider).reconcile(\n            context,\n            scope=UsageScope(project_id=project_id, owner_type="user", owner_id="alice"),\n        )\n    )\n    assert usage.quantity == 0.0\n    assert usage.scope.owner_id == "alice"\n\n\ndef test_storage_scope_cannot_claim_another_project(tmp_path) -> None:\n    project_id = new_id("project")\n    provider = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")\n    storage = FileStorageAccounting(AccountingService(InMemoryUsageStore()), provider)\n    with pytest.raises(ValueError):\n        asyncio.run(\n            storage.reconcile(\n                _context(project_id),\n                scope=UsageScope(project_id=new_id("project")),\n            )\n        )\n\n\ndef test_sqlite_round_trip_preserves_latest_aggregation_mode(tmp_path) -> None:\n    path = tmp_path / "usage.sqlite3"\n    service = AccountingService(SQLiteUsageStore(path))\n    record = UsageRecord(\n        metric_type="storage.file.bytes.current",\n        unit="bytes",\n        quality=MeasurementQuality.REPORTED,\n        source="file-provider",\n        quantity=12.0,\n        aggregation_mode=AggregationMode.LATEST,\n    )\n    service.record(record)\n    restarted = AccountingService(SQLiteUsageStore(path))\n    stored = restarted.query(UsageQuery(metric_type="storage.file.bytes.current", unit="bytes"))[0]\n    assert stored.aggregation_mode is AggregationMode.LATEST\n    assert (\n        restarted.aggregate(UsageQuery(metric_type="storage.file.bytes.current", unit="bytes")).total\n        == 12.0\n    )\n\n\ndef test_one_metric_unit_cannot_mix_additive_and_latest_semantics() -> None:\n    service = AccountingService(InMemoryUsageStore())\n    for mode in (AggregationMode.ADDITIVE, AggregationMode.LATEST):\n        service.record(\n            UsageRecord(\n                metric_type="test.mixed",\n                unit="count",\n                quality=MeasurementQuality.MEASURED,\n                source="test",\n                quantity=1.0,\n                aggregation_mode=mode,\n            )\n        )\n    with pytest.raises(ValueError, match="mix aggregation modes"):\n        service.aggregate(UsageQuery(metric_type="test.mixed", unit="count"))\n''')

accounting_doc = Path("docs/ACCOUNTING.md")
accounting_doc.write_text(
    accounting_doc.read_text()
    + '''\n\n## Point-in-time resources and storage\n\nRepeated accounting records now declare an aggregation mode. `additive` is used for counters and consumptive quantities such as calls, tokens and durations. `latest` is used for point-in-time state such as current storage bytes and, later, RAM/VRAM/utilization gauges. A canonical metric/unit query may not mix both modes.\n\n`FileStorageAccounting` consumes the completed #13 `FileProvider` boundary and records `storage.file.bytes.current` in bytes. It sums only READY canonical `FileRecord.size_bytes` values visible through the provider's scoped `list_files()` call. Tombstoned or pending files are not counted. The measurement is provider-reported by default because a replaceable FileProvider may obtain size metadata differently; callers may mark it measured only when their provider contract justifies that classification.\n\nStorage reconciliation does not infer usage ownership from `DataAccessContext.actor_ref`: the actor performing a measurement is not necessarily the owner of the measured resources. Project scope is inherited from the FileProvider request; user/team/organization ownership must be supplied explicitly when the caller actually knows it.\n\nProvider errors may record an unavailable latest measurement but are re-raised. An unavailable gauge therefore never becomes a fabricated zero. Budget evaluation retains its last available value until a new available gauge is reported, while aggregate/current-state queries expose a latest unavailable sample as unavailable.\n'''
)

Path("docs/ISSUE_76_STORAGE_PROGRESS.md").write_text('''# Issue #76 storage accounting progress\n\nThis progressive slice connects the completed #13 FileProvider contract to the #76 accounting foundation without making filesystem paths or one storage backend canonical.\n\n## Added\n\n- explicit `additive` versus `latest` aggregation semantics;\n- latest-value budget semantics for point-in-time gauges;\n- SQLite round-trip support for the aggregation mode;\n- `FileStorageAccounting` over canonical `FileRecord.size_bytes`;\n- current file-storage metric `storage.file.bytes.current` with unit `bytes`;\n- project-scope validation and explicit ownership attribution;\n- no actor-to-owner inference;\n- tombstone/removal decreases current storage rather than accumulating historical bytes;\n- unavailable latest values remain unavailable rather than zero.\n\n## Boundary\n\nThis does not yet account for WorkspaceSnapshot logical storage, Knowledge/index storage, network transfer, remote Worker/Node storage, or physical backend allocation/replication overhead. Those quantities require their owning domains to expose reliable semantics before #76 records them.\n\nIssue #76 remains open for the other progressive measurement sources and Resources UI.\n''')
