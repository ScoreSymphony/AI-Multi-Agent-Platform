# Multi-Agent Trace Explorer

The Trace Explorer productizes the canonical Observability foundation as a Task-scoped trace explorer. The explorer is a **read projection** only: it does not own Task, Run, Step, AgentRun, Approval, Verification, Result, Artifact or accounting lifecycle state, and it does not create a second telemetry store.

## Surfaces

The versioned Control Plane exposes:

- `GET /api/v1/tasks/{task_id}/trace` for the paginated Task trace;
- `GET /api/v1/tasks/{task_id}/trace/{node_id}` for one trace node;
- the existing `GET /api/v1/tasks/{task_id}/timeline` for the flat chronological view.

The Web UI uses the same endpoints from the Observability page. The CLI exposes the same projection with:

```text
platform trace TASK_ID
platform trace TASK_ID --agent AGENT_ID
platform trace TASK_ID --capability CAPABILITY_ID
platform trace TASK_ID --failure-component execution
platform trace TASK_ID --node SPAN_OR_NODE_ID
```

Clients must not inspect exporter-private storage or provider logs directly.

## Trace model

Canonical `SpanRecord` parent relationships form the primary hierarchy. Timeline events are attached to the most specific matching span when their canonical context and time interval identify one. Detached/asynchronous relationships remain visible through span links. Overlapping sibling spans are reported through `parallel_with`, which makes parallel Step/Agent execution distinguishable from sequential flow without inventing lifecycle state.

Trace nodes preserve the identifiers already carried by `TelemetryContext`, including Task, Run, Step, Agent, Team, model, Capability/Tool, Worker/Node, Approval and Verification identifiers when available. Safe reference attributes may additionally surface canonical links to Plans, AgentRuns, Results, Artifacts, Handoffs, ContextBundles and Verification Results. The Web projection also resolves the canonical Runs belonging to the selected Task and, when a Run records a Workspace binding, exposes that canonical Workspace as a navigable diagnostic reference. The binding is read from Run/Workspace state rather than inferred from telemetry.

Missing telemetry is explicit. `telemetry_state` is `available`, `degraded` or `missing`, and `missing_sources` identifies unavailable span, timeline or usage readers. The projection never fabricates durations, identifiers, token counts or costs.

## Usage and cost

Accounting remains the durable accounting authority. When Accounting is configured, the trace explorer reads canonical `UsageRecord`s and preserves:

- metric type and unit;
- measured/reported/estimated/unavailable quality;
- provider/source and provenance;
- quantity;
- cost amount and currency when the accounting record actually contains them.

Observability does not recalculate provider cost or convert an unavailable measurement into an estimate. Records that can be unambiguously matched to a span are shown on that node; Task-only records remain visible as Task-level usage.

## Security and content visibility

The trace explorer inherits the Observability capture/redaction policy and adds a projection-level defense in depth:

- prompts, messages, request/response bodies, tool inputs/outputs, file content and other content-bearing payload fields are omitted from the trace read model;
- secrets, credentials, passwords, cookies, authorization material and token-like secrets are redacted;
- the projection prefers canonical identifiers, references, digests and diagnostic metadata;
- a trace resource link is only a link. Reading the linked canonical resource still goes through its owning Control Plane authorization boundary;
- Task trace reads themselves use the existing Task event/read authorization path.

This means the explorer is useful for causal inspection without becoming a bypass around Artifact, Context, File, Approval or Verification authorization.

## Filtering and large traces

The Task trace supports Control Plane pagination and filters for Agent, Step, model configuration/provider, Capability, failure component and timezone-aware start-time bounds. The Web UI loads additional pages lazily with the returned cursor. The CLI exposes the common filters directly.

Filtering is performed on the derived node model after canonical Task authorization. Pagination is then applied to the filtered projection, so the API does not expose provider-specific query semantics.

## Multi-agent acceptance

The maintained reference multi-agent golden path is the acceptance anchor for the product experience. Trace Explorer tests additionally cover hierarchy, parallel siblings, model/tool children, failure/retry classification, Verification events, Handoff/Context/Artifact references where telemetry supplies them, missing accounting data, redaction and pagination semantics.

The important boundary is intentional: The reference golden path and the owning domains remain the authority for whether a Plan revision was activated, a Handoff was consumed or a Result was accepted. The Trace Explorer only makes the corresponding canonical telemetry and references inspectable.
