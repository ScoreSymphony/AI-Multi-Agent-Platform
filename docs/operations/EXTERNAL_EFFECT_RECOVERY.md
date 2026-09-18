# External side-effect recovery

Issue #1154 closes the reliability gap left after #707 for external actions whose provider outcome
is ambiguous after dispatch. The platform does **not** claim exactly-once execution and does not
blindly replay a non-idempotent action after timeout, cancellation, process loss or a lost callback.

## Recovery boundary

The ordinary capability pipeline remains authoritative:

```text
authorization / approval
        |
        v
InvocationStatus.RUNNING
        |
        +-- durable external-effect journal entry
        |
        v
provider dispatch
        |
        +-- acknowledged success ----------> terminal success
        +-- timeout/cancel/failure ---------> classify uncertainty
        +-- process loss before ack --------> classify on startup
```

The journal is created only at the existing `RUNNING` transition, immediately before provider
execution. Policy/approval failures therefore do not create false evidence that an external effect
may have happened.

The journal is recovery evidence, not a second Task/Run lifecycle. Canonical Task, Run, Plan, Step
and Event ownership stays with their existing platform owners.

## Provider-neutral outcomes

The recovery coordinator can represent:

| Disposition | Meaning |
| --- | --- |
| `safe_to_resume` | provider reconciliation proves the same operation is still in progress |
| `safe_to_retry` | replay is supported by evidence; the normal capability path must still be used |
| `reconcile_with_provider` | observe the original provider before deciding |
| `terminal_success` | provider or operator evidence settles the effect as applied |
| `terminal_failure` | provider or operator evidence settles the effect as failed |
| `blocked_dependency` | reconciliation cannot currently reach the owning provider/dependency |
| `uncertain_manual_review` | replay safety cannot be established automatically |
| `blocked_operator_action` | an explicit operator decision is required before progress |

A capability declares `ExternalEffectRecoveryPolicy` independently from compensation. The
declaration separates native idempotency guarantees from read-only reconciliation support.

Automatic retry is permitted only when all of the following hold:

1. the capability declares `ExternalEffectIdempotency.GUARANTEED`;
2. the invocation carries an idempotency key;
3. the retry preserves the same canonical invocation, provider, capability and key.

`PROVIDER_DEPENDENT`, `UNKNOWN` and `NONE` are never treated as guaranteed replay safety by
the platform.

## Reconciliation

Providers that can safely observe an already-dispatched operation may implement
`ExternalEffectReconciler`. Reconciliation receives canonical correlation plus private
provider-recovery metadata and returns only a provider-neutral observation such as `APPLIED`,
`NOT_APPLIED`, `IN_PROGRESS`, `FAILED`, `UNAVAILABLE` or `UNKNOWN`.

Reconciliation itself never dispatches the effect. Repeated reconciliation is therefore safe with
respect to external side effects. A terminal recovery record is returned unchanged on later passes.

The existing distributed Worker runtime remains the owner of Worker dispatch recovery. It already
persists Worker-job ownership before dispatch and reconciles the same `worker_job_id` after lost
acknowledgement. #1154 does not introduce a competing Worker lifecycle.

## Startup behavior

The single-node runtime stores recovery evidence in:

```text
<data-dir>/db/external-effect-recovery.sqlite3
```

On startup, a persisted `dispatching` record means the previous process crossed the provider
dispatch boundary but did not durably acknowledge an outcome. Startup converts that record into
the appropriate provider-neutral uncertainty classification and invokes read-only reconciliation
when the exact provider advertises support.

Unresolved effects do not prevent the Control Plane from opening, because operators need the
diagnostic surface to settle them. The unresolved **effect itself** remains fail-closed: its
canonical invocation cannot be blindly replayed unless it is classified `safe_to_retry`.

## Operator surface

The Control Plane collection is:

```text
external-effect-recoveries
```

Registered commands are:

```text
external-effect.reconcile
external-effect.authorize-retry
external-effect.mark-failed
external-effect.confirm-succeeded
```

The diagnostic resource exposes canonical Task/Run/invocation/capability identity, provider
identity, last status/disposition/reason, reconciliation/dispatch counts and permitted next
actions. It deliberately exposes only whether an idempotency key exists and the namespaces of
adapter metadata. Raw idempotency keys, provider tool references, provider-native identifiers and
invocation payloads are not exposed northbound.

Commands use the existing Control Plane command path, including authorization and mutation
idempotency. `authorize-retry` does not execute the external action. A retry must re-enter the
normal `CapabilityInvoker`, so ordinary policy and approval checks still apply before dispatch.

## Late and duplicate results

Once an ambiguous outcome has moved out of `dispatching`, a late callback does not silently
overwrite manual-review or reconciliation state. It is counted as a prevented duplicate callback.
Provider success can settle such a record only through the explicit reconciliation contract or an
authorized operator confirmation.

## Observability

Recovery transitions reuse #16 telemetry without recording request/response bodies or private
provider recovery values. The runtime emits events for:

- `external_effect.uncertain_outcome_detected`
- `external_effect.reconciliation_started`
- `external_effect.reconciliation_completed`
- `external_effect.reconciliation_failed`
- `external_effect.retry_safe`
- `external_effect.retry_unsafe`
- `external_effect.duplicate_callback_prevented`
- `external_effect.manual_review_required`
- `external_effect.operator_action_applied`

## Failure injection

`ai_multi_agent_platform.testing.FailureInjectingExternalEffectProvider` provides reusable
fault windows for integration/conformance tests. It can apply an effect and then inject a timeout
or provider failure before acknowledgement, while optionally modelling a provider-native
idempotency guarantee.

This fixture is intentionally a provider test double; it does not emulate idempotency in the
platform itself.
