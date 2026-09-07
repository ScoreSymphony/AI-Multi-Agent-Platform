# Observability

Issue: #16

## Purpose

Observability is a platform-owned, cross-cutting capability for explaining what happened across canonical Tasks, Runs and replaceable provider boundaries without making one monitoring vendor part of the platform architecture.

The Stage 1 foundation is usable with only the canonical domain/kernel and reference execution path from #4-#7. Later Model, Tool, Agent, Worker, authorization, verification and accounting domains attach to the same contracts progressively.

## Ownership and source-of-truth rule

Canonical lifecycle state remains owned by the platform kernel. Observability is a derived operational view and must never become a second lifecycle authority.

Dropping, replacing or disabling an observability exporter must not change Task/Run behavior or canonical state.

## Trace hierarchy

The target hierarchy is:

```text
Task
└── Run
    └── Orchestration / Agent Run
        ├── Model Call
        ├── Tool Call
        └── Worker Dispatch
            └── Worker Job / Node Execution
```

Stage 1 implements Task -> Run -> Executor. Progressive instrumentation provides Orchestrator, Model, Tool, Agent-hook, Node and Worker provider seams. Real Agent and distributed Worker runtime integration remains dependent on #33 and #14 respectively.

A trace is an operational identifier. It supplements canonical identifiers; it does not replace them.

## Canonical telemetry context

`TelemetryContext` carries identifiers when available:

- project/workspace;
- Task/Run/Step;
- Agent/Team;
- model call/config/provider;
- tool invocation/capability;
- Worker job/Node/Worker;
- automation/trigger;
- approval/verification;
- correlation/causation;
- adapter/provider.

Instrumentation propagates existing identifiers rather than manufacturing unrelated canonical identities.

## Parent/child spans, remote propagation and async links

Normal synchronous nesting uses parent/child spans through `TraceHierarchy`.

Remote propagation uses `TraceCarrier`, which preserves trace parentage plus Task/Run/Step/Project and correlation/causation identifiers. `inject_trace_carrier()` and `extract_trace_carrier()` bridge that carrier to the replaceable #35 `TransportEnvelope.trace_context`. `observe_remote()` creates the remote child span.

Detached asynchronous or fan-in work where direct parentage would be false uses `SpanLink` and `TraceHierarchy.observe_linked()`. Linked work starts independently and references one or more prior spans without pretending one of them is the sole parent.

## Structured logs

`StructuredLog` records:

- timestamp;
- severity;
- canonical component/layer;
- event name;
- telemetry context;
- normalized outcome;
- optional failure classification;
- optional duration;
- explicitly selected, redacted attributes.

Kernel event payloads, prompts, model responses, tool input/output, file contents and auth/session values are not copied wholesale into telemetry.

## Failure taxonomy

Failures may be classified by canonical component:

- `domain_kernel`;
- `orchestration`;
- `agent`;
- `execution`;
- `model_provider_router`;
- `capability_tool`;
- `persistence_storage`;
- `authorization_approval`;
- `verification`;
- `scheduler_worker_node`;
- `automation`;
- `connector_browser`;
- `plugin_adapter`;
- `infrastructure_unknown`.

Provider-specific exceptions remain behind canonical contracts.

## Metrics

Foundation metrics include:

- `platform.lifecycle.events`;
- `platform.tasks.created`;
- `platform.tasks.terminal`;
- `platform.task.duration_seconds`;
- `platform.runs.created`;
- `platform.runs.terminal`;
- `platform.run.retries`;
- `platform.run.queue_wait_seconds`;
- `platform.run.duration_seconds`;
- `platform.executor.calls`;
- `platform.executor.failures`;
- `platform.executor.duration_seconds`.

`platform.run.retries` is derived from a second or later deduplicated `run.created` event for the same canonical Task. Observability counts attempts it sees but does not own retry lifecycle state.

Progressive Model instrumentation adds calls, latency/failures, provider-reported numeric usage and route/fallback counts. Tool instrumentation adds calls, latency/failures and canonical capability permission/approval outcomes. Worker/Node wrappers emit only measurements their current provider contracts actually expose; #14 remains responsible for the final heartbeat/job/resource runtime data.

## Timeline semantics

`TimelineEntry` is a derived operator/user-facing view. The Control Plane can bind a backend-neutral `TimelineReader` and merge non-kernel telemetry with canonical kernel Event history.

Clients therefore query the canonical Control Plane surface instead of Hermes, Forge, model providers, workers or exporter-private stores. The timeline is not an event-sourcing store and must not be used to reconstruct canonical lifecycle state.

## Exporter boundary

`ObservabilityExporter` is the backend-neutral sink boundary for normalized logs, metrics, spans and timeline entries.

Reference implementations:

- `NoOpExporter`: default/local mode with no external backend;
- `InMemoryExporter`: deterministic local/test mode and timeline query reference.

No Prometheus, Grafana, Jaeger, Loki, OTLP collector or commercial SaaS is required by the canonical platform.

## Redaction and sensitive data

`CapturePolicy` is default-deny for content-bearing telemetry. By default the platform does not capture:

- prompts;
- model responses;
- tool inputs/outputs;
- file contents;
- authentication/session values.

Common secret fields are recursively redacted. Span-link attributes pass through the same generic redaction policy before export.

## Health semantics

Liveness and readiness are distinct:

- `alive`: process/component is running;
- `ready`: it can accept intended work;
- `degraded`: usable with impaired optional dependency/capability;
- `unavailable`: a required dependency prevents intended work;
- `draining`: alive but intentionally not accepting new work.

`AggregatedHealthProvider` maps required-vs-optional provider health into the Control Plane seam so an optional adapter failure does not automatically make the entire platform unavailable.

## Usage/accounting boundary

`AccountingBridgeExporter` forwards `MetricRecord` measurements to a `MeasurementSink` while observability retains ownership of telemetry and #76 retains ownership of durable UsageRecords, normalization, aggregation, budgets, thresholds and costs.

Missing measurements are not fabricated.

## Current completion boundary

The observability foundation and progressive contracts are implemented, including async links, retry metrics, Model/Tool instrumentation, health aggregation, Control Plane timeline enrichment, #35 trace propagation and the #76 measurement handoff seam.

#16 nevertheless remains open until the real completion-input runtimes are available and integrated, especially:

- #33 real Agent/Team runtime;
- #14 real scheduler/Worker/Node runtime and heartbeat/job/resource telemetry;
- #15 authorization/approval audit integration;
- #86 verification telemetry;
- #76 real accounting consumer integration.

See `docs/ISSUE_16_COMPLETION.md` for the current acceptance mapping and remaining closure checklist.
