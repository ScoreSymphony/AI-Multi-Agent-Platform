# Evaluation cross-domain evidence provenance

Issue #19 consumes source-owned evidence from the completed Observability (#16) and
Usage/Resource Accounting (#76) domains. Evaluation does not create a second
telemetry, logging, or accounting authority.

## Integration boundary

`EvidenceEnrichingCaseExecutor` decorates the replaceable `EvaluationCaseExecutor`
boundary. It executes the underlying case first, then asks one or more
`EvaluationEvidenceProvider` implementations to project evidence for the exact
canonical `task_id` / `run_id` returned by that execution.

This keeps evidence projection independent from a concrete executor. The same
decorator can wrap the reference kernel executor, Hermes-backed orchestration,
Forge-backed execution, or a future adapter without moving evidence ownership into
those implementations.

`CompositeEvaluationEvidenceProvider` composes multiple providers and rejects
duplicate top-level data or metric keys instead of silently overwriting evidence.

## Accounting evidence

`AccountingEvaluationEvidenceProvider` reads canonical `UsageRecord` values through
`AccountingService`.

For the exact Task/Run pair it:

- retains the source-owned canonical `UsageRecord.id`;
- preserves metric type, unit, measurement quality, source, aggregation mode,
  provider and correlation/causation references;
- uses the canonical #76 `aggregate_usage_records(...)` implementation rather than
  reimplementing additive/latest semantics;
- projects available totals into observation metrics as
  `accounting:<metric_type>:<unit>`;
- keeps `MeasurementQuality.UNAVAILABLE` records as structured evidence but does
  **not** emit a numeric metric for an unavailable-only aggregate;
- adds source-qualified references of the form
  `accounting:usage:<canonical UsageRecord.id>`.

Evaluation therefore never converts an unavailable resource measurement into zero
and never treats an estimated/reported quantity as if it were directly measured.

## Observability evidence

`InMemoryObservabilityEvaluationEvidenceProvider` is the deterministic reference
adapter for the completed #16 in-memory exporter.

For the exact Task/Run pair it projects:

- trace IDs and span IDs from canonical `SpanRecord` values;
- correlation IDs where present;
- bounded structured span/log/timeline metadata needed for evaluation evidence.

Content-bearing attributes are deliberately not copied by this adapter. Evaluation
must not become a path for bypassing the existing Observability capture/redaction
policy.

Trace/span/correlation references use source identities:

- `observability:trace:<trace_id>`;
- `observability:span:<span_id>`;
- `observability:correlation:<correlation_id>`.

`StructuredLog` currently has no canonical platform log ID. Evaluation therefore
does **not** synthesize one. A log reference is persisted only when the configured
observability adapter supplies a real reference through `LogReferenceResolver`, in
which case it is namespaced as `observability:log:<source reference>`.

A future durable observability backend can implement another
`EvaluationEvidenceProvider`; the Evaluation domain does not depend on the
in-memory exporter as canonical storage.

## Result and persistence propagation

The enriched evidence is added to the existing `EvaluationObservation`:

- structured source metadata -> `data`;
- numeric accounting totals -> `metrics`;
- source-qualified evidence references -> `telemetry_refs`.

Existing deterministic, metric, rubric and optional model-judge evaluators already
copy `EvaluationObservation.telemetry_refs` into the canonical `EvaluationResult`.
The strict EvaluationResult codec and SQLite history therefore persist the
references without a new evaluation-private accounting/log schema.

Repeated-run aggregation already unions `telemetry_refs`, so an
`AggregatedEvaluationResult` remains linked to the evidence of every contributing
sample.

The optional ModelJudge receives the same observation data, metrics and references
through its existing structured request payload; this evidence integration does
not make model judging mandatory.

## Invariants

- #16 owns observability records and redaction semantics.
- #76 owns usage/accounting records, units, quality and aggregation semantics.
- Evaluation stores references/projections, not duplicate source-of-truth records.
- Missing/unavailable measurements are never fabricated.
- A backend-private log identifier never becomes canonical merely because an
  evaluation consumed it.
- Evidence enrichment remains replaceable and executor-neutral.
- The mandatory deterministic PR Evaluation gate remains free of paid services.
