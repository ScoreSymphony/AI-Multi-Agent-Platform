# Issue #16 completion evidence

Issue: #16 — Add end-to-end observability for tasks, agents, models, tools and workers

## Scope rule

Issue #16 owns the cross-cutting observability contract and instrumentation seams. It does **not**
own the durable Agent/Team domain (#33), the Node/Worker scheduler/runtime (#14), or durable
usage/budget accounting (#76).

Completion therefore means that every existing canonical layer is instrumented directly and that
later Agent/Worker/Accounting owners can attach to the same trace and measurement contracts
without redefining Task/Run telemetry ownership. Reference fixtures prove those extension paths
before the owning later domains are complete.

## Foundation acceptance

The Stage-1 foundation remains unchanged and covered by `tests/test_observability.py`:

- Task -> Run -> Executor uses one trace and canonical identifiers;
- structured logs carry Task/Run/correlation context;
- lifecycle and executor metrics include timing/outcome data;
- liveness/readiness distinguishes ready, degraded, unavailable and draining states;
- `NoOpExporter` keeps external observability optional;
- `CapturePolicy` defaults to suppressing content-bearing telemetry and recursively redacts
  common secret fields;
- Stage 1 requires no model, tool or distributed-worker installation.

## Progressive completion

### Shared trace hierarchy

`TraceHierarchy` composes child spans using the currently active operation, then canonical Run and
Task anchors. `observe_agent_run()` is the backend-neutral Agent runtime hook. It does not create
or own Agent state.

The supported hierarchy is now:

```text
Task
└── Run
    └── Orchestration / Agent Run
        ├── Model Call
        ├── Tool Call
        └── Worker Dispatch
            └── remote Worker / Node execution
```

`ObservedOrchestrator`, `ObservedModelProvider`, `ObservedModelRouter`, `ObservedToolProvider`,
`ObservedNodeProvider` and `ObservedWorkerProvider` attach their operations to this hierarchy.

### Models

Model instrumentation emits calls, failures and duration. Numeric usage supplied by a provider is
emitted as `platform.model.usage`; missing usage is not fabricated. The observed router emits route
selection counts and records a fallback only when the router explicitly reports that a fallback was
used.

Prompts and responses are not added to telemetry attributes by these wrappers.

### Capabilities / tools / approvals

Provider execution remains instrumented by `ObservedToolProvider`. `ObservabilityInvocationObserver`
consumes the canonical capability invocation records from #12 and adds policy/approval outcomes:

- denied;
- approval required;
- approved;
- failed/timed out;
- completed duration when a running record exists.

`CompositeInvocationObserver` allows the existing durable event/audit observer and observability
observer to receive the same canonical invocation record without creating a second invocation
lifecycle.

### Worker / node integration and remote trace propagation

The Node/Worker provider wrappers operate only on the existing backend-neutral provider contracts.
They emit inventory, dispatch, failure/timing and numeric resource/load measurements only when a
provider actually reports those values.

`inject_trace_carrier()` and `extract_trace_carrier()` bridge `TraceCarrier` to the replaceable #35
`TransportEnvelope.trace_context`. A remote process reconstructs the parent and creates a child via
`TraceHierarchy.observe_remote()`.

The regression suite sends a real envelope through `InProcessMessageTransport`, consumes it on the
other side, and verifies the remote span has the dispatch span as its parent with unchanged
Task/Run/correlation identity.

### Health / readiness

`AggregatedHealthProvider` adapts the issue-16 `aggregate_health()` semantics to the existing Control
Plane health-provider seam. An unavailable optional dependency produces `degraded` while the Control
Plane remains ready; an unavailable required dependency produces unavailable/not-ready.

### Canonical Task/Run timeline

The composed Control Plane exposes observability entries through the existing canonical Task
timeline. `ControlPlane.bind_observability_timeline()` binds a backend-neutral `TimelineReader`.
Derived non-kernel telemetry entries are merged with canonical kernel events. Kernel lifecycle
history remains authoritative and is not duplicated from telemetry.

Clients therefore continue to query the Control Plane rather than Hermes, Forge, model providers,
workers or exporter-private stores.

### Usage/accounting handoff (#76)

`AccountingBridgeExporter` forwards canonical `MetricRecord` measurements to a `MeasurementSink`
while still forwarding normal observability records to the configured exporter.

The bridge intentionally contains no `UsageRecord`, budget, threshold or cost state. #76 remains the
owner of durable accounting, normalization, aggregation and budget semantics. A failing measurement
sink is non-fatal by default so accounting consumption cannot become hidden lifecycle authority.

## Completion acceptance mapping

| Acceptance criterion | Evidence |
| --- | --- |
| Model, Tool, Agent and Worker operations share the canonical hierarchy | `test_agent_model_tool_and_worker_attach_to_one_canonical_trace` |
| Trace context crosses remote worker boundaries | `test_trace_context_crosses_actual_message_transport_boundary` |
| Failures identify canonical component/layer | foundation failure test + `ObservabilityInvocationObserver` |
| Optional adapter degradation does not unnecessarily kill readiness | `test_optional_degradation_stays_ready_and_required_failure_does_not` |
| API obtains canonical Task/Run timeline without private backend queries | `test_control_plane_timeline_is_enriched_without_private_backend_queries` |
| Measurements can feed #76 without observability owning budgets/costs | `test_accounting_bridge_forwards_measurements_without_owning_accounting_state` |

## Additional regression evidence

`tests/test_issue_16_completion.py` also verifies:

- model usage is emitted only from reported numeric measurements;
- Capability permission/approval outcomes are queryable telemetry;
- Agent/Model/Tool/Worker child contexts preserve Task/Run/Agent IDs;
- prompt/tool-input content is not copied into structured logs;
- remote Worker/Node child telemetry preserves canonical IDs.

Existing `tests/test_observability.py` continues to cover the required Stage-1 reference tests,
redaction, exporter-disabled path, failure classification and asynchronous event correlation.

## Verification commands

The pull request for #16 must pass the repository's normal verification gates, including:

```bash
pytest
ruff check .
mypy src
```

Issue #16 should only be closed after those checks pass on the composed current-main PR revision.
