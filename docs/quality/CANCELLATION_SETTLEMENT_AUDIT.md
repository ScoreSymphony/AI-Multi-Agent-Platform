# Cancellation, shutdown and settlement audit

This document records the cancellation-sensitive slice of the broad-exception audit. It is scoped to
process signals, `asyncio.CancelledError`, task teardown, and exact-once resource settlement. It does
not attempt the repository-wide `Exception` migration.

## Baseline

The slice was finally revalidated against `main` at
`6b968ac705d160d764022d4f414efa79145b5237`. The commits that landed on `main` after the slice
branch point do not touch the Chat-2-owned production files or tests; the production
`BaseException` inventory relevant to this slice is unchanged.

The project requires Python 3.12 or newer. `asyncio.CancelledError` therefore derives from
`BaseException`, not `Exception`, and must be handled deliberately.

On this baseline there are 14 production `except BaseException` handlers in 13 files. There are no
bare production `except:` handlers and no `suppress(BaseException)` calls. The separate typed
cancellation forms relevant to this slice are:

- `suppress(asyncio.CancelledError)` in Conversation Streaming and Messaging Network;
- one `(Exception, asyncio.CancelledError)` catch in the Hermes adapter;
- explicit `except asyncio.CancelledError` boundaries in Models, Observability, Messaging and
  execution/runtime code.

## BaseException disposition

Line numbers below refer to the baseline commit above. Symbols are the stable review anchor if later
edits shift the numeric line.

