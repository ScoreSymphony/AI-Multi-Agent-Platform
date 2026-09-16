# #983 runtime / execution error-boundary audit

Status: focused runtime/execution slice for #983 on `codex/983-runtime-execution-boundaries`.

This document records the classification and migration decisions for the runtime-owned scope:

- Tools / Capabilities;
- Workspaces;
- Execution;
- Workers / distributed delivery;
- Retry;
- Evaluation;
- Templates;
- Plugins;
- Verification runtime/recovery.

It does not replace `docs/quality/ERROR_BOUNDARY_AUDIT.md`. `ContractError` / `ErrorCode` remain the
single canonical cross-boundary taxonomy.

## Scope boundaries

Already completed elsewhere and deliberately not reworked here:

- repository-wide inventory / scanner foundation;
- process-control, cancellation, shutdown and generic settlement foundation;
- persistence / repository async-offload / portability ownership review.

Parallel #983 slices own:

- provider / model / network / external adapter root-cause translation;
- northbound Control Plane / API / ASGI / lifecycle / notifications / observability / HA.

The final repository-wide ratchet and acceptance pass remain follow-up work after the parallel slices
converge. #983 remains open and is not completed by this slice.

## Classification summary

| Area / file | Broad-boundary role | Decision |
| --- | --- | --- |
| `execution/reference.py` | outer `ExecutionResult` owner | **FIX + KEEP boundary** |
| `evaluation/evaluators.py` | evaluator-local result containment | **FIX + KEEP boundary** |
| `evaluation/runner.py` | cancellation-resistant durable run/case settlement | **FIX + KEEP cleanup** |
| `plugins/registry.py`, `plugins/manifest.py`, `plugins/_settlement.py` | lifecycle compensation, health containment and schema translation | **FIX + KEEP cleanup/boundary** |
| `templates/*` + `_settlement.py` | template transaction compensation | **FIX + KEEP cleanup** |
| `workspaces/reference.py` | partial local materialization cleanup | **FIX + KEEP cleanup** |
| `workspaces/retention.py` | per-workspace retention batch containment | **KEEP + JUSTIFY** |
| `distributed/transport.py` and workspace distributed adapters | Worker delivery / transport translation | **FIX + KEEP boundary/translation** |
| `application_distribution/distributed_execution.py` | application-build distributed lifecycle translation | **FIX + KEEP boundary/translation** |
| `automation/service.py`, `automation/runtime.py`, `automation/retry_policy.py` | TaskCreator containment and automatic retry classification | **FIX + KEEP boundary** |
| `capabilities/invocation.py` | capability/provider invocation boundary | **FIX + KEEP boundary** |
| `verification/reviewer_recovery.py` | per-Verification startup recovery containment | **FIX + KEEP boundary** |

No new platform error hierarchy is introduced.

## Execution

### Reference executor

`ReferenceExecutor.execute()` intentionally owns the outer `ExecutionResult` contract. Unknown
ordinary exceptions therefore remain contained there, but the previous raw `str(exc)` diagnostic is
removed. Unexpected implementation faults now produce a stable internal execution failure without
copying implementation exception text into the result.

Canonical `CancellationToken` cancellation is separated from actual `asyncio` task cancellation via
an internal sentinel. Task-level cancellation is no longer normalized into an ordinary
`ExecutionResult`; only the platform-owned cancellation token produces the canonical cancelled
result.

Child sleep/cancellation waiters are cleanup resources and are cancelled in `finally` without taking
authority over caller task cancellation.

Verdict: **boundary catch retained and made content-safe; task cancellation authority restored.**

### Distributed application-build lifecycle

`DistributedApplicationBuildLifecycleBackend` is the application-specific lifecycle boundary between
a canonical build Run and the distributed Worker runtime. Known scheduler, authorization, registry
and canonical `ContractError` failures retain their typed semantics.

The start path keeps one deliberately broad catch because dispatch may fail after ownership has
already become uncertain. The boundary first inspects the canonical dispatch record:

