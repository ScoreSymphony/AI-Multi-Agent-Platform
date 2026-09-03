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
`AGENT_REPOSITORY_SCHEMA_VERSION`. Schema v2 adds Team `shared_resource_refs` and
AgentRun capability-version pins. Schema-v1 snapshots are migrated explicitly in memory
to their v2 defaults before validation; unsupported versions are rejected. Writing the
repository always emits v2, so an older binary that only understands v1 rejects the
newer document rather than silently dropping v2 fields on a later save.

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

The Agent revision owns the Agent-role instruction separately from platform and
Project/Workspace instruction references. `project_instruction_refs` is the canonical
Project/Workspace instruction layer; the name is retained for compatibility but the
references may point to instructions scoped at either canonical project or workspace
resources. Task-specific context is supplied separately at execution time through
`AgentExecutionSpec.task_context`, while project/workspace execution context is supplied
through `project_context`.

A concrete orchestrator may compose these layers with tool/provider-generated context
inside its private prompt/session format. Provider-generated prompt state is intentionally
not promoted into the canonical Agent profile.

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

An Agent-specific `approval_ref` is a hard requirement, not descriptive metadata. A
capability constrained by an Agent approval reference can only be prepared when a
canonical `CapabilityRegistry` is attached and the resolved `CapabilitySpec` declares
that same reference in `required_approvals`. This binds the Agent policy to the existing
canonical capability invocation approval path instead of introducing a second approval
authority. If the canonical capability would not enforce the Agent's approval reference,
preflight fails before orchestrator mapping.

The exact capability version selected by the registry is pinned in
`AgentExecutionSpec.capability_versions` and copied into `AgentRunRecord`. Orchestrator
adapters therefore receive the same version that passed compatibility and approval
preflight and must use that version when constructing the concrete capability invocation.
A newer registration cannot silently replace the checked version after preparation.

## Memory, knowledge and authorization hooks

Agent revisions carry only provider-neutral access declarations:

- canonical `MemoryScope` values;
- memory configuration references;
- canonical knowledge-source IDs;
- explicit opt-in for user-scoped memory;
- an optional authorization-profile reference.

`AgentService.ensure_memory_scope`, `ensure_memory_config`,
`ensure_knowledge_source` and `ensure_authorization_profile` are direct enforcement
hooks for integrations that consume these references. Allowed and denied paths are
covered by Agent contract tests. These helpers verify the exact immutable Agent revision;
they do not implement a second memory, knowledge or authorization engine.

Shared Team memory does not introduce a backend-specific `TEAM` memory scope. A Team can
carry a provider-neutral memory configuration or other shared object through
`shared_resource_refs`; `AgentService.ensure_team_shared_resource` verifies the exact
Team revision before that reference is handed to a data integration. The data subsystem's
`MemoryAccessPolicy.team_access` remains the authority for whether the underlying memory
entry/configuration can actually be shared by that Team. Backend collection names,
filesystem paths and vector indexes are never part of the Agent/Team contract.

## Agent Teams

An `AgentTeamRevision` contains exact `AgentRevisionRef` members, member roles,
delegation targets, shared capabilities, provider-neutral `shared_resource_refs`,
coordination-policy references, optional leader assignment, limits and unavailable-member
behavior. Shared resource references remain opaque canonical/integration references; the
Team contract does not embed host paths, provider object handles or orchestrator-private
resource schemas.

A reviewer is an ordinary Agent member role. The Agent subsystem gives a reviewer no
special completion authority, no policy bypass, and no ability to redefine canonical
Task/Run state. Task completion and verification authority remain owned by their
respective lifecycle/governance layers.

The reference runtime preflights every required team member before persisting team
AgentRun records. Optional members can be skipped only when the Team revision explicitly
uses `skip_optional`.

`max_parallel_agents` is a scheduler concurrency ceiling, not a maximum Team-member
count. The reference runtime maps members sequentially and therefore never exceeds any
valid positive parallelism ceiling; it does not reject a Team merely because it has more
members than the limit. `max_steps` is likewise a coordination/scheduling budget rather
than a definition-time member-count rule. Because the reference mapper does not execute
an orchestrator step loop, both limits are carried in the exact pinned Team revision (and
surfaced by the reference mapping metadata) for the concrete orchestrator adapter to
enforce while scheduling real work.

## Runtime records

`AgentRunRecord` records the exact execution context needed to explain a run:

- canonical Task and Run IDs;
- exact Agent and Team revisions;
- selected canonical model configuration and provider;
- actual capability IDs and their registry-resolved versions;
- orchestrator adapter/runtime references;
- status and timing;
- Artifact and Result IDs;
- model-call and tool-invocation references;
- errors, telemetry and verification context.

After creation, the start-time execution identity is immutable: Task/Run IDs, pinned
Agent/Team revisions, selected model/provider, capability IDs and versions, orchestrator
references and `started_at` cannot be rewritten by later AgentRun updates. Lifecycle,
evidence, telemetry, verification and terminal fields may still advance through their
normal service paths.

The orchestrator runtime reference is adapter-owned metadata, not canonical lifecycle
truth.

## Reference execution path

`ReferenceOrchestratorMapper` creates a deterministic runtime mapping without Hermes.
This is intentionally small: it proves that a canonical Agent can be created and
started before any external orchestrator adapter exists.

`AgentOrchestratorMapper` is the replaceable seam. The same Agent revision can be mapped
by multiple mapper implementations without changing its canonical definition. The
execution spec includes pinned capability versions whenever registry resolution was
available and the exact Team revision when invoked through a Team, so adapters cannot
re-resolve a different capability version or silently replace Team scheduling policy
after preflight.

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
Updates require `expected_revision` to prevent lost updates. Agent and Team create,
update, clone and rollback operations write `Provenance(source="control-plane")` with
the calling principal and request/correlation identifiers. Update and clone commands
also preserve or explicitly change owner, project and workspace scope without replacing
the canonical Agent/Team identity.

The start commands expose the portable execution inputs that belong northbound:

- exact Agent/Team revision selection;
- requested canonical capability IDs;
- policy-gated task model override requirements;
- task and project/workspace execution context;
- selection of a server-registered orchestrator adapter by canonical adapter ID.

Concrete mapper objects never cross the API boundary. A composition can register any
number of `AgentOrchestratorMapper` implementations through `orchestrator_mappers`; the
reference mapper remains available as `reference-orchestrator` and remains the default
when no adapter is requested.

Runtime availability and authorization facts are deliberately *not* accepted from the
caller. `available_capability_ids`, `granted_permissions` and
`available_worker_capabilities` are server-resolved through the optional
`AgentExecutionEnvironmentResolver`, which receives the authenticated `RequestContext`.
If a client tries to assert those fields directly, the command fails with
`invalid_request`. This prevents the northbound API from turning caller-controlled JSON
into capability or permission grants while still allowing reference/self-hosted
compositions to derive deterministic availability from trusted platform state.

Authentication and session management remain upstream Control Plane concerns. The #36
authentication layer resolves an authenticated request into the canonical
`RequestContext`; Agent command handlers consume that context for ownership, provenance
and trusted execution-environment resolution but do not define credentials, sessions or
identity-provider semantics. Authentication therefore does not become part of the
canonical Agent/Team profile.

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
