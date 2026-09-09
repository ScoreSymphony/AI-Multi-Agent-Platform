# Compensation and reversibility

Issue #596 adds provider-neutral compensation semantics for completed external side effects. It does
not add transactional rollback and it does not mutate historical Task, Plan, Step, Run or
ToolInvocation records.

## Authorities

The compensation subsystem is deliberately narrow:

- the capability registry and `CapabilityInvoker` remain the only execution path for compensating
  capabilities;
- the #15 authorization/Approval hooks attached to `CapabilityInvoker` remain the security
  authority;
- the durable #384 Plan/Step coordinator remains the lifecycle authority;
- connector/provider-native resource identifiers remain evidence references rather than canonical
  platform entity types;
- optional #86 verification runs after the compensating invocation and does not redefine its
  result.

`PlanCompensationHooks` is therefore called only after a canonical failure or cancellation has
already been committed. It reacts to that signal but never advances or rewrites Plan state.

## Reversibility metadata

`CapabilitySpec.reversibility` declares one of:

- `reversible`;
- `partially_reversible`;
- `irreversible`;
- `unknown`.

A fully reversible capability must carry a `CompensationDescriptor`. Irreversible and unknown
capabilities may not carry fabricated compensation support. A partially reversible capability may
carry a descriptor only for the supported subset; otherwise an attempted compensation becomes an
explicit `not_compensable` operator state.

The descriptor records the compensating capability, required original evidence, optional time
window, side-effect classification, Approval expectation, idempotency guarantee and known
limitations. Runtime execution still resolves the referenced capability through the ordinary
registry.

## Explicit compensation groups

Compensation is never inferred from arbitrary Plan topology. A `CompensationGroup` binds an exact
Task, Plan revision and optional Project to one explicit policy. Every `CompletedSideEffect`
retains its original execution order, dependency links, capability identity, arguments,
compensation inputs and external/evidence references.

Registration fails if an action does not belong to the group's exact Task/Plan revision or if it
claims a dependency that has not completed inside that group. This prevents unrelated completed
work from being pulled into a recovery sequence.

Automatic behavior defaults to `never`. A group can opt into downstream-failure compensation or
failure-and-cancellation compensation. A newer Plan revision is rejected unless the group policy
explicitly permits it.

When a group is compensated, actions execute in deterministic reverse dependency order. For
otherwise independent actions, their original execution order provides the stable tie-breaker.
`stop_and_escalate` halts the sequence on the first non-success; `continue_and_escalate` continues
through the explicit group while preserving each visible failure.

## Request identity and duplicate delivery

Every `CompensationRequest` has a stable compensation ID and idempotency key. The reference
coordinator derives an automatic key from group, action and Plan revision. Trigger changes do not
create a second default compensation identity for the same completed side effect, so a downstream
failure followed by cancellation or manual recovery cannot repeat an already-requested undo. Callers
may provide a stronger external key where needed. Reusing one key for a different immutable
compensation target is rejected with `conflict` instead of silently aliasing the requests.

The hardened coordinator also recognizes the trigger-suffixed default keys written by the initial
#596 implementation. An upgraded durable store therefore reuses the historical request rather than
creating a second external undo. If more than one legacy request already exists for the same
immutable target, recovery fails closed with `conflict` and requires manual reconciliation instead
of guessing which historical side effect is authoritative.

For a descriptor whose provider idempotency is guaranteed, the same key is propagated through
`OperationControl` with `RetryMode.IDEMPOTENT`. Other providers receive `RetryMode.NEVER`; the
platform does not promote an unknown provider guarantee into retry safety.

A terminal compensation result is returned on duplicate execution instead of invoking the external
provider again.

## Crash, restart and expiry safety

Before the provider call starts, the coordinator persists a `running` result with the canonical
execution Task/Run/Agent and invocation identity. This creates an intentionally conservative crash
boundary.

If the process restarts while a result is `running`, the coordinator **does not call the provider
again blindly**. It invokes an optional `CompensationReconciler` that checks provider evidence. The
outcome becomes one of:

- `succeeded` when evidence proves the compensation happened;
- `failed` when evidence proves it did not succeed;
- `reconciliation_required` when the outcome remains ambiguous.

Without a reconciler, the state becomes `reconciliation_required` and requires operator action.
The SQLite reference repository proves this state survives process restart.

A declared `window_seconds` is checked when the request is created **and again immediately before
provider invocation**. This second check covers queued, restarted and Approval-delayed requests. A
request whose compensation window expired while waiting becomes `expired` and does not reach the
external provider.

## Approval and authorization

A compensating action does not inherit authorization from the original action. The coordinator
constructs a fresh canonical capability invocation with the compensation execution Task/Run/Agent
context. The configured `CapabilityInvoker` then applies its ordinary policy hook, canonical
ToolInvocation binding and Approval hook.

`CompensationDescriptor.requires_approval` and
`CompensationPolicy.require_human_approval` strengthen that individual invocation through the same
ordinary `CapabilityInvoker` Approval path. They do not create a compensation-private approval
mechanism and cannot weaken `CapabilitySpec.required_approvals` or a policy decision that already
requires Approval.

If #15 requires Approval, the invocation is not sent to the provider. The compensation result is
`approval_required` and retains the canonical compensating ToolInvocation linkage exposed by the
invoker. A later retry of the same compensation request may proceed only when the ordinary #15
Approval hook accepts that exact action. Policy denial is persisted separately as `denied`.

## Verification

`CompensationVerificationHook` is a narrow optional seam for #86. It runs only after a successful
compensating invocation and may attach a verification reference. Verification is intentionally
separate from provider execution so it cannot turn an external side effect into hidden adapter
cleanup.

## Operator projection

`CompensationCoordinator.projection(group_id)` joins each immutable completed action with the
persisted request/result state that operators must act on. Under normal history this is the latest
request. If an upgraded store contains conflicting canonical and historical request identities, an
unresolved `reconciliation_required` result with manual intervention takes precedence over a newer
sibling success so the conflict cannot disappear from the read model.

The projection exposes original/compensating linkage, current status, evidence and
`manual_intervention_required` without altering historical execution records. The Control Plane
projection applies the same fail-closed visibility rule. These are the read models intended for
Control Plane/UI/CLI surfaces, and they include irreversible and unknown actions rather than hiding
them.
