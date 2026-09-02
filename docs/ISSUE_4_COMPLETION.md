# Issue #4 completion audit

This follow-up closes the implementation gaps left after the original domain-model documentation merge.

## Completed here

- platform-owned Python definitions for all canonical entities requested by issue #4;
- deterministic lifecycle tables for Task, Step, Run, Approval and Worker Job;
- canonical UUID validation and platform-owned ID generation;
- strict Run subject identity rules (`task_...` or `step_...` only);
- `waiting` semantics for resumable Task/Step pauses such as pending approval;
- Worker Job as a placement record that references canonical Run and Worker identities;
- correlation, causation, trace, provenance and external-reference hooks;
- schema alignment for Task, Run and Event;
- tests for all six validation scenarios from issue #4;
- architecture test preventing Hermes, Forge or Temporal imports in the core domain layer.

## Review findings addressed

The follow-up also addresses the three findings raised on PR #27:

1. Run subjects can no longer use arbitrary backend IDs.
2. `trace_id` is accepted by Run and Event schemas as documented.
3. Canonical schema IDs validate UUID structure rather than any 36-character hex/hyphen sequence.

## Still deliberately out of scope

Persistence, Hermes/Forge mappings, scheduler implementation, final authorization, UI and provider-specific runtime integration remain later work items.