| File | Symbol / line | Purpose of catch | CancelledError | KeyboardInterrupt | SystemExit | Settlement invariant | Verdict | Change | Tests | Owner | Residual risk |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `control_plane/conversation_streaming.py` | `_pump_task_events()` ~L170 | Transport an ordinary child-stream failure to the owning aggregator | propagate and make observable to aggregator | propagate directly | propagate directly | failure/completion queue records cannot block or be interrupted by queue capacity | **NARROW** | `BaseException` -> explicit `CancelledError` + marked `Exception`; queue settlement uses `put_nowait`; outer pumps are cancellation-resistantly drained | ordinary failure, `ContractError` cause, child cancellation, process signals, sibling teardown, repeated consumer cancellation, no leaked pumps | Chat 2 | first observed ordinary child failure remains primary; concurrent secondary failures are teardown diagnostics rather than an API-level exception group |
| `deployment/task_budget_bindings.py` | `TaskBudgetRepairRuntime.start_repair()` ~L98 | Release a repair reservation when repair startup does not complete | settle then propagate | settle then propagate | settle then propagate | reservation release is attempted once and completes despite repeated caller cancellation; cleanup failure does not replace the primary throwable | **RESTRUCTURE** | retained justified `BaseException`; shared cancellation-resistant settlement helper | repair cancellation + repeated cancellation; release exactly once | Chat 2 | underlying store/release implementation remains responsible for its own durability semantics |
| `deployment/task_budget_bindings.py` | `TaskBudgetCoordinationBindings._start_with_budget()` ~L262 | Settle claims before ownership transfer; preserve a live parallel claim once a Run starts | settle pre-start claims then propagate; post-start retry failure settles only retry ownership | same | same | every acquired pre-start claim gets one settlement attempt; after successful start the parallel claim remains owned by the active Run until observe/cancel | **RESTRUCTURE** | retained justified `BaseException`; fault-isolated batch settlement; false-start path moved outside catch; successful start transfers parallel ownership before retry reconciliation | ordinary error, cancellation, repeated cancellation, `KeyboardInterrupt`, `SystemExit`, false-start success/failure, exact-once release, post-start retry-reconcile failure | Chat 2 | an exception from the underlying coordinator after it has partially persisted a Run but before returning success remains a coordination/recovery concern rather than a budget-only ownership signal |
| `accounting/async_service.py` | `AccountingPersistenceOffload.run()` L127 | Release pre-submission capacity if `run_in_executor()` fails synchronously | N/A during the synchronous no-`await` submission section | release then propagate | release then propagate | pre-submission failure releases exactly once; accepted worker owns release in `_run_sync(... finally)` | **HANDOFF TO CHAT 5**; recommended KEEP + JUSTIFY | not changed | submission failure, process signal, accepted worker success/failure/cancellation, no double release | Chat 5 | started-worker failure currently wins pending cancellation; owner must confirm that precedence |
| `coordination/async_repository.py` | `CoordinationPersistenceOffload.run()` L136 | same bounded submission settlement | N/A during synchronous submission | release then propagate | release then propagate | same | **HANDOFF TO CHAT 5**; recommended KEEP + JUSTIFY | not changed | same parameterized offload contract | Chat 5 | confirm started-worker failure versus caller-cancellation precedence |
| `decisions/async_persistence.py` | `DecisionPersistenceOffload.run()` L58 | same bounded submission settlement | N/A during synchronous submission | release then propagate | release then propagate | same | **HANDOFF TO CHAT 5**; recommended KEEP + JUSTIFY | not changed | same parameterized offload contract | Chat 5 | helper explicitly documents worker failure winning pending cancellation; test/confirm intentionally |
| `governance/async_persistence.py` | `GovernancePersistenceOffload.run()` L58 | same bounded submission settlement | N/A during synchronous submission | release then propagate | release then propagate | same | **HANDOFF TO CHAT 5**; recommended KEEP + JUSTIFY | not changed | same parameterized offload contract | Chat 5 | helper explicitly documents worker failure winning pending cancellation; test/confirm intentionally |
| `handoffs/async_repository.py` | `HandoffPersistenceOffload.run()` L95 | same bounded submission settlement | N/A during synchronous submission | release then propagate | release then propagate | same | **HANDOFF TO CHAT 5**; recommended KEEP + JUSTIFY | not changed | same parameterized offload contract | Chat 5 | confirm started-worker precedence |
| `learning/async_persistence.py` | `LearningPersistenceOffload.run()` L58 | same bounded submission settlement | N/A during synchronous submission | release then propagate | release then propagate | same | **HANDOFF TO CHAT 5**; recommended KEEP + JUSTIFY | not changed | same parameterized offload contract | Chat 5 | helper explicitly documents worker failure winning pending cancellation; test/confirm intentionally |
| `repositories/async_provenance.py` | `RepositoryProvenancePersistenceOffload.run()` L102 | same bounded submission settlement | N/A during synchronous submission | release then propagate | release then propagate | same | **HANDOFF TO CHAT 5**; recommended KEEP + JUSTIFY | not changed | same parameterized offload contract | Chat 5 | confirm started-worker precedence |
| `security/async_authentication.py` | `AuthenticationPersistenceOffload.run()` L170 | same bounded submission settlement | N/A during synchronous submission | release then propagate | release then propagate | same | **HANDOFF TO CHAT 5**; recommended KEEP + JUSTIFY | not changed | same parameterized offload contract | Chat 5 | confirm started-worker precedence |
| `security/async_authorization_policy.py` | `AuthorizationPolicyPersistenceOffload.run()` L68 | same bounded submission settlement | N/A during synchronous submission | release then propagate | release then propagate | same | **HANDOFF TO CHAT 5**; recommended KEEP + JUSTIFY | not changed | same parameterized offload contract | Chat 5 | confirm started-worker precedence |
| `verification/async_persistence.py` | `VerificationPersistenceOffload.run()` L173 | same bounded submission settlement | N/A during synchronous submission | release then propagate | release then propagate | same | **HANDOFF TO CHAT 5**; recommended KEEP + JUSTIFY | not changed | same parameterized offload contract | Chat 5 | confirm started-worker precedence |
| `workspaces/_retention_async.py` | `WorkspaceRetentionPersistenceOffload.run()` L60 | same bounded submission settlement | N/A during synchronous submission | release then propagate | release then propagate | same | **HANDOFF TO CHAT 5**; recommended KEEP + JUSTIFY | not changed | same parameterized offload contract | Chat 5 | transaction helper explicitly lets worker failure win pending cancellation; test/confirm intentionally |

### Why the offload catches must not be mechanically narrowed

All eleven offload classes acquire bounded capacity before calling `loop.run_in_executor(...)`.
That call is synchronous with respect to coroutine cancellation: there is no `await` between the
capacity acquisition and successful submission. Once a Future has been returned, `_run_sync()` owns
capacity release in a `finally` block. Before a Future exists, the submission catch is the only
settlement owner.

