# Issue #4 completion audit

Issue #4 was originally closed after the documentation-focused PR #27 and then strengthened by PR #50. A final post-merge audit found additional invariant gaps that could still allow canonical identity or lifecycle rules to be bypassed. This document records the complete closure state.

## Canonical model coverage

- platform-owned Python definitions exist for every canonical entity requested by issue #4;
- canonical IDs use platform-owned type-prefixed UUID identities;
- canonical IDs are immutable after object creation;
- canonical relationship fields validate the expected canonical ID type;
- backend/provider IDs remain `ExternalRef` values and cannot replace canonical relationship identities;
- ownership/project/provenance hooks exist where applicable;
- Task, Run and Event remain versioned cross-boundary JSON contracts;
- all common canonical ID types are defined in the shared schema vocabulary.

## Lifecycle coverage

- deterministic transition tables exist for Task, Step, Run, Approval and Worker Job;
- `waiting` models resumable pauses such as pending approval or dependencies;
- lifecycle-bearing domain objects cannot have `status` directly reassigned;
- `transition_to(...)` validates the legal transition and returns the next immutable value state;
- Run and Worker Job transitions record start/finish timestamps when entering execution/terminal states;
- terminal Run states have no outgoing transitions.

## Event and execution integrity

- Event is an append-only immutable value object;
- Event payload and provenance details are defensively deep-frozen, including nested mappings/collections;
- Event subjects are canonical entity references both in Python and in the JSON Schema;
- Event exposes optional ownership/project hooks plus correlation, causation and trace metadata;
- Worker Job carries Run/Worker identity plus optional project, correlation, causation and trace metadata.

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
- mutable caller-owned Event data cannot rewrite an existing Event;
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
4. Model Assignment subjects must use canonical Agent/Task/Step IDs.

## Deliberately out of scope

Persistence, concrete Hermes/Forge mappings, final scheduler implementation, final authorization/policy entity design, UI and provider-specific runtime integration remain later work items. Model Assignment therefore targets canonical Agent, Task or Step identities; policy-scoped assignment is deferred until a canonical policy contract exists instead of using an untyped string relationship.
