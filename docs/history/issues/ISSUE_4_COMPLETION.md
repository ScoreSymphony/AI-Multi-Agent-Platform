# Issue #4 completion audit

Issue #4 was originally closed after the documentation-focused PR #27 and then strengthened by PR #50, PR #53, PR #54 and PR #64. A final audit of stale PR #60 showed one useful requirement that had not yet been preserved on `main`: explicit pre-execution validation that a provider Tool Invocation still matches the canonical invocation that was approved. This document records the closure state after preserving that requirement without merging the stale/conflicting PR #60 branch.

## Canonical model coverage

- platform-owned Python definitions exist for every canonical entity requested by issue #4;
- a small canonical `PolicyScope` support type preserves the architecture requirement for policy-scoped model assignment without defining the final authorization engine;
- a canonical `ToolInvocation` support type provides a stable per-call identity so approvals can govern one sensitive invocation rather than the reusable Tool definition;
- canonical IDs use platform-owned type-prefixed UUID identities;
- canonical IDs are immutable after object creation;
- canonical relationship fields validate the expected canonical ID type;
- backend/provider IDs remain `ExternalRef` values and cannot replace canonical relationship identities;
- ownership/project/provenance hooks exist where applicable;
- nested configuration/metadata collections are defensively deep-frozen so immutable value objects cannot be changed indirectly;
- generic Enum values are snapshotted through their recursively frozen `.value`, so mutable enum payloads cannot mutate existing canonical state;
- Agent contains canonical model/capability relationships plus a provider-neutral structured policy-requirements hook;
- Model Assignment targets canonical Agent, Task, Step, Capability or Policy Scope identities as required by the product/architecture principles;
- Result contains canonical Artifact references plus immutable structured `status_data` in addition to its semantic outcome;
- all common canonical ID types, including `policy_scope_...` and `tool_invocation_...`, are defined in the shared schema vocabulary.

## Lifecycle coverage

- deterministic transition tables exist for Task, Step, Run, Approval and Worker Job;
- `waiting` models resumable pauses such as pending approval or dependencies;
- lifecycle-bearing domain objects cannot have `status` directly reassigned;
- `transition_to(...)` validates the legal transition and returns the next immutable value state;
- Run and Worker Job transitions record start/finish timestamps when entering execution/terminal states;
- terminal Run states have no outgoing transitions.

## Event and execution integrity

- Event is an append-only immutable value object;
- Event payload and provenance details are defensively deep-frozen, including nested arbitrary `Mapping` implementations and collections;
- the Python Event model requires canonical subject identities;
- Event schema `1.0` remains backward-compatible with the previously published nonempty subject-type/subject-id contract;
- Event schema `2.0` is the strict canonical-subject contract and validates subject type against the corresponding canonical ID type, including Policy Scope and Tool Invocation;
- Event exposes optional ownership/project hooks plus correlation, causation and trace metadata;
- Worker Job carries Run/Worker identity plus optional project, correlation, causation and trace metadata;
- Provider Contract `2.0` deep-freezes Tool Invocation arguments and exposes `arguments_json()` for detached JSON transport;
- contract-to-domain Tool Invocation mapping records a deterministic SHA-256 digest of the exact governed arguments in immutable provenance;
- `validate_tool_invocation_binding(...)` verifies the current provider invocation handle, tool reference, correlation/causation context, any explicitly supplied owner/project context and argument digest before governed execution;
- reconstructing a new provider DTO with changed arguments or handles therefore does not inherit an earlier approval merely by reusing the same logical call path.

## Validation scenarios

Tests cover every scenario required by issue #4:

1. one Task with one Run and one Artifact;
2. one Task retried through two Runs;
3. one Plan with dependent Steps;
4. one Task paused for Approval;
5. one Task/Run executed on a remote Worker/Node through Worker Job;
6. one canonical Task mapped to external orchestrator-run and executor-job IDs without changing canonical identity.

Regression tests additionally prove that:

- malformed UUID payloads are rejected;
- backend IDs cannot be used as Run, Event, Approval or Model Assignment canonical subjects;
- relationship fields reject provider/database IDs where canonical IDs are required;
- canonical IDs cannot be reassigned after creation;
- direct status assignment cannot bypass transition rules;
- caller-owned nested metadata/configuration cannot mutate canonical value state after construction;
- mutable caller-owned Event/provenance data, including nested non-`dict` mappings, cannot rewrite an existing Event;
- mutable-valued generic Enum members cannot mutate an already-created canonical Event because their values are recursively snapshotted;
- Agent policy requirements are preserved as provider-neutral immutable structured data;
- Result structured status data is preserved immutably;
- Capability- and Policy-scoped Model Assignments use canonical IDs;
- an Approval can target one canonical `tool_invocation_...` identity and rejects backend invocation IDs;
- a governed Tool Invocation accepts the exact mapped provider call and rejects reconstructed calls with changed arguments, provider handles or governance context;
- Event v1 continues accepting its historical subject contract while Event v2 enforces strict canonical subject relationships;
- Provider Contract `2.0` is explicitly required for immutable Tool Invocation argument semantics;
- the core domain package imports no Hermes, Forge or Temporal types.

## Review findings addressed

PR #27 findings:

1. Run subjects can no longer use arbitrary backend IDs.
2. `trace_id` is accepted by Run and Event schemas as documented.
3. Canonical schema IDs validate UUID structure rather than any 36-character hex/hyphen sequence.

PR #50 findings:

1. canonical IDs cannot be reassigned after construction;
2. lifecycle status cannot be mutated around transition tables;
3. append-only Event payload data is deeply immutable;
4. Model Assignment subjects use canonical IDs.

PR #53 first Codex pass findings:

1. capability/policy-scoped Model Assignments are preserved through canonical Capability and Policy Scope targets;
2. mutable fields on otherwise frozen entities are defensively deep-frozen;
3. `_deep_freeze` handles the declared `Mapping` interface rather than only concrete dictionaries.

PR #53/#54 findings:

1. per-invocation approval targeting is preserved through canonical `ToolInvocation` identities;
2. the stricter Event subject contract is published as schema `2.0` instead of silently narrowing the existing `1.0` public contract;
3. canonical Policy Scope and Tool Invocation semantics are recorded in ADR 0001;
4. provider-neutral Tool Invocation DTOs are connected to canonical identity through an explicit mapping boundary.

PR #64 findings:

1. Tool Invocation arguments are defensively frozen so source-object mutation cannot change the governed snapshot;
2. the canonical mapping records a deterministic argument digest;
3. generic Enum values are recursively snapshotted rather than retained as mutable-bearing objects;
4. immutable Tool Invocation semantics are correctly published as Provider Contract `2.0`;
5. `arguments_json()` supplies a supported JSON-safe transport representation.

Stale PR #60 audit:

1. its conflicting/custom workflow and pre-Contract-2.0 DTO implementation are superseded and must not be merged;
2. its useful pre-execution binding concept is preserved independently as `validate_tool_invocation_binding(...)` on top of the current Contract `2.0` implementation;
3. the validator closes the reconstructed-DTO gap by comparing provider handles, context and the current SHA-256 argument digest against the mapped canonical Tool Invocation.

## Deliberately out of scope

Persistence, concrete Hermes/Forge mappings, final scheduler implementation, final authorization/policy evaluation model, UI and provider-specific runtime integration remain later work items. `PolicyScope` supplies a stable platform-owned target for model assignment without defining policy evaluation/enforcement, and `ToolInvocation` supplies a stable governed-action identity plus a provider-neutral validation primitive without selecting a concrete tool transport or final authorization engine.