- `LOST` plus `dispatch_outcome_unknown` remains `UNAVAILABLE` and retryable because reconciliation
  can resolve the original dispatch outcome;
- every other unknown implementation failure becomes non-retryable `BACKEND_ERROR` with a stable
  message and the original exception retained only as the internal cause.

Result retrieval follows the same fail-closed rule. Typed `RegistryError` and `ContractError`
semantics are preserved; an otherwise unknown result-provider exception becomes non-retryable
`BACKEND_ERROR` rather than being mislabeled as transient `UNAVAILABLE`.

Verdict: **FIX + KEEP boundary/translation; only evidence-backed uncertain ownership remains
retryable, while unknown programming/backend failures are terminal and content-safe.**

## Evaluation

### Evaluator containment

Evaluator implementations are explicitly local fault domains: one evaluator failure must become an
`EvaluationResult` error rather than aborting unrelated evaluator execution. The broad catches in
`evaluate_safely()` and the resource-limit evaluator are therefore true result boundaries.

Their public/durable diagnostic no longer copies `str(error)`. It records only a stable message plus
the exception type.

Verdict: **boundary catches retained; unexpected exception text redacted.**

### Suite runner settlement

`EvaluationRunner.run_suite()` owns the durable transition of an already-persisted RUNNING run. Its
outer settlement handler intentionally catches `BaseException` so cancellation or process-control
signals cannot abandon the run in RUNNING state after mutation has begun.

The FAILED persistence operation is executed through a shielded settlement helper that survives
repeated caller cancellation. A secondary settlement failure is attached only as an exception note,
and the original throwable is always re-raised. The handler therefore does not translate
`CancelledError`, `KeyboardInterrupt` or `SystemExit` into ordinary evaluation failure semantics.

Case teardown uses the same ownership principle after isolation setup. Teardown settlement survives
repeated cancellation; a secondary teardown failure cannot replace a cancellation/process-control
throwable, and ordinary teardown failures are classified separately from case-execution failures.
The teardown logic is isolated in a dedicated helper so the main attempt path remains inside the
repository maintainability limits.

Existing readiness coverage proves that failures after run persistence cannot leave a run falsely
`COMPLETED`, while failures before run persistence do not manufacture incomplete run state.

Verdict: **FIX + KEEP cleanup; `BaseException` is justified solely by durable/owned settlement and
the primary throwable is unconditionally re-raised.**

## Plugins

Plugin enable/disable owns a multi-step mutation: runtime startup/shutdown plus extension binding.
The outer compensation boundary now includes `BaseException` because cancellation or process-control
signals can arrive after partial mutation. Compensation completes best-effort and the original
throwable is re-raised; it is never converted into ordinary plugin failure.

Asynchronous rollback steps are settled through a shielded helper, so a second caller cancellation
during unregister/register/shutdown cannot abandon the remaining rollback. Nested broad catches are
cleanup-only: one rollback failure must not prevent the remaining owned compensation steps. Health
refresh remains an explicit plugin-local boundary and reports `UNAVAILABLE` with a stable redacted
detail.

Plugin JSON Schema boundaries are now content-safe as well. Runtime configuration validation no
longer appends `jsonschema`'s raw `ValidationError.message`; it retains only the stable plugin ID.
Serialized manifest validation retains only a schema path such as `extensions.0.extension_type`,
never the rejected value. In both cases the original `ValidationError` remains available through the
internal exception cause.

Verdict: **KEEP cleanup/boundary; preserve rollback ownership, survive repeated cancellation and FIX
plugin schema diagnostic leakage while retaining safe identity/path context.**

## Templates

Template application/export operations have transactional compensation obligations. The shared
`settle_awaitable()` helper shields a started compensation task from repeated caller cancellation,
waits until the cleanup operation settles and returns any cleanup failure to the transaction owner.
It does not replace the primary failure.

Template call sites use this helper for rollback/restore paths so caller cancellation cannot abandon
an already-started mutation cleanup.

