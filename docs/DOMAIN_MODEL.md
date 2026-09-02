# Canonical Platform Domain Model

This document defines the platform-owned domain model for the AI Multi-Agent Platform. It is intentionally independent from Hermes, Forge, Temporal, model providers, storage engines and deployment products.

Executable reference definitions live in `src/ai_multi_agent_platform/domain/`. Serialized Task, Run and Event contracts live in `schemas/domain/` and are versioned independently from adapters.

## Design rules

1. Canonical entities and scope identities are owned by the platform core.
2. External systems may map to canonical objects but may not redefine them.
3. Stable platform identifiers survive adapter replacement, retries, process restarts and node changes.
4. Backend/provider-specific identifiers are stored only as external references.
5. Ownership, project and provenance hooks are available where applicable.
6. Multi-user and organization ownership must not require an identifier redesign.
7. Lifecycle transitions are explicit and deterministic.
8. Core domain code imports no Hermes-, Forge- or Temporal-specific types.
9. Canonical identity is immutable after construction.
10. Canonical relationships validate canonical IDs instead of accepting backend-private strings.
11. Nested mappings/collections that belong to canonical value state are defensively deep-frozen.
12. Breaking external-contract restrictions require a new schema major version rather than silently narrowing an existing contract.

## Identifier model

Canonical identifiers are opaque type-prefixed UUID strings:

- `goal_<uuid>`
- `project_<uuid>`
- `task_<uuid>`
- `plan_<uuid>`
- `step_<uuid>`
- `run_<uuid>`
- `agent_<uuid>`
- `team_<uuid>`
- `artifact_<uuid>`
- `result_<uuid>`
- `event_<uuid>`
- `approval_<uuid>`
- `node_<uuid>`
- `worker_<uuid>`
- `worker_job_<uuid>`
- `tool_<uuid>`
- `tool_invocation_<uuid>`
- `cap_<uuid>`
- `policy_scope_<uuid>`
- `model_assignment_<uuid>`

Task and Run IDs survive process restart and adapter replacement. Canonical IDs cannot be reassigned after creation.

Backend identifiers are represented separately as `ExternalRef(system, kind, value)` entries. Orchestrator run IDs, executor job IDs, provider request IDs and scheduler handles therefore never become canonical primary keys or relationship targets.

## Common metadata

Canonical persistent objects support, where applicable:

- `id` and `schema_version`;
- creation/update timestamps;
- `owner_ref` and `project_id`;
- provenance;
- external references;
- implementation-neutral metadata/configuration.

Execution/event records additionally use:

- `correlation_id` for one logical flow;
- `causation_id` for the record/action that directly caused another record;
- optional `trace_id` as an observability bridge rather than a domain identity.

`owner_ref` supports `user`, `organization`, `team`, and `service`. Final authorization semantics remain separate work.

Structured provenance, metadata, requirements, status data and coordination data are copied into immutable nested representations at construction time. This prevents callers from mutating canonical state through a retained dictionary, list or other `Mapping` implementation.

## Canonical entities

### Goal

High-level desired outcome requested by a user, application or automation. A Goal may lead to one or more Tasks and is not itself an execution attempt.

### Project / Workspace

Logical ownership and working boundary grouping Goals, Tasks, Agents, Artifacts and configuration. It does not imply a filesystem layout or provider-specific project type.

### Task

Canonical unit of requested work, independent from a concrete orchestrator or executor.

Task states:

```text
draft -> ready -> running -> succeeded
  |        |        |  +----> failed
  |        |        +-------> waiting -> running
  |        |        +-------> cancelled
  |        +----------------> cancelled
  +-------------------------> cancelled

failed -> ready   (explicit retry/reset action)
waiting -> failed | cancelled
```

`waiting` represents resumable pauses such as approval or external dependencies. Lifecycle changes use `task.transition_to(target)`, which validates the transition and returns a new immutable value preserving the Task ID.

### Plan

Versioned decomposition of a Task into ordered/dependency-linked Steps. Replanning creates a new revision rather than rewriting historical intent.

### Step / Subtask

Executable/delegatable unit in a Plan. Nested work uses `parent_step_id`; dependency edges use canonical Step IDs.

```text
pending -> ready -> running -> succeeded
   |        |        |  +----> failed
   |        |        +-------> waiting -> running
   |        |        +-------> cancelled
   |        +----------------> skipped | cancelled
   +-------------------------> skipped | cancelled

failed -> ready
waiting -> failed | cancelled
```

