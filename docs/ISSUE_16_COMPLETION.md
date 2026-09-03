# Issue #16 completion progress evidence

Issue: #16 — Add end-to-end observability for tasks, agents, models, tools and workers

> Status: **open**. This document records implemented evidence and the remaining dependency-bound completion work. It is not itself evidence that the full issue Definition of Done has been reached.

## Scope and ownership rule

Issue #16 owns the cross-cutting observability contract and instrumentation seams. It does **not** own the durable Agent/Team domain (#33), the Node/Worker scheduler/runtime (#14), authorization/approval ownership (#15), runtime verification (#86), or durable usage/budget accounting (#76).

Those boundaries do not make their integrations optional for the final #16 Definition of Done. Stage 1 and progressive seams can be completed before those domains exist, but #16 remains open until the real owning runtimes attach to the canonical telemetry hierarchy where the issue explicitly requires end-to-end integration.

## Foundation acceptance

Covered by `tests/test_observability.py`:

- Task -> Run -> Executor uses one trace and canonical identifiers;
- structured logs carry Task/Run/correlation context;
- lifecycle and executor metrics include timing/outcome data;
- liveness/readiness distinguishes ready, degraded, unavailable and draining states;
- `NoOpExporter` keeps external observability optional;
- `CapturePolicy` defaults to suppressing content-bearing telemetry and recursively redacts common secret fields;
- Stage 1 requires no model, tool or distributed-worker installation.

## Progressive implementation already present

### Shared trace hierarchy

`TraceHierarchy` composes child spans using the active operation, then canonical Run and Task anchors. `observe_agent_run()` is a backend-neutral Agent-runtime hook and does not create or own Agent state.

The intended hierarchy is:

```text
Task
└── Run
    └── Orchestration / Agent Run
        ├── Model Call
        ├── Tool Call
        └── Worker Dispatch
            └── remote Worker / Node execution
```

`ObservedOrchestrator`, `ObservedModelProvider`, `ObservedModelRouter`, `ObservedToolProvider`, `ObservedNodeProvider` and `ObservedWorkerProvider` provide instrumentation seams for the corresponding canonical provider contracts.

### Detached asynchronous work

`SpanLink` and `TraceHierarchy.observe_linked()` model fan-in or detached asynchronous work where a direct parent/child relation would be false. Linked spans retain trace/span references and canonical context while starting independently. Link attributes pass through the same redaction policy as other exported telemetry.

### Retry metric

The exported `ObservabilityEventProvider` emits `platform.run.retries` when a Task receives its second or later deduplicated `run.created` event. The event stream remains authoritative; observability only derives a count from attempts it actually sees and does not create retry lifecycle state.

### Models

Model instrumentation emits calls, failures, duration and numeric usage supplied by the provider. Missing usage is not fabricated. Router instrumentation records selections and explicit fallback usage when reported by the router.

Prompts and responses are not added to telemetry attributes by these wrappers.

### Capabilities / tools / approvals

`ObservedToolProvider` instruments provider execution. `ObservabilityInvocationObserver` consumes canonical capability invocation records and exposes denied, approval-required, approved, failed/timed-out and completed-duration telemetry without copying tool input/output bodies.

### Authorization progress (#15)

`ObservedAuthorizationProvider` instruments the authorization contract that exists today. It attaches authorization calls to the current Task/Run trace where canonical identifiers are available and emits:

- authorization call/failure/duration telemetry;
- `platform.authorization.decisions` for allow/deny outcomes;
- `platform.authorization.denied` for denied policy decisions;
- canonical `authorization.allowed` / `authorization.denied` log and timeline entries;
- `authorization_approval` failure-component classification for denied decisions and provider failures.

The wrapper does not copy provider `reason` free text or exception details into telemetry. Principal/resource references are kept in structured log/timeline audit attributes, not metric dimensions.

The current `AuthorizationDecision` only models `allowed: bool`; therefore this is partial #15 integration. `require approval`, Approval IDs/lifecycle and final #15 audit records must be integrated when #15 supplies those canonical concepts.

### Worker / node seams and trace transport

The current Node/Worker provider wrappers instrument the existing backend-neutral provider contracts. `inject_trace_carrier()` and `extract_trace_carrier()` bridge `TraceCarrier` to the #35 `TransportEnvelope.trace_context`, and `TraceHierarchy.observe_remote()` reconstructs remote parentage.

The current regression test proves this across `InProcessMessageTransport`. This is transport propagation evidence, not yet a substitute for a real #14 scheduler -> worker runtime integration.

### Health / readiness

`AggregatedHealthProvider` maps required-vs-optional dependency health into the Control Plane health seam. Optional dependency failure can remain ready/degraded; required dependency failure makes readiness unavailable.

### Canonical Task/Run timeline

The Control Plane can bind a backend-neutral `TimelineReader` and merge derived non-kernel telemetry with canonical event history. Clients therefore do not need provider-private logs as canonical history.

### Usage/accounting handoff (#76)

`AccountingBridgeExporter` forwards canonical `MetricRecord` measurements to a `MeasurementSink` while keeping UsageRecords, budgets, thresholds, costs and durable accounting state outside observability.

This is the #16-side handoff contract. Final integration with a real #76 accounting consumer remains dependent on #76.

## Current verification coverage

`tests/test_issue_16_completion.py` covers:

- reference Agent -> Model/Tool/Worker trace composition;
- provider-reported model usage;
- capability policy/approval outcomes;
- trace propagation through the real #35 in-process message transport;
- optional vs required health semantics in the Control Plane;
- Control Plane timeline enrichment;
- accounting measurement handoff without accounting ownership.

`tests/test_issue_16_followup.py` additionally covers:

- retry counting from repeated canonical Run attempts;
- detached asynchronous span links without false parentage;
- redaction of span-link attributes.

`tests/test_issue_16_authorization_telemetry.py` covers:

- allow/deny decisions under the canonical Task trace;
- denial metrics and canonical authorization-layer failure classification;
- safe audit attributes without copying provider reason text;
- technical authorization-provider failure classification without exporting exception details.

## Remaining work before #16 may close

The following items remain open because their owning runtime domains are not complete:

- [ ] Attach the real #33 Agent/Team runtime to `TraceHierarchy` and prove a real Agent Run inherits the canonical Task/Run trace.
- [ ] Attach the real #14 scheduler/Worker/Node runtime to the existing wrappers.
- [ ] Prove scheduler dispatch -> transport -> remote Worker job -> Node execution end-to-end with the real #14 runtime, not only the in-process transport fixture.
- [ ] Emit #14-owned heartbeat age, active-job and canonical health/load/resource measurements when the runtime exposes them.
- [ ] Extend the now-instrumented #15 allow/deny provider path with require-approval, Approval IDs/lifecycle and final canonical security audit records once #15 defines them.
- [ ] Consume #86 verification telemetry once the canonical verification runtime exists.
- [ ] Connect the `MeasurementSink` handoff to the real #76 accounting ingestion path and verify the ownership boundary in integration tests.
- [ ] Add final completion-level integration coverage over the real `Task -> Run -> Agent/Orchestration -> Model/Tool -> Worker/Node` execution path.

## Closure rule

#16 should remain open until the real later-domain implementations that the issue names as completion inputs are connected and the final end-to-end trace is demonstrated without changing telemetry ownership or depending on one observability backend.
