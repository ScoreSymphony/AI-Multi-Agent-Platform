# Observability

Issue: #16

## Purpose

Observability is a platform-owned, cross-cutting capability for explaining what happened across canonical tasks, runs and replaceable provider boundaries without making one monitoring vendor part of the platform architecture.

The Stage 1 foundation is intentionally usable with only the canonical domain/kernel and reference execution path from issues #4-#7. Model, tool, worker, messaging, authorization and distributed-runtime integrations may add richer child telemetry later without redefining this contract.

## Ownership and source-of-truth rule

Canonical lifecycle state remains owned by the platform kernel. Observability is a derived operational view; it must never become a second lifecycle authority.

The kernel's canonical `Event` history is mirrored through `ObservabilityEventProvider`. That provider derives:

- structured logs;
- counters and timing metrics;
- task/run lifecycle spans;
- an API-ready task/run timeline view.

Dropping, replacing or disabling an observability exporter must not change task/run behavior or canonical state.

## Trace hierarchy

The target hierarchy is:

```text
Task
└── Run
    └── Orchestration / Agent Run
        ├── Model Call
        ├── Tool Call
        ├── Worker Job
        └── Node Execution
```

Stage 1 implements Task -> Run -> Executor and defines model/tool child wrappers plus transport-neutral trace propagation for later worker/message boundaries.

A trace is an operational identifier. It supplements canonical identifiers; it does not replace them.

## Canonical telemetry context

`TelemetryContext` carries identifiers when they are available:

- project ID;
- workspace ID;
- task ID;
- run ID;
- step ID;
- agent ID;
- team ID;
- model call/config/provider IDs;
- tool invocation/capability IDs;
- worker job/node/worker IDs;
- automation/trigger IDs;
- approval/verification IDs;
- correlation ID;
- causation ID;
- adapter/provider IDs.

Not every component has every identifier. Instrumentation must propagate existing identifiers rather than manufacture unrelated canonical identities.

## Structured logs

Structured logs use `StructuredLog` and include:

- timestamp;
- severity;
- canonical component category;
- event name;
- telemetry context;
- normalized outcome;
- optional failure classification;
- optional duration;
- explicitly selected, redacted attributes.

Kernel event payloads are not copied wholesale into logs. Reference executor instrumentation records capability/action identity, IDs, result code, duration and outcome, but not arguments, stdout or stderr.

## Failure taxonomy

Every failure may be assigned to one canonical component/layer:

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

The layer classification is separate from the more specific failure code. For example an executor can report component `execution` with code `timeout`, while a model adapter may report component `model_provider_router` with code `rate_limited`.

Provider SDK exceptions must continue to follow the canonical error contracts; observability does not authorize raw backend exceptions to become public API semantics.

## Metrics

The Stage 1 reference instrumentation emits backend-neutral records including:

- `platform.lifecycle.events`;
- `platform.tasks.created`;
- `platform.tasks.terminal`;
- `platform.task.duration_seconds`;
- `platform.runs.created`;
- `platform.runs.terminal`;
- `platform.run.queue_wait_seconds`;
- `platform.run.duration_seconds`;
- `platform.executor.calls`;
- `platform.executor.failures`;
- `platform.executor.duration_seconds`.

Model/tool wrappers additionally define `platform.model.*` and `platform.tool.*` child metrics. Later worker/node and automation integrations should extend the same naming approach instead of introducing a separate metrics vocabulary.

Metrics must prefer stable canonical dimensions. High-cardinality IDs remain available in trace/log/timeline records and should not automatically become production metrics labels in an external backend.

## Timeline semantics

`TimelineEntry` is a derived operator/user-facing view for answering questions such as:

- when a task was created, started and completed;
- which run attempt executed;
- which executor/model/tool child operations occurred;
- where a failure was classified;
- how long lifecycle and execution stages took.

`InMemoryExporter.query_timeline()` provides the Stage 1 API-ready query contract by task ID, run ID or correlation ID. A later Control Plane endpoint may expose this view without changing the telemetry model.

The timeline is not an event-sourcing store and must not be used to reconstruct canonical state when the kernel event history is available.

## Exporter boundary

`ObservabilityExporter` is the only Stage 1 sink boundary. It accepts normalized log, metric, span and timeline records.

Reference implementations:

- `NoOpExporter`: default/local mode with no external telemetry backend;
- `InMemoryExporter`: deterministic local/test mode and timeline query reference.

Future Prometheus/OpenTelemetry/OTLP/logging/tracing adapters belong behind this boundary. No exporter technology is mandatory for the platform to run.

## Redaction and sensitive data

`CapturePolicy` is default-deny for content-bearing telemetry.

By default the platform does **not** capture:

- prompts;
- model responses;
- tool inputs;
- tool outputs;
- file contents;
- authentication/session values.

Common secret fields such as API keys, passwords, credentials, authorization values, cookies and access/refresh/ID tokens are recursively redacted even when generic attributes are otherwise permitted.

Instrumentation should select safe attributes instead of relying on redaction as permission to log arbitrary payloads. Secrets must never intentionally be logged.

## Health semantics

Liveness and readiness are distinct:

- `alive`: the process/component is running;
- `ready`: it can accept the intended class of work;
- `degraded`: it can operate, but one or more dependencies/capabilities are impaired;
- `unavailable`: a required dependency or the component itself prevents work;
- `draining`: it is alive but intentionally not accepting new work.

`aggregate_health()` treats an unavailable optional adapter as degraded rather than fatal. An unavailable required dependency makes readiness unavailable. This preserves useful partial operation when optional components are absent.

## Trace propagation

`TraceCarrier` serializes a transport-neutral trace parent plus correlation/causation and canonical task/run/step/project IDs. It intentionally has no dependency on HTTP, a message broker or the future worker scheduler.

Later remote-worker or messaging adapters should:

1. create a carrier from the current span;
2. attach the carrier mapping to their transport envelope;
3. reconstruct the carrier remotely;
4. create the child span using the propagated trace ID and parent span ID;
5. preserve canonical correlation/causation IDs.

## Progressive integrations

Later issues extend this foundation rather than replacing it:

- model routing/provider work: model latency, usage, route/failure dimensions;
- tool/capability work: tool latency, approval/failure dimensions;
- node/worker scheduling: queue, dispatch, worker/node health and resource dimensions;
- authorization/approval: denied/approval/verification events without secret/session leakage;
- messaging: cross-process propagation and event-transport telemetry;
- automation: trigger-to-task traces;
- Agent Runtime: agent/team/orchestration child spans.

These integrations are intentionally not hard dependencies of the Stage 1 package.

## Reference integration

A fully observable reference path is composed without changing kernel ownership:

```python
exporter = InMemoryExporter()
telemetry = Telemetry(exporter)
event_sink = ObservabilityEventProvider(telemetry)
executor = ObservedExecutor(ReferenceExecutor(workspace_root), telemetry)
lifecycle = ExecutorLifecycleBackend(executor, workspace=workspace, action="echo")
kernel = PlatformKernel(
    orchestrator=FakeOrchestrator(),
    lifecycle=lifecycle,
    event_sink=event_sink,
)
```

The kernel emits canonical events, `ObservabilityEventProvider` derives lifecycle telemetry, and `ObservedExecutor` contributes execution child telemetry under the same task/run trace.