Lifecycle changes use `step.transition_to(target)`.

### Run

One execution attempt for a Task or Step. Retries create new Run IDs and increment the attempt number.

```text
queued -> starting -> running -> succeeded
  |          |          +-----> failed
  |          |          +-----> cancelled
  |          |          +-----> timed_out
  |          +----------------> failed | cancelled
  +---------------------------> cancelled
```

A Task Run must reference `task_<uuid>`; a Step Run must reference `step_<uuid>`. Backend workflow/job IDs belong only in `external_refs`. `run.transition_to(target)` enforces legal transitions and records start/finish timestamps.

### Agent

Platform-owned reasoning/acting participant. It contains a role, canonical Capability references, an optional canonical Model Assignment reference and structured provider-neutral `policy_requirements`. The latter is a neutral policy/configuration hook and does not select an authorization engine.

### Agent Team

Named grouping of canonical Agents plus immutable coordination metadata. Team membership uses canonical Agent IDs and does not imply one orchestration implementation.

### Artifact

Durable/version-addressable input, output or evidence object. Payload storage is provider-backed, while canonical identity and provenance remain platform-owned.

### Result

Semantic completion outcome for a Task or Run. Result references canonical Artifact IDs and carries immutable structured `status_data` in addition to the high-level outcome.

### Event

Append-only canonical fact about lifecycle or another significant platform action. Events carry a canonical subject, correlation/causation data, optional tracing and ownership/project hooks, payload, provenance and external references.

Event payload/provenance are deeply immutable, including nested arbitrary `Mapping` implementations. The Python Event model requires canonical subject identities.

The external Event contract is explicitly versioned:

- `event.schema.json` / `schema_version="1.0"` preserves the previously published compatibility contract: `subject_type` and `subject_id` remain nonempty strings;
- `event.v2.schema.json` / `schema_version="2.0"` is the strict canonical contract: supported subject types are enumerated and each subject ID must match the corresponding canonical ID type.

New canonical producers should target Event v2. Existing v1 payloads remain valid and can be migrated at API/persistence boundaries without pretending a breaking restriction was additive.

### Approval

Human or policy decision associated with a canonical governed subject.

```text
pending -> approved | rejected | expired | cancelled
```

A Task or Step can remain in `waiting` while an Approval is pending. Approval status changes use the immutable transition API. Approval may also target a canonical `ToolInvocation` when the decision governs one concrete sensitive call rather than the reusable Tool definition.

### Tool

Canonical invokable tool description independent of native, MCP, HTTP or plugin transport. Tool capabilities use canonical Capability IDs.

### Tool Invocation

Canonical identity for one concrete invocation of a Tool. `ToolInvocation` uses `tool_invocation_<uuid>`, references the canonical `tool_<uuid>` definition and carries neutral project/correlation/causation/trace/provenance hooks.

Its purpose in this domain layer is identity and governance: an Approval can point to exactly one invocation. Concrete arguments/results and protocol-specific invocation DTOs remain adapter/contract concerns and may map to this canonical identity.

### Capability

Implementation-neutral descriptor used to match Agent, Tool, Model, Node or Worker requirements. Capability identity is platform-owned and its structured attributes are immutable.

### Policy Scope

`PolicyScope` is a small canonical scope identity used where model assignment must target a policy-defined scope. It has a `policy_scope_<uuid>` identity, owner/project hooks, immutable criteria, provenance and external references.

It intentionally **does not** define the final authorization/policy evaluation model. Its purpose in Issue #4 is to preserve the normative architecture contract that models can be assigned by capability/policy scope without accepting an arbitrary non-canonical string.

### Model Assignment

Versioned association between model requirements/provider selection and one of these canonical targets:

- Agent (`agent_<uuid>`),
- Task (`task_<uuid>`),
- Step (`step_<uuid>`),
- Capability (`cap_<uuid>`),
- Policy Scope (`policy_scope_<uuid>`; `subject_type="policy"`).

This matches the product and architecture principles requiring assignment per agent, task, step or capability/policy scope. Requirements are immutable structured data. Provider-specific handles are not canonical identity.

### Node

Canonical compute device/runtime host. Hardware/vendor/deployment class belongs in immutable metadata and capabilities, not identity.

### Worker

Execution-capable service/process registered on a canonical Node. Workers advertise canonical Capability IDs and availability independently from scheduler implementation.