Verdict: **KEEP cleanup; cancellation-resistant settlement added.**

## Workspaces

### Local reference materialization

A failed or cancelled materialization may leave a partially created local tree. The cleanup catch is
therefore intentionally `BaseException`: it restores writability, removes the partial tree and
unconditionally re-raises the original throwable. Best-effort filesystem cleanup failure does not
replace that primary throwable.

Verdict: **KEEP cleanup; `BaseException` explicitly justified by partial-resource ownership.**

### Retention enforcement

`RetentionManagedWorkspaceProvider.enforce_retention()` is intentionally a per-workspace batch
boundary. One workspace/guard/persistence failure must not prevent retention evaluation for all
remaining workspaces. Failure is not silent: the workspace ID is returned in
`WorkspaceRetentionReport.failed_workspace_ids`.

The handler catches only `Exception`, so task cancellation and process-control signals are not
normalized into a failed workspace result.

Verdict: **KEEP + JUSTIFY as observable batch containment.**

## Workers and distributed transport

Distributed Worker delivery is an outer ownership boundary because every received delivery must
settle exactly once as ACK or NACK. Unknown ordinary failures remain contained there, but redelivery
now fails closed:

- typed `ContractError.retryable` is preserved for operational Worker transport failures;
- narrow built-in timeout/connection failures are retryable before a typed adapter boundary exists;
- unknown exceptions are non-retryable;
- workspace transport preserves canonical `ErrorCode` + retryability for `ContractError`;
- malformed registry/value/type failures map to `CONTRACT_VIOLATION`;
- unknown workspace failures map to non-retryable `BACKEND_ERROR`.

Remote workspace replies are translated back into canonical platform errors rather than leaking
transport-private failures. Safe error messages are used at the transport boundary.

Verdict: **boundary/translation retained; retry semantics made explicit and fail-closed.**

## Retry / Automation

Generic `BACKEND_ERROR` is no longer automatically retryable merely because it is a backend-shaped
category. A boundary may mark a generic backend/custom category retryable only when it has concrete
knowledge that the failure is transient and replay is safe.

Stable canonical failures cannot be made retryable by a stale or buggy hint. In particular
cancellation, invalid request/configuration, unsupported capability, routing/not-found,
input/provider-response validation, conflict, authorization, permanent failure and contract
violation remain terminal for an unchanged delivery even if older evidence carries
`retryable=True`.

The same fail-closed principle is used by canonical Event cursor processing. `BACKEND_ERROR` remains
terminal by default there, while a genuinely typed operational `ContractError` may carry explicit
retryability.

The `AutomationService._process()` TaskCreator catch-all remains an explicit delivery boundary because
a failed task creation must settle the durable delivery record. Unknown TaskCreator implementation
failures are now persisted with a stable exception-type-only diagnostic, are marked non-retryable,
and receive no retry deadline. Raw `str(exc)` content is not copied into the delivery record.

The legacy `automation_task_creation_failed` catch-all code is also forced terminal by the retry
classifier even if an older caller supplies a retry hint. This prevents an unknown
TaskCreator/programming failure from being re-executed automatically as if it were a known transient
operational failure.

Known transient categories such as timeout, unavailable, rate limit, resource exhaustion and
`TRANSIENT_FAILURE` retain retry behavior.

Verdict: **FIX + KEEP boundary; stable/cancellation failures remain terminal, unknown implementation
failures fail closed and durable diagnostics are content-safe.**

## Capability / tool invocation

`CapabilityInvoker` is the owning provider invocation boundary. Its unknown ordinary-provider catch
is intentionally retained and classified as a boundary. It maps to canonical non-retryable
`BACKEND_ERROR`, preserves the internal cause and emits only the stable capability/provider context.

Timeout remains explicitly retryable and existing canonical `ContractError` instances pass through
without reclassification. The established capability-level `ContractError(CANCELLED)` translation is
retained in this slice; the Automation retry classifier guarantees that canonical `CANCELLED` stays
terminal even if legacy invocation evidence carries a retryable hint.

