# Canonical Platform Domain Model

This document defines the platform-owned domain model for the AI Multi-Agent Platform. It is intentionally independent from Hermes, Forge, Temporal, LiteLLM, MCP implementations, model providers, storage engines and deployment products.

## Design rules

1. Canonical entities are owned by the platform core.
2. External systems may map to these entities but may not redefine them.
3. Stable platform identifiers survive adapter replacement, retries, process restarts and node changes.
4. Runtime/provider-specific identifiers are stored only as external references.
5. Every execution-relevant entity is traceable through timestamps, ownership and correlation metadata.
6. Multi-user and organization ownership must be representable without changing identifier formats.
7. State transitions are explicit and validated; adapters cannot invent canonical lifecycle states.

## Identifier model

Canonical identifiers are opaque strings with a type prefix and globally unique payload. The initial representation is UUID-based, but consumers must treat identifiers as opaque.

Examples:

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
- `tool_<uuid>`
- `cap_<uuid>`
- `model_assignment_<uuid>`

External identifiers are represented separately as `(system, kind, value)` references and are never used as canonical primary keys.

## Common metadata

Canonical persistent entities should support, where applicable:

- `id`
- `schema_version`
- `created_at`
- `updated_at`
- `owner_ref`
- `project_id`
- `labels`
- `metadata`

Execution and event records additionally support:

- `correlation_id`: groups activity belonging to one logical flow.
- `causation_id`: identifies the event/command/run that directly caused the record.
- `trace_id`: optional observability bridge; it is not a domain identifier.
- `external_refs`: adapter/provider-specific references.

## Ownership reference

`owner_ref` is a neutral reference with `type` and `id`. Initial supported owner types are `user`, `organization`, `team`, and `service`. Issue #15 will define authorization semantics; this model only reserves stable ownership fields now.

## Core entities

### Goal

Represents the desired outcome expressed by a user, application or automation. A Goal may produce one or more Tasks and is not itself an execution record.

### Project

Logical workspace/ownership boundary grouping Goals, Tasks, Agents, Artifacts and related configuration. Project does not imply a filesystem workspace or any specific backend project concept.

### Task

Canonical unit of requested work. Task captures intent and lifecycle independently from a specific orchestrator or executor.

A Task may reference one Goal, one Project, zero or more Plans, Runs, Artifacts and Results.

Initial Task states:

`draft -> ready -> running -> {succeeded | failed | cancelled}`

Additional transitions:

- `draft -> cancelled`
- `ready -> cancelled`
- `failed -> ready` only through an explicit retry/reset command defined by later lifecycle work

### Plan

A versioned decomposition of a Task into ordered and/or dependency-linked Steps. Plans are immutable once activated; revisions create a new Plan version.

### Step / Subtask

A Step is an executable or delegatable unit belonging to a Plan. Nested work is represented by `parent_step_id`; Subtask is therefore a relationship/role of Step, not a separate storage identity.

Initial Step states:

`pending -> ready -> running -> {succeeded | failed | skipped | cancelled}`

### Run

An immutable identity for one execution attempt of a Task or Step. Retries create a new Run rather than mutating execution history into a different attempt.

Initial Run states:

`queued -> running -> {succeeded | failed | cancelled | timed_out}`

A Run identifies its subject (`task` or `step`) and attempt number.

### Agent

A platform-owned agent definition: identity, role, instructions/configuration references and required capabilities. It is not tied to a model provider or agent framework.

### Agent Team

Named composition of Agents plus coordination metadata. Team membership references canonical Agent IDs. A Team is configuration, not an orchestrator implementation.

### Artifact

Immutable or version-addressable input/output produced, consumed or attached during work. Payload storage is delegated to a provider boundary while canonical metadata and provenance remain platform-owned.

### Result

Semantic outcome of a Task or Run. A Result summarizes completion and references zero or more Artifacts. Result is distinct from raw execution output.

### Event

Append-only canonical fact about a state change or significant platform action. Events support recovery, auditability and downstream automation.

Minimum fields include `id`, `event_type`, `occurred_at`, `subject_type`, `subject_id`, `correlation_id`, optional `causation_id`, `payload` and `schema_version`.

Issue #6 will define persistence and replay semantics.

### Approval

Records a requested human/policy decision associated with a Task, Run, Step, Tool invocation or another governed action. Authorization and policy evaluation are deferred to issue #15.

Initial states:

`pending -> {approved | rejected | expired | cancelled}`

### Node

Canonical compute/resource endpoint participating in the platform. Hardware or provider class is metadata, not identity.

### Worker

Execution-capable process/service registered on a Node. Workers advertise capabilities and availability. A Node may host multiple Workers.

### Tool

Canonical tool definition exposed to agents/tasks. Native, MCP, HTTP or plugin implementations live behind adapters.

### Capability

A discoverable functional/property descriptor used for matching requirements to tools, workers, nodes, models or agents. Capability identifiers should remain implementation-neutral.

### Model Assignment

A versioned association between a subject (Agent, Task, Step or policy scope) and model requirements/provider selection. Canonical assignments do not require a specific provider.

## Relationship overview

```text
Project
  ├─ Goal
  │   └─ Task
  │       ├─ Plan (versioned)
  │       │   └─ Step ── parent_step_id → Step
  │       ├─ Run
  │       ├─ Result
  │       ├─ Artifact
  │       └─ Event
  ├─ Agent ── membership → Agent Team
  └─ shared Artifacts / configuration

Task/Step ── Run ── produces → Artifact/Result
Run ── emits → Event
Task/Run/Step/etc. ── may require → Approval
Agent/Task/Step ── may use → Model Assignment
Worker ── runs on → Node
Worker/Node/Tool/Model ── advertises → Capability
```

## Lifecycle invariants

- Canonical IDs never change after creation.
- A retry creates a new Run ID.
- Terminal Runs are immutable except for append-only metadata/evidence references defined by later contracts.
- Task terminal state is decided by platform lifecycle rules, not copied from one backend-specific state string.
- Events are facts and are never rewritten to represent a new outcome.
- Plans are versioned; active plan replacement preserves history.
- Artifacts retain provenance even if their storage backend changes.
- External references may change or multiply without changing canonical IDs.

## Adapter boundary

Adapters receive and return canonical references/contracts. Backend-specific state belongs in adapter-owned translation structures or `external_refs`; framework session objects, database row types, workflow handles and provider response classes must not become canonical domain types.

## Deferred decisions

This issue deliberately does not select database technology, event-store implementation, orchestrator/executor APIs, model gateway, memory/vector backend, file/object storage product, scheduler implementation or authorization engine. Later issues must conform to this domain model rather than reshape it implicitly.
