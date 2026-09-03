from pathlib import Path


def replace(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text()
    if old not in text:
        raise SystemExit(f"expected snippet missing in {path}: {old[:100]!r}")
    target.write_text(text.replace(old, new, 1))


models = "src/ai_multi_agent_platform/accounting/models.py"
replace(
    models,
    '        "capability",\n        "model_provider",',
    '        "capability",\n        "model_config",\n        "model_provider",',
)
replace(
    models,
    'class BudgetKind(StrEnum):\n    SOFT = "soft"\n    HARD = "hard"\n\n\nclass BudgetAction',
    'class BudgetKind(StrEnum):\n    SOFT = "soft"\n    HARD = "hard"\n\n\nclass BudgetWindowMode(StrEnum):\n    """How a budget consumption window advances over time."""\n\n    LIFETIME = "lifetime"\n    ROLLING = "rolling"\n\n\nclass BudgetAction',
)
replace(
    models,
    '        if self.version < 1:\n            raise ValueError("budget version must be >= 1")\n\n\n@dataclass(frozen=True, slots=True)\nclass BudgetState:\n    budget: UsageBudget\n    consumed: float\n    remaining: float\n    fraction: float\n    level: ThresholdLevel | None\n',
    '        if self.version < 1:\n            raise ValueError("budget version must be >= 1")\n\n    @property\n    def window_mode(self) -> BudgetWindowMode:\n        return (\n            BudgetWindowMode.LIFETIME\n            if self.window_seconds is None\n            else BudgetWindowMode.ROLLING\n        )\n\n\n@dataclass(frozen=True, slots=True)\nclass BudgetState:\n    budget: UsageBudget\n    consumed: float\n    remaining: float\n    fraction: float\n    level: ThresholdLevel | None\n    window_start: datetime | None = None\n    window_end: datetime | None = None\n',
)

init = "src/ai_multi_agent_platform/accounting/__init__.py"
replace(init, '    BudgetKind,\n    BudgetState,', '    BudgetKind,\n    BudgetState,\n    BudgetWindowMode,')
replace(init, '    "BudgetState",\n    "BudgetThresholdEvent",', '    "BudgetState",\n    "BudgetThresholdEvent",\n    "BudgetWindowMode",')

service = "src/ai_multi_agent_platform/accounting/service.py"
replace(
    service,
    '    def query(self, query: UsageQuery | None = None) -> tuple[UsageRecord, ...]:\n        return self.store.query(query or UsageQuery())\n',
    '''    def record_external_cost(
        self,
        *,
        amount: float,
        currency: str,
        source: str,
        quality: MeasurementQuality,
        scope: UsageScope | None = None,
        provider: str | None = None,
        confidence: float | None = None,
        provenance: dict[str, JsonValue] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> UsageRecord:
        """Record an explicitly supplied external monetary amount.

        Cost is never inferred from provider usage units or free-form model metadata.
        The ISO currency is also the canonical unit so unlike currencies cannot be
        silently aggregated.
        """

        if quality not in {MeasurementQuality.REPORTED, MeasurementQuality.ESTIMATED}:
            raise ValueError("external cost quality must be reported or estimated")
        canonical_currency = currency.upper()
        record = UsageRecord(
            metric_type="external.cost.amount",
            unit=canonical_currency,
            quality=quality,
            source=source,
            quantity=amount,
            scope=scope or UsageScope(),
            provider=provider,
            correlation_id=correlation_id,
            causation_id=causation_id,
            cost_amount=amount,
            currency=canonical_currency,
            confidence=confidence,
            provenance=dict(provenance or {}),
        )
        self.record(record)
        return record

    def query(self, query: UsageQuery | None = None) -> tuple[UsageRecord, ...]:
        return self.store.query(query or UsageQuery())
''',
)
replace(
    service,
    '            fraction=fraction,\n            level=level,\n        )',
    '            fraction=fraction,\n            level=level,\n            window_start=start,\n            window_end=end,\n        )',
)
replace(
    service,
    '    metric_type, unit, quality = mapping\n',
    '    metric_type, unit, quality = mapping\n    aggregation_mode = _aggregation_mode(metric)\n',
)
replace(
    service,
    '        quality=quality,\n        source="observability",',
    '        quality=quality,\n        aggregation_mode=aggregation_mode,\n        source="observability",',
)
replace(
    service,
    '    if metric.name == "platform.model.usage":\n',
    '''    if metric.name in {
        "platform.node.reported_resource",
        "platform.worker.reported_resource",
    }:
        resource_key = _normalized_metric_label(metric.attributes.get("resource_key"))
        if resource_key is None:
            return None
        subject = "node" if metric.name.startswith("platform.node.") else "worker"
        return (
            f"{subject}.provider_reported.{resource_key}",
            metric.unit,
            MeasurementQuality.REPORTED,
        )

    if metric.name == "platform.model.usage":
''',
)
replace(
    service,
    '        "platform.tool.duration_seconds": (\n            "capability.invocation.duration",\n            "seconds",\n            MeasurementQuality.MEASURED,\n        ),\n',
    '''        "platform.tool.duration_seconds": (
            "capability.invocation.duration",
            "seconds",
            MeasurementQuality.MEASURED,
        ),
        "platform.worker.dispatch.calls": (
            "worker.dispatch.count",
            "count",
            MeasurementQuality.MEASURED,
        ),
        "platform.worker.dispatch.duration_seconds": (
            "worker.dispatch.duration",
            "seconds",
            MeasurementQuality.MEASURED,
        ),
        "platform.worker.dispatch.failures": (
            "worker.dispatch.failure.count",
            "count",
            MeasurementQuality.MEASURED,
        ),
''',
)
replace(
    service,
    '\n\ndef _normalized_metric_label(value: object) -> str | None:\n',
    '''

def _aggregation_mode(metric: MetricRecord) -> AggregationMode:
    if metric.name in {
        "platform.node.reported_resource",
        "platform.worker.reported_resource",
    }:
        return AggregationMode.LATEST
    return AggregationMode.ADDITIVE


def _normalized_metric_label(value: object) -> str | None:
''',
)
replace(
    service,
    '        case "capability":\n            return UsageScope(capability_id=value)\n        case "model_provider":',
    '        case "capability":\n            return UsageScope(capability_id=value)\n        case "model_config":\n            return UsageScope(model_config_id=value)\n        case "model_provider":',
)
replace(
    service,
    '        "capability": scope.capability_id,\n        "model_provider": scope.model_provider_id,',
    '        "capability": scope.capability_id,\n        "model_config": scope.model_config_id,\n        "model_provider": scope.model_provider_id,',
)

control_plane = "src/ai_multi_agent_platform/accounting/control_plane.py"
replace(
    control_plane,
    'from .models import (\n    UsageAggregate,',
    'from .models import (\n    AggregationMode,\n    UsageAggregate,',
)
replace(
    control_plane,
    '''        pairs = sorted({(record.metric_type, record.unit) for record in records})
        resources: list[dict[str, JsonValue]] = []
        scope_key = _scope_key(context)
        trend_end = utc_now()
        trend_start = trend_end - timedelta(seconds=self._trend_window_seconds)
        for metric_type, unit in pairs:
            selected = _metric_records(records, metric_type, unit)
            aggregate = aggregate_usage_records(selected, metric_type=metric_type, unit=unit)
            trend = trend_usage_records(
                selected,
                metric_type=metric_type,
                unit=unit,
                start=trend_start,
                end=trend_end,
                bucket_seconds=self._trend_bucket_seconds,
            )
            resources.append(
                _aggregate_resource(
                    metric_type,
                    unit,
                    aggregate,
                    scope_key,
                    trend=trend,
                    trend_start=trend_start,
                    trend_end=trend_end,
                    trend_bucket_seconds=self._trend_bucket_seconds,
                )
            )
        return tuple(resources)
''',
    '''        pairs = sorted({(record.metric_type, record.unit) for record in records})
        resources: list[dict[str, JsonValue]] = []
        owner_scope_key = _scope_key(context)
        trend_end = utc_now()
        trend_start = trend_end - timedelta(seconds=self._trend_window_seconds)
        for metric_type, unit in pairs:
            selected = _metric_records(records, metric_type, unit)
            for aggregate_scope, scoped_records in _aggregate_groups(selected, context):
                aggregate = aggregate_usage_records(
                    scoped_records, metric_type=metric_type, unit=unit
                )
                trend = trend_usage_records(
                    scoped_records,
                    metric_type=metric_type,
                    unit=unit,
                    start=trend_start,
                    end=trend_end,
                    bucket_seconds=self._trend_bucket_seconds,
                )
                resources.append(
                    _aggregate_resource(
                        metric_type,
                        unit,
                        aggregate,
                        f"{owner_scope_key}|{_usage_scope_key(aggregate_scope)}",
                        aggregate_scope,
                        trend=trend,
                        trend_start=trend_start,
                        trend_end=trend_end,
                        trend_bucket_seconds=self._trend_bucket_seconds,
                    )
                )
        return tuple(resources)
''',
)
replace(
    control_plane,
    '''

def _aggregate_visible(
    records: tuple[UsageRecord, ...], metric_type: str, unit: str
) -> UsageAggregate:
    selected = _metric_records(records, metric_type, unit)
    return aggregate_usage_records(selected, metric_type=metric_type, unit=unit)
''',
    '''

def _aggregate_groups(
    records: tuple[UsageRecord, ...],
    context: RequestContext,
) -> tuple[tuple[UsageScope, tuple[UsageRecord, ...]], ...]:
    """Keep point-in-time gauges scoped to the resource they describe."""

    modes = {record.aggregation_mode for record in records}
    if len(modes) > 1:
        raise ValueError("one metric/unit aggregate cannot mix aggregation modes")
    if not records or next(iter(modes), AggregationMode.ADDITIVE) is AggregationMode.ADDITIVE:
        return ((_owner_query(context).scope, records),)

    grouped: dict[tuple[tuple[str, str], ...], list[UsageRecord]] = {}
    scopes: dict[tuple[tuple[str, str], ...], UsageScope] = {}
    for record in records:
        key = tuple(sorted(record.scope.fields().items()))
        grouped.setdefault(key, []).append(record)
        scopes[key] = record.scope
    return tuple((scopes[key], tuple(grouped[key])) for key in sorted(grouped))


def _usage_scope_key(scope: UsageScope) -> str:
    fields = scope.fields()
    if not fields:
        return "unscoped"
    return "|".join(f"{key}={fields[key]}" for key in sorted(fields))
''',
)
replace(
    control_plane,
    '    aggregate: UsageAggregate,\n    scope_key: str,\n    *,',
    '    aggregate: UsageAggregate,\n    scope_key: str,\n    scope: UsageScope,\n    *,',
)
replace(
    control_plane,
    '    quality_counts: dict[str, JsonValue] = {\n        quality.value: count for quality, count in aggregate.quality_counts.items()\n    }\n',
    '    quality_counts: dict[str, JsonValue] = {\n        quality.value: count for quality, count in aggregate.quality_counts.items()\n    }\n    scope_resource: dict[str, JsonValue] = dict(scope.fields())\n',
)
replace(
    control_plane,
    '        "aggregation_mode": aggregate.aggregation_mode.value,\n        "trend_window_start":',
    '        "aggregation_mode": aggregate.aggregation_mode.value,\n        "scope": scope_resource,\n        "trend_window_start":',
)
replace(
    control_plane,
    '        "window_seconds": budget.window_seconds,\n        "include_estimated": budget.include_estimated,',
    '        "window_seconds": budget.window_seconds,\n        "window_mode": budget.window_mode.value,\n        "window_start": None if state.window_start is None else state.window_start.isoformat(),\n        "window_end": None if state.window_end is None else state.window_end.isoformat(),\n        "include_estimated": budget.include_estimated,',
)

types = "frontend/src/api/types.ts"
replace(
    types,
    '  aggregation_mode: AggregationMode;\n  trend_window_start:',
    '  aggregation_mode: AggregationMode;\n  scope: Record<string, string>;\n  trend_window_start:',
)
replace(
    types,
    '  window_seconds: number | null;\n  include_estimated: boolean;',
    '  window_seconds: number | null;\n  window_mode: "lifetime" | "rolling";\n  window_start: string | null;\n  window_end: string | null;\n  include_estimated: boolean;',
)

page = "frontend/src/pages/UsagePage.tsx"
replace(
    page,
    '<thead><tr><th>Metric</th><th>Value</th><th>Mode</th><th>Records</th><th>Quality mix</th><th>Recent history</th></tr></thead>',
    '<thead><tr><th>Metric</th><th>Value</th><th>Mode</th><th>Scope</th><th>Records</th><th>Quality mix</th><th>Recent history</th></tr></thead>',
)
replace(
    page,
    '              <td>{aggregate.aggregation_mode}</td>\n              <td>{aggregate.record_count}</td>',
    '              <td>{aggregate.aggregation_mode}</td>\n              <td>{scopeSummary(aggregate.scope)}</td>\n              <td>{aggregate.record_count}</td>',
)
replace(
    page,
    '<td>{budget.kind} · {budget.action}{budget.include_estimated ? " · estimates included" : ""}</td>',
    '<td>{budget.kind} · {budget.action} · {budget.window_mode}{budget.include_estimated ? " · estimates included" : ""}</td>',
)