JSON Schema validation previously appended `jsonschema`'s raw `exc.message` to the canonical error.
Validation messages can include rejected input or provider-output values, so the translated error is
now content-safe. It retains only input/output stage and capability identity while preserving the
original validation exception as the internal cause.

Verdict: **KEEP provider boundary; FIX schema diagnostic leakage and prevent cancellation from
becoming automatic retry work.**

## Verification startup recovery

Automatic reviewer startup reconciliation is a deliberate per-Verification fail-closed boundary. A
single broken reviewer obligation or unavailable canonical Task must become a `BLOCKED` recovery
record without aborting reconciliation of all durable requests.

The boundary remains broad for that reason, but durable recovery evidence no longer embeds
`str(exc)`:

- `ContractError` records only the canonical `ErrorCode`;
- other ordinary exceptions record only the exception type;
- raw provider/runtime/request payload text is not persisted in `ReviewerRecoveryRecord.reason`.

Verdict: **KEEP boundary; FIX durable diagnostic leakage.**

## Representative regression coverage

This slice adds or strengthens coverage for:

- real task cancellation versus canonical execution-token cancellation;
- redaction of unexpected executor failures;
- evaluator-local unexpected failure containment and redaction;
- durable Evaluation run/case settlement across failure/cancellation paths;
- retry classification for generic backend, stable/cancelled and legacy Automation catch-all
  failures;
- Automation TaskCreator catch-all redaction and terminal settlement;
- Automation Event cursor `BACKEND_ERROR` fail-closed behavior with explicit retryability override;
- distributed Worker/workspace typed error preservation and fail-closed redelivery;
- distributed application-build unknown dispatch/result containment and terminal retryability;
- template/plugin/workspace compensation under repeated cancellation;
- plugin configuration/manifest schema diagnostic redaction;
- capability input/output schema diagnostic redaction;
- reviewer startup recovery diagnostic redaction.

Existing workspace retention behavior provides explicit failed-workspace result evidence.

## Cross-slice handoffs

### Provider / model / network adapters

Root SDK exception families, provider-specific timeout/rate-limit/network classification and external
adapter translation belong to the parallel provider/adapter #983 slice. This slice consumes
canonical `ContractError` semantics at runtime boundaries and does not duplicate SDK ownership.

### Northbound / Control Plane

HTTP/ASGI serialization, lifecycle/notification containment, telemetry exporter policy and public
error-envelope stability belong to the parallel northbound #983 slice. Runtime code here avoids raw
diagnostic leakage so the northbound layer does not need to sanitize implementation-private payloads
again.

### Persistence / offload

Repository persistence and bounded async-offload settlement were already audited in the dedicated
persistence/portability slice. This slice does not rework those ownership helpers, including the
Evaluation, Verification and Workspace async-persistence implementations.

### Final #983 acceptance

A later convergence pass still owns:

- final repository-wide broad-catch inventory against the merged slices;
- baseline-aware CI/quality ratchet acceptance;
- confirmation that all required checks are green;
- the final decision on #983 after every parallel slice has converged.

## Slice result

Within the runtime/execution ownership slice:

- unknown execution/provider/worker/Automation/plugin/application-build implementation diagnostics
  are not exposed as raw exception strings through the changed boundaries;
- generic implementation/backend failures are not implicitly treated as retryable;
- stable contract/configuration/authorization/cancellation failures cannot be made retryable by a
  stale hint;
- canonical typed errors are preserved where they already exist;
- true batch/result boundaries remain broad only where the owner must contain one unit of work;
- cleanup boundaries preserve the primary failure and do not steal cancellation/process-control
  authority;
- `BaseException` catches are limited to explicit compensation/resource/durable-settlement ownership
  and always re-raise the primary throwable after settlement;
- no competing northbound error taxonomy was introduced.

#983 remains open after this slice.
