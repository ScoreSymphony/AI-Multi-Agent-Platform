# Issue #1159 transport DTO audit

This audit records the Search, observability/telemetry and accounting/usage frontend types reviewed while completing the generated Control Plane transport coverage left by #984.

## Supported wire contracts migrated to generated DTOs

| Surface | Canonical transport contract | Frontend compatibility export |
| --- | --- | --- |
| Search query wire semantics | `SearchQueryParameters`, derived from the canonical `/api/v1/search` OpenAPI parameters | `SearchMode` and `SearchSort` derive from generated query types; `SearchRequest` remains a query-builder model described below |
| Search response | `SearchResult`, `SearchPage` | `SearchResult`, `SearchPage` |
| Timeline domain event | `CanonicalEvent` | `CanonicalEvent` |
| Timeline telemetry | `TelemetryFailure`, `TelemetryTimelineEntry`, `TimelineItem` | `TelemetryTimelineEntry`, `TimelineItem` |
| Usage record | `UsageRecord` | `CanonicalUsageRecord` |
| Usage aggregate | `UsageQualityCounts`, `UsageTrendPoint`, `UsageAggregate` | `MeasurementQuality`, `AggregationMode`, `CanonicalUsageTrendPoint`, `CanonicalUsageAggregate` |
| Usage budget | `UsageBudget` | `CanonicalUsageBudget` |

The TypeScript compatibility exports above resolve to types emitted by `scripts/generate_frontend_contracts.py`; they are not independently maintained wire declarations.

## Frontend-owned models intentionally not generated

- `SearchRequest` is an ergonomic query-builder model. Its plural array fields (`types`, `statuses`, `tags`, `sources`, `providers`) are transformed by `toSearchQuery()` into the canonical comma-separated singular HTTP query parameters. Scalar enum/value semantics derive from `SearchQueryParameters`.
- `Page<T>` is a generic frontend compatibility helper used across generated and non-generated resource clients; concrete Search and accounting transport pages are schema-backed where their public API contracts require them.
- `ListQuery` is generic frontend request-building state for collection clients rather than a resource DTO.
- `TimelineSummary` and page/component state are presentation models derived from wire DTOs and remain frontend-owned.

## Backend/internal contracts intentionally not exposed as frontend wire DTOs

- `SearchQuery` and `SearchDocument` are backend/provider-side Search contracts. The browser wire contract is the `/search` OpenAPI query/response surface instead.
- Observability `MetricRecord`, `SpanRecord`, exporters/readers and trace-internal records remain internal domain/provider contracts unless separately exposed by a supported Control Plane endpoint.
- Accounting `UsageQuery`, `UsageScope`, `BudgetState` and store/service types remain domain/runtime contracts. Only the serialized `usage-records`, `usage-aggregates` and `usage-budgets` projections are generated for the browser.

## Contract change rule

For these supported surfaces, contributors change the canonical backend/API schema first, regenerate `frontend/src/api/generated/control-plane-v1.ts`, update explicit query/UI mapping where needed, and rely on generated-contract drift checks plus frontend type/test/build coverage to reject stale output. There is no remaining Search, telemetry or accounting exception permitting an independently handwritten frontend wire DTO.