Consequently, replacing `BaseException` with `Exception` would create a real capacity-leak path for
`KeyboardInterrupt`, `SystemExit`, or any other process-level throwable raised during submission.
The catch is legitimate precisely because it immediately releases and bare re-raises; it is not an
error-translation boundary.

Cancellation of an already submitted worker is a separate contract. The current helpers shield the
worker until it settles and in several modules explicitly make worker failure win pending caller
cancellation. Chat 5 must decide and test that precedence per persistence/transaction contract rather
than infer it from the submission catch.

## Confirmed defects in Chat-2-owned code

### Conversation Streaming

1. An independently cancelled pump re-raised `CancelledError`, but its `finally` still emitted the
   normal completion sentinel. The aggregator could therefore observe only completion and finish as
   if that child stream had ended normally.
2. Outer iterator teardown cancelled pumps and then awaited them one by one while suppressing only
   child `CancelledError`. A second cancellation of the consumer during that teardown could
   interrupt the drain and leave pump tasks behind.
3. The pump caught `BaseException`, thereby containing `KeyboardInterrupt` and `SystemExit` long
   enough to route them through an ordinary child-failure queue. That conflicts with process-signal
   preservation and with the existing broad-exception guardrail.

The queue itself is intentionally unbounded, so a queue-capacity deadlock was **not** confirmed.
`put_nowait()` is nevertheless used for pump events/failure/completion so queue settlement has no
cancellation checkpoint and the non-backpressured design is explicit.

### Task-budget settlement

1. A cleanup failure inside either `BaseException` handler could replace the original ordinary
   exception, cancellation, `KeyboardInterrupt`, or `SystemExit`.
2. Retry and parallel claims were released sequentially. If the first release failed or was
   interrupted, the sibling claim was not attempted.
3. Cancellation could interrupt `finally`-based parallel-claim release in observation, active-run
   cancellation and plan cancellation paths. Plan cancellation could also stop after the first
   failed step release.
4. Retry reconciliation happened inside the same pre-start cleanup boundary as dispatch. If the Run
   had already started and retry reconciliation then failed, the cleanup path released the live
   `parallel_steps` claim even though the active Run still owned that capacity slot.

The new settlement helper runs each release in a separately shielded task, observes repeated caller
cancellation, attempts every sibling settlement once, and only then restores the primary control
flow. Ordinary cleanup-task failures are collected after the shielded wait so they cannot skip later
settlements. Secondary cleanup failures are attached as type-only exception notes when a primary
throwable already exists. A successful start now transfers the parallel claim to the active-Run
lifecycle before retry reconciliation; a later retry-reconciliation failure settles only the retry
reservation and leaves parallel release to observe/cancel.

## Chat 5 handoff: Persistence, repository and portability

### Async offload wrappers

For all eleven wrappers above:

**Current behavior**

- capacity is reserved before executor submission;
- failed submission releases capacity and re-raises the exact `BaseException`;
- accepted work releases capacity in `_run_sync(... finally)`;
- caller cancellation after submission is shielded until the worker settles;
- many helpers make worker failure win pending cancellation.

**Required invariant**

- submission failure: exactly one capacity release;
- accepted worker success/failure: exactly one capacity release owned by the worker path;
- caller cancellation must never cause caller and worker to double-release;
- process signals must not be translated to `ContractError`;
- precedence between a pending cancellation and a worker failure must be explicit and tested.

**Recommended implementation**

Keep the submission `BaseException` catch unless a common submission primitive is introduced that
preserves the same all-throwable settlement guarantee. Do not narrow it solely to satisfy the audit.
If common code is extracted in Chat 5, preserve the acquire/submission/worker ownership split.

**Required tests**

- synchronous submission failure after reservation;
- `KeyboardInterrupt` and `SystemExit` during submission release once and propagate;
- accepted worker success releases once;
- accepted worker ordinary failure releases once;
- cancellation while worker runs does not leak or double-release;
- repeated caller cancellation while waiting for started persistence;
- worker failure racing cancellation, with the chosen precedence asserted.

### Portability compensation

`portability/executor.py::ImportExecutor.execute()` currently runs package compensation from an
`except Exception` path. On Python 3.12, `asyncio.CancelledError` bypasses that handler. Cancellation
after one or more successful mutations can therefore bypass the package-level rollback guarantee.

