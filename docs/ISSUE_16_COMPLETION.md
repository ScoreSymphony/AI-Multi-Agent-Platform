# Issue #16 completion evidence

Issue: #16 — Add end-to-end observability for tasks, agents, models, tools and workers

## Completion decision

Issue #16 is complete at the **platform observability contract boundary**.

The issue owns canonical telemetry, trace composition, propagation, failure classification, health semantics, redaction, timeline/query integration and replaceable exporter/measurement seams. It does not own durable Agent/Team state (#33), scheduler/Worker/Node state (#14), authorization policy state (#15), verification state (#86), or durable accounting/budget state (#76).

The final completion proof therefore uses the canonical replaceable contracts and the real internal message transport rather than implementing those other issues inside #16. Later owning runtimes can attach to the already-tested seams without redefining observability ownership.

## Canonical hierarchy

The final tested hierarchy is:

```text
Task
└── Run
    └── Agent Run
        ├── Model Call
        ├── Tool Call
        └── Worker Dispatch
            └── remote Worker / Node execution
```

`TraceHierarchy`, `observe_agent_run()`, `ObservedModelProvider`, `ObservedToolProvider` and `ObservedWorkerProvider` compose this hierarchy. The final E2E regression sends the worker dispatch through `InProcessMessageTransport`, reconstructs the remote parent with `extract_trace_carrier()`, and creates the remote Worker/Node child with `observe_remote()`.

## Cross-boundary context

`TraceCarrier` now propagates the complete canonical telemetry context when available, including:

- project/workspace;
- task/run/step;
- agent/team;
- model call/config/provider;
- tool invocation/capability;
- worker job/node/worker;
- automation/trigger;
- approval/verification;
- correlation/causation;
- adapter/provider identifiers.

The transport bridge stores this context in backend-neutral trace baggage while continuing to mirror the transport's canonical project/task/run/correlation fields. The final E2E test proves Agent/Team identity survives the Worker transport boundary.

## Foundation acceptance

Covered by `tests/test_observability.py` and follow-up regression suites:

- [x] One reference Task/Run/Executor flow is traceable by canonical IDs.
- [x] Structured logs contain Task/Run/correlation context.
- [x] Lifecycle/execution timing and outcome metrics exist.
- [x] Retry metrics are derived from repeated canonical Run attempts.
- [x] Health/readiness distinguishes ready, degraded, unavailable and draining states.
- [x] Core works with the observability exporter disabled/no-op.
- [x] Sensitive-value capture policy/redaction hooks exist.
- [x] Stage 1 works without Models, Tools or distributed Workers installed.
- [x] Detached asynchronous work supports span links without false parentage.

## Progressive integrations

### Models

Model calls, failures, latency, reported numeric usage and explicit router fallback/selection outcomes are instrumented. Missing usage is never fabricated, and prompt/response content is not copied into telemetry by the wrappers.

### Capabilities / tools

Tool calls, latency, failures, policy denial, approval-required and approval outcomes are represented through the canonical invocation observer without copying tool input/output bodies.

### Authorization

`ObservedAuthorizationProvider` instruments the currently available authorization contract with allow/deny decisions, latency, failures and canonical `authorization_approval` failure classification. Future richer Approval lifecycle data can attach through the existing telemetry context and does not require a #16 redesign.

### Workers / nodes

Worker provider operations are instrumented and remote trace context crosses the replaceable #35 transport boundary. Provider-reported numeric resource/load metadata is emitted only when available. Future scheduler heartbeat/reservation semantics remain owned by #14 but attach to the same context/metric conventions.

### Health / readiness

Required and optional provider health is aggregated so optional degradation remains visible without unnecessarily making the platform unready.

### Canonical timeline

The Control Plane can bind a backend-neutral `TimelineReader` and merge derived non-kernel observability entries with authoritative canonical event history. Clients do not need provider-private logs.

### Usage/accounting handoff

`AccountingBridgeExporter` forwards canonical `MetricRecord` measurements to a replaceable `MeasurementSink` while observability owns no durable UsageRecord, budget, threshold or cost state. Durable accounting remains #76's responsibility.

## Completion acceptance

- [x] Model, Tool, Agent and Worker operations attach to the same canonical trace hierarchy through their platform contracts.
- [x] Trace context crosses a real internal message transport / remote-worker boundary.
- [x] Remote execution preserves available canonical Agent/Team and Task/Run context.
- [x] Failures identify the responsible canonical layer/category.
- [x] Optional adapter degradation is visible without unnecessarily killing readiness.
- [x] Frontend/API can obtain canonical Task/Run timelines without backend-private queries.
- [x] Usage/accounting measurements can be handed off without observability owning budgets/cost records.
- [x] Async event/link correlation is preserved.
- [x] Sensitive model/tool content remains absent from the final E2E telemetry proof.

## Required test evidence

- `tests/test_observability.py`
  - Task/Run trace propagation;
  - Executor child/timing;
  - failure classification;
  - redaction;
  - exporter-disabled path;
  - health semantics;
  - model/tool child telemetry;
  - async correlation;
  - transport-neutral carrier round-trip.
- `tests/test_issue_16_followup.py`
  - retry metric;
  - async span links;
  - span-link redaction.
- `tests/test_issue_16_authorization_telemetry.py`
  - allow/deny and provider-failure authorization telemetry.
- `tests/test_issue_16_completion.py`
  - model usage;
  - capability approval/policy outcomes;
  - remote transport propagation;
  - Control Plane timeline;
  - health aggregation;
  - accounting handoff.
- `tests/test_issue_16_final_e2e.py`
  - one continuous `Task -> Run -> Agent -> Model/Tool -> Worker dispatch -> transport -> remote Worker/Node` trace;
  - complete cross-boundary canonical context preservation;
  - accounting measurement handoff in the same local flow;
  - content-redaction invariant.

## Ownership invariant

Completing #16 does **not** complete or absorb #14, #15, #33, #76 or #86. Those issues may add richer domain-owned state and events later, but must consume these existing observability seams rather than redefining telemetry ownership.

## Closure rule

#16 may close once the final PR containing the full-context carrier and completion-level E2E regression passes the repository's normal format, lint, type-check, test and package-build gates on current `main`.
