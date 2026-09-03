# Agent Runtime and Agent Teams

Issue: #33

## Purpose

The Agent subsystem gives the platform durable, versioned Agent and Agent Team
resources without making any concrete orchestrator, model provider, tool protocol,
memory backend, or workspace implementation canonical.

The central direction is:

```text
Canonical Agent / Agent Team
            |
            v
   AgentExecutionSpec
            |
            v
 AgentOrchestratorMapper
            |
            v
 private orchestrator runtime
```

Hermes can implement the mapping boundary later, but the canonical Agent model does
not import or encode Hermes session types.

## Stable identity and immutable revisions

`AgentDefinition` and `AgentTeamDefinition` own stable canonical IDs and point at the
latest revision. `AgentRevision` and `AgentTeamRevision` are immutable snapshots.
Updating an Agent or Team creates the next revision rather than mutating history.

Rollback is also revision-producing: rolling back to revision 1 while revision 2 is
current creates revision 3 with the selected historical snapshot. Clone creates a new
canonical identity from an explicit source revision.

Active and historical `AgentRunRecord` objects pin `AgentRevisionRef` and, for team
execution, `AgentTeamRevisionRef`. Updating or disabling the current definition cannot
silently change an already-started run.

## Durable persistence

`AgentRepository` is the persistence boundary. Two reference implementations exist:

- `InMemoryAgentRepository` for deterministic tests and ephemeral compositions;
- `JsonAgentRepository` for durable bootstrap/reference deployments.

`JsonAgentRepository` persists the current Agent and Agent Team definitions, every
immutable historical revision, and every AgentRun record in a versioned JSON document.
Writes use a temporary file followed by atomic replacement. Opening a new repository
instance for the same path replays the persisted history through the normal repository
validation rules, so restart recovery cannot bypass revision or identity invariants.

The persisted format is versioned independently through
`AGENT_REPOSITORY_SCHEMA_VERSION`. A repository with an unsupported schema version is
rejected explicitly rather than being interpreted heuristically.

## Agent profile

An `AgentProfile` contains provider-neutral policy and configuration:

- name, description and role;
- layered instructions through versioned content or references;
- model routing requirements, routing-profile reference, task-override policy and
  fallback policy;
- capability allow/deny rules and required/optional version constraints;
- memory scopes, memory configuration references and knowledge-source references;
- project/workspace defaults;
- authorization and verification policy references;
- resource/runtime hints, enabled state and metadata.

Provider-native model IDs, vector-store IDs, orchestrator session schemas and tool SDK
objects do not belong in this profile.

## Prompt layers

The Agent revision owns the Agent-role instruction separately from platform and project
instruction references. A concrete orchestrator may compose these into its private
prompt/session format, but that composition is an adapter concern.

## Model resolution

`AgentRuntime` resolves the effective `RoutingRequirements` through the existing
platform-owned `ModelRegistry` and `DeterministicModelRouter`.

Routing profiles can supply reusable requirements. Agent-local requirements are merged
monotonically with them. A task-level override is accepted only when the Agent revision
explicitly enables it. The override may strengthen constraints and may select an
explicit canonical model configuration. When an explicit model is unavailable and the
Agent fallback policy is `route`, the runtime retries normal routing while retaining the
remaining constraints.

If an Agent has non-empty model requirements but no Model Registry is attached, the
reference runtime fails before orchestration instead of inventing a provider choice.

## Capability resolution

Capabilities remain canonical capability IDs. The Agent profile may define:

- an allowlist;
- explicit denies;
- required or optional constraints;
- exact versions or compatibility bounds/features;
- approval-policy references.

When a `CapabilityRegistry` is attached, the runtime resolves requested/required
capabilities through it before orchestrator mapping. Without a registry, the reference
runtime can be given an explicit set of available capability IDs for deterministic
tests and bootstrap execution. Missing, denied or incompatible capabilities fail before
execution.

Approval references are retained as Agent capability policy metadata. Actual approval
requirements and decisions remain enforced by the canonical capability invocation and
authorization/approval pipeline; the Agent runtime does not create a second approval
authority or bypass that pipeline.

## Memory and knowledge

Agent revisions carry only provider-neutral access declarations:

- canonical `MemoryScope` values;
- memory configuration references;
- canonical knowledge-source IDs;
- explicit opt-in for user-scoped memory.

`AgentService.ensure_memory_scope` and `ensure_knowledge_source` are the direct policy
hooks used by data integrations. Backend collection names or vector indexes are not
part of the Agent contract.

## Agent Teams

An `AgentTeamRevision` contains exact `AgentRevisionRef` members, member roles,
delegation targets, shared capabilities, coordination-policy references, optional
leader assignment, limits and unavailable-member behavior.

A reviewer is an ordinary Agent member role. The Agent subsystem gives a reviewer no
special completion authority, no policy bypass, and no ability to redefine canonical
Task/Run state. Task completion and verification authority remain owned by their
respective lifecycle/governance layers.

The reference runtime preflights every required team member before persisting team
AgentRun records. Optional members can be skipped only when the Team revision explicitly
uses `skip_optional`.

## Runtime records

`AgentRunRecord` records the exact execution context needed to explain a run:

- canonical Task and Run IDs;
- exact Agent and Team revisions;
- selected canonical model configuration and provider;
- actual capability IDs;
- orchestrator adapter/runtime references;
- status and timing;
- Artifact and Result IDs;
- model-call and tool-invocation references;
- errors, telemetry and verification context.

The orchestrator runtime reference is adapter-owned metadata, not canonical lifecycle
truth.

## Reference execution path

`ReferenceOrchestratorMapper` creates a deterministic runtime mapping without Hermes.
This is intentionally small: it proves that a canonical Agent can be created and
started before any external orchestrator adapter exists.

`AgentOrchestratorMapper` is the replaceable seam. The same Agent revision can be mapped
by multiple mapper implementations without changing its canonical definition.

## Control Plane

`register_agent_control_plane(...)` extends the generic Control Plane registration seam
rather than modifying the #32 foundation.

Registered resources:

- `agents`
- `agent-teams`
- `agent-runs`

Registered mutation commands:

- `agent.create`, `agent.update`, `agent.clone`, `agent.rollback`
- `agent-team.create`, `agent-team.update`, `agent-team.clone`, `agent-team.rollback`

When an `AgentRuntime` is supplied to the composition, the extension also registers:

- `agent.start`
- `agent-team.start`

All mutations use the existing Control Plane idempotency and authorization command seam.
Updates require `expected_revision` to prevent lost updates.

## Deliberate boundaries

The #33 implementation does not:

- make Hermes or another orchestrator canonical;
- let reviewer Agents decide canonical completion;
- embed provider-native model or tool IDs in Agent definitions;
- define a new memory backend;
- bypass authorization/approval rules;
- mutate active runs when a newer Agent revision is created.

Future orchestrator adapters should consume `AgentExecutionSpec` and return an
`OrchestratorMapping`. Production persistence backends may replace the provided JSON
reference store behind the same `AgentRepository` protocol without changing canonical
models, stable identities, revision semantics, or application-service behavior.