Chat 5 should make mutation compensation a cancellation/process-signal-aware settlement boundary:

- cancellation after an applied mutation triggers reverse compensation, then re-propagates;
- `KeyboardInterrupt` / `SystemExit` after an applied mutation are not converted to platform errors;
- rollback itself is resistant to repeated caller cancellation;
- every required compensation is attempted even if one compensation fails;
- cleanup failures do not silently replace cancellation or a process signal.

Required tests: cancellation before the first mutation, after one mutation, after multiple mutations,
during compensation, compensation failure plus cancellation, process signals after mutation, and no
double compensation.

## Other-owner handoffs

### Chat 3 — Providers, Models, Network and Adapters

- `models/runtime.py` deliberately translates provider-operation `CancelledError` to canonical
  `ContractError(ErrorCode.CANCELLED)`. Verify that this is limited to the model-operation contract
  and cannot turn cancellation of the owning platform task or shutdown into an ordinary failed
  result. Test both direct provider cancellation and caller cancellation.
- `adapters/hermes.py` has the repository's one `(Exception, asyncio.CancelledError)` tuple catch in
  best-effort stop handling. Verify that it contains only expected cleanup-child cancellation and
  never swallows cancellation of the owning operation.
- `messaging/network.py` has typed `suppress(asyncio.CancelledError)`. Verify that suppression applies
  only to child-task teardown; drain and surface unexpected non-cancellation child failures without
  replacing an existing primary failure.

### Chat 4 — Execution, Consumer Retry, Verification and related runtimes

- Generic task-budget runtime wrappers already release admitted reservations in `finally` and have
  cancellation tests. Extend the contract so a release failure or repeated cancellation cannot
  replace/interrupt a primary cancellation and cannot leak the reservation.
- `TaskBudgetEnforcementService.release()` releases multiple reservations sequentially. One failed
  release currently prevents later reservations from being attempted; use fault-isolated settlement
  where multi-reservation atomicity is not provided by the store.
- `messaging/helpers.py::IdempotentConsumer.handle()` releases its idempotency claim and `nack`s on
  cancellation before re-raising. Make those cleanup actions cancellation-resistant and define what
  happens if `release` or `nack` fails, while preserving owning-task cancellation.
- execution subprocess paths that catch `CancelledError` and return a canonical cancelled
  `ExecutionResult` must distinguish task-domain cancellation from cancellation of the owning runtime
  coroutine. Process kill/wait/communicate cleanup must settle without leaking subprocess tasks.

### Chat 6 — Observability and lifecycle

`observability/hierarchy.py` is a good reference shape: it records a `CANCELLED` telemetry outcome
and then re-raises `CancelledError`. Keep that propagation property. Telemetry/exporter cleanup must
not turn cancellation or process signals into ordinary telemetry failures, and shutdown tests should
cover exporter failure while the primary operation is being cancelled.

## Scanner and marker policy

The existing marker syntax is the only syntax used. The two retained Chat-2 `BaseException` catches
are cleanup boundaries that unconditionally bare re-raise after settlement. Conversation Streaming
no longer has a `BaseException` catch; its ordinary `Exception` transport boundary is explicitly
marked. No marker is used to authorize swallowing process signals.

A temporary CI-only inventory test executed `scripts/ci/broad_exception_audit.py` over the entire
production tree and asserted exactly 13 production `BaseException` findings on the branch, all 13
scanner-allowed, with no `suppress(BaseException)` finding. That evidence test passed in the green CI
run for head `e3b555688fccb5475c594126dedfdfd5218ce88a` and was then removed from the final PR state so
this slice does not introduce the final repository-wide ratchet owned by the later audit stage.

## Slice result

Starting production `BaseException` handlers: **14**.

- removed: **0**;
- narrowed out of `BaseException`: **1** (`conversation_streaming._pump_task_events`);
- restructured while deliberately retaining `BaseException`: **2** (Task-budget settlement);
- handed to Chat 5 with a KEEP + JUSTIFY recommendation for submission settlement: **11**;
- production `BaseException` handlers remaining on this branch: **13**.

The remaining 13 are not ordinary error-swallowing boundaries: two are Chat-2 settlement guards and
eleven are pre-submission capacity-settlement guards. The broader repository audit remains open for
the other migration slices and final ratchet work.