### Worker Job

Canonical placement/dispatch record connecting one Run to one Worker. It does not duplicate Task or Run semantics.

```text
queued -> assigned -> starting -> running -> succeeded
  |         |           |          +-----> waiting -> running
  |         |           |          +-----> failed
  |         |           |          +-----> cancelled
  |         |           |          +-----> timed_out
  |         |           +----------------> failed | cancelled
  |         +----------------------------> cancelled
  +--------------------------------------> cancelled
```

Worker Job carries canonical Run/Worker IDs plus project, correlation, causation and trace hooks. Its transition API records execution start/finish timestamps.

## Relationship overview

```text
Project
  ├─ Goal -> Task
  │          ├─ Plan -> Step -> Step dependencies
  │          ├─ Run -> Result / Artifact / Event
  │          └─ Approval
  ├─ Agent -> Agent Team
  └─ shared Artifacts / configuration

Run -> Worker Job -> Worker -> Node
Tool -> Tool Invocation -> Approval (when a specific call is governed)
Agent/Task/Step/Capability/Policy Scope -> Model Assignment
Worker/Node/Tool/Agent -> Capability
Agent -> neutral policy requirements
```

Every entity/scope relationship uses canonical IDs. Backend IDs live only in `external_refs`.

## Lifecycle invariants

- canonical IDs never change after creation;
- direct lifecycle-status reassignment is impossible;
- nested canonical value state is defensively deep-frozen;
- lifecycle changes pass through explicit transition tables;
- retries create new Run IDs;
- terminal Runs have no outgoing transitions;
- `waiting` is resumable and distinct from failure;
- Events remain append-only immutable facts;
- Plan revisions preserve history;
- backend identifiers remain non-canonical;
- Worker Jobs reference canonical Runs and Workers;
- approvals for sensitive tool calls can reference one canonical Tool Invocation rather than over-broadly governing the Tool definition.

Executable transition tables live in `src/ai_multi_agent_platform/domain/lifecycle.py`.

## Serialization and schema versioning

Externally visible canonical payloads carry `schema_version`.

- backward-compatible additive changes retain the major version;
- removing/changing required fields, narrowing previously valid enum/value space or changing field semantics requires a new major version;
- adapter/provider versions never replace canonical schema versions;
- readers reject unsupported major versions explicitly;
- migration logic belongs at persistence/API boundaries and preserves canonical identity/provenance;
- Python dataclasses are not the external wire contract.

Task and Run currently use schema `1.0`. Event preserves its published v1 contract in `event.schema.json` and publishes strict canonical subject validation in `event.v2.schema.json` with `schema_version="2.0"`. The shared common schema defines canonical ID formats, including Policy Scope and Tool Invocation, for reuse by later contracts.

## Adapter boundary

Adapters translate canonical contracts to/from backend concepts. Framework session objects, workflow handles, database row types and provider response classes must never become canonical domain objects.

Example:

```text
canonical task_id: task_<uuid>
external_refs:
  orchestrator/run -> backend-run-42
  executor/job     -> backend-job-99
```

Changing adapters changes mappings, not canonical identity.

A protocol-level tool call with its own request/invocation handle maps to a canonical `tool_invocation_<uuid>` when the platform needs stable governance, approval or audit identity. Provider/private call handles remain external references.

## Required validation scenarios

Tests model all Issue #4 scenarios:

1. one Task with one Run and one Artifact;
2. one Task retried through two Runs;
3. one Plan with dependent Steps;
4. one Task paused for Approval;
5. one Run dispatched to a remote Worker/Node through Worker Job;
6. one canonical Task mapped to external orchestrator/executor IDs without identity change.

Additional regression coverage includes malformed IDs, backend IDs in canonical relationships, immutable IDs, lifecycle-bypass prevention, deep immutability for arbitrary Mapping implementations, capability/policy-scoped Model Assignments, per-invocation Approval targeting, structured Result status data, Event v1 compatibility plus strict Event v2 validation, and the no-vendor-import architecture guard.

## Deferred decisions

Persistence, concrete Hermes/Forge mappings, final scheduler implementation, final authorization/policy evaluation semantics, UI and provider-specific runtime integration remain later work. `PolicyScope` is only a canonical targeting primitive for model assignment, and `ToolInvocation` is only the canonical governed-call identity; neither pre-empts those later implementation choices.
