# #983 persistence, offload and portability exception audit

Status: focused Chat 5 slice for #983, based on `main` at
`b54708094062730424a8621bbd85e0906189bb19`.

This note is evidence for the repository-wide broad-exception migration. It does not replace
`docs/quality/ERROR_BOUNDARY_AUDIT.md` or the canonical `ContractError` / `ErrorCode` taxonomy.

## Scope

This slice re-audits the persistence/repository offload sites handed off by the cancellation and
settlement review, plus the portability import compensation path.

Python support is `>=3.12`. `asyncio.CancelledError` is therefore outside `Exception`, and process
control (`KeyboardInterrupt`, `SystemExit`) must never be normalized into ordinary persistence or
backend failures.

## Production `BaseException` offload findings

The following 11 production handlers have the same ownership shape and are intentionally retained.
Their verdict is **KEEP + JUSTIFY / cleanup boundary**.

| File | Owner | Verdict |
| --- | --- | --- |
| `accounting/async_service.py` | `AccountingPersistenceOffload.run()` | KEEP + JUSTIFY |
| `coordination/async_repository.py` | `CoordinationPersistenceOffload.run()` | KEEP + JUSTIFY |
| `decisions/async_persistence.py` | `DecisionPersistenceOffload.run()` | KEEP + JUSTIFY |
| `governance/async_persistence.py` | `GovernancePersistenceOffload.run()` | KEEP + JUSTIFY |
| `handoffs/async_repository.py` | `HandoffPersistenceOffload.run()` | KEEP + JUSTIFY |
| `learning/async_persistence.py` | `LearningPersistenceOffload.run()` | KEEP + JUSTIFY |
| `repositories/async_provenance.py` | `RepositoryProvenancePersistenceOffload.run()` | KEEP + JUSTIFY |
| `security/async_authentication.py` | `AuthenticationPersistenceOffload.run()` | KEEP + JUSTIFY |
| `security/async_authorization_policy.py` | `AuthorizationPolicyPersistenceOffload.run()` | KEEP + JUSTIFY |
| `verification/async_persistence.py` | `VerificationPersistenceOffload.run()` | KEEP + JUSTIFY |
| `workspaces/_retention_async.py` | `WorkspaceRetentionPersistenceOffload.run()` | KEEP + JUSTIFY |

### Why `BaseException` is correct here

Each owner acquires a bounded-capacity slot synchronously, then submits the operation to its owned
`ThreadPoolExecutor`. Ownership transfers only after `run_in_executor(...)` has returned a worker
future:

1. before a worker future exists, the async caller owns the acquired slot;
2. after a worker future exists, `_run_sync(...)` owns release in `finally`;
3. if submission itself raises, the caller must release the slot and re-raise the exact throwable;
4. there is no `await` between acquisition and submission, so this is not a normal task-cancellation
   catch; the reason to include `BaseException` is process-control/resource ownership during the
   synchronous handoff;
5. narrowing these handlers to `Exception` would allow `KeyboardInterrupt` or `SystemExit` during
   submission to escape while leaking one capacity slot;
6. once submission succeeds, the caller must not release the slot, because the worker owns it and
   will release exactly once.

All 11 handlers unconditionally bare re-raise after the pre-transfer release. They do not translate,
log, serialize or suppress the throwable. That is the scanner-approved cleanup shape described by
the foundation audit.

The regression coverage added by this slice exercises both `KeyboardInterrupt` and `SystemExit` at
submission for every owner and proves that the same throwable escapes while capacity is returned.

## Started-worker cancellation settlement

All 11 wrappers use the same established post-submission contract:

- the worker future is shielded once persistence has started;
- caller cancellation waits until the worker has settled;
- repeated caller cancellation cannot abandon the started persistence operation;
- if the worker fails while cancellation is pending, the worker failure wins;
- if the worker succeeds while cancellation is pending, the caller's cancellation propagates;
- worker-owned capacity is released before the awaitable boundary completes.

The parameterized regression suite exercises worker-failure precedence, successful settlement under
repeated cancellation and capacity restoration for all 11 owners.

This slice deliberately does not mechanically centralize the duplicated helper implementations.
The semantic contract is already established and covered; changing ownership architecture would be a
separate refactor rather than an exception-audit requirement.

## Portability import compensation

### Confirmed defect

`ImportExecutor.execute()` previously compensated completed resources only under `except Exception`.
If the owning task was cancelled while applying a later resource, `asyncio.CancelledError` bypassed
package rollback and previously applied resources could remain committed.

Rollback itself was also awaited directly. A second caller cancellation during compensation could
therefore interrupt rollback and leave a partially compensated package.

### Corrected semantics

The portability executor now treats three primary paths separately:

- `asyncio.CancelledError`: roll back all previously completed resources, then re-raise the original
  cancellation;
- `KeyboardInterrupt` / `SystemExit`: perform the same best-effort rollback, then re-raise the exact
  process-control signal without converting it into `ContractError`;
- ordinary `Exception`: retain the existing canonical `ContractError` translation and rollback
  completeness details.

Each individual rollback operation runs in its own task and is awaited through `asyncio.shield(...)`
until it settles. Repeated cancellation of the owning import task therefore cannot abandon an
already-started rollback. A rollback operation's own ordinary exception or `CancelledError` is
recorded as a rollback failure and does not prevent remaining applied resources from being
compensated. A new `KeyboardInterrupt` or `SystemExit` raised by rollback itself is not swallowed.

When cancellation or an original process-control signal is primary, rollback failure does not replace
that primary control flow. The primary exception receives only a count/type-safe note; raw rollback
exception text is not copied into that note. Ordinary import failures retain the established
`ContractError` behavior, including redacted rollback-failure diagnostics.

The mutation-handler contract remains important: an `apply(...)` implementation owns compensation
for its own partial mutation until it returns a rollback token. The executor can roll back only
resources whose `apply(...)` completed and produced a token.

## Regression coverage

`tests/regression/persistence/test_offload_baseexception_ownership.py` covers all 11 persistence
owners for:

- synchronous submission `KeyboardInterrupt` and `SystemExit`;
- pre-transfer capacity release and exact process-signal propagation;
- worker failure winning repeated pending cancellation;
- successful worker settlement preserving pending cancellation;
- capacity restoration after worker settlement.

`tests/regression/portability/test_import_executor_cancellation.py` covers:

- cancellation after an earlier resource was applied;
- repeated cancellation while rollback is blocked;
- rollback completion before cancellation escapes;
- cancellation remaining primary when rollback itself fails;
- no rollback-failure secret text in cancellation notes;
- rollback plus exact `KeyboardInterrupt` / `SystemExit` propagation;
- an ordinary primary import failure remaining primary when cancellation arrives during rollback.

## Slice result

For the 11 handed-off production `BaseException` handlers:

- removed: **0**;
- narrowed: **0**;
- retained and explicitly justified as true cleanup/ownership boundaries: **11**;
- silent swallowing found in this set: **0**;
- process-signal translation found in this set: **0**.

The portability cancellation gap is fixed in this slice. #983 remains open for the other repository
migration slices and final repository-wide acceptance/ratchet work.
