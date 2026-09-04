# Automation portability

Issue #79 treats an Automation as portable canonical configuration, not as a transferable scheduler process.

## Portable definition

The `automation` resource preserves canonical durable configuration:

- Automation canonical ID and revision;
- name and description;
- explicit `IdentityContext`;
- Trigger definition, including timezone and schedule/event/webhook configuration;
- Task template and canonical Project/Workspace references;
- deduplication strategy;
- retry policy;
- overlap policy;
- durable lifecycle state and invalidation metadata where applicable;
- timestamps that describe the configuration revision.

Canonical Project and Workspace references are declared as resource dependencies and rewritten through the accepted `ImportContext` mapping.

For webhook Automations, `TriggerDefinition.verification_ref` is a required `secret` dependency. The reference is portable; the credential value is not. Import therefore still requires destination-side secret resolution and normal authorization.

## Runtime state is not portable

The package deliberately excludes:

- `last_evaluated_at`;
- `next_evaluation_at`;
- TriggerDelivery rows and dedupe history;
- delivery processing/retry timestamps and errors;
- generated Task IDs associated with source deliveries;
- scheduler queues, leases, timers or backend-native scheduling state.

`automation_runtime_exclusions()` records scheduler progress and TriggerDelivery state in the package exclusion report as `backend_runtime_state`.

This prevents migration from replaying missed source-side occurrences or importing a second scheduler cursor.

## Safe activation semantics

An Automation that was `enabled` on the source installation is materialized as `paused` on the destination. It cannot create Tasks until an authorized destination-side action explicitly resumes it.

A source Automation that is already `paused`, `disabled` or `invalid` remains non-running. Invalid Automations preserve their categorical invalidation metadata because that metadata is part of the canonical lifecycle contract.

When a paused imported Automation is later resumed, the ordinary #18 lifecycle computes destination scheduler state. The import never restores the source `next_evaluation_at` value.

## Identity and privacy

Automation import preserves `IdentityContext`; it does not silently rewrite delegated authority.

`AutomationImportMutationHandler` therefore requires the destination identity to match the imported identity by default. `AutomationImportPolicy.allow_identity_transfer` is an explicit low-level exception for callers that have already obtained the required authorization. It does not itself grant permissions or bypass the normal authorization boundary.

## Rollback

Package-wide rollback uses `AutomationRepository.remove_automation_if_unused()` as a narrowly scoped storage-compensation seam.

The operation is not a new user-facing Automation lifecycle action. It is guarded by a hard invariant: an Automation with any TriggerDelivery history cannot be removed by this seam. Both the in-memory and SQLite reference repositories enforce the invariant.

Therefore a failed portable import can remove a newly inserted, never-run Automation while regular Automation history remains protected.

## Non-goals

Automation portability does not:

- replay TriggerDelivery history;
- resume source schedules automatically;
- transfer plaintext webhook secrets;
- transfer scheduler implementation state;
- bypass canonical Task creation, authorization, deduplication or observability;
- redefine the normal pause/resume/disable/invalid lifecycle from issue #18/#241.
