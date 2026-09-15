# Verification async persistence

Issue #892 moves persistence-bearing Verification work out of asyncio runtime hotpaths without replacing the canonical #86 Verification lifecycle.

## Runtime boundary

`VerificationService` and `VerificationCompletionAuthority` remain the synchronous canonical authorities and compatibility/setup seams. Async runtime callers use the backend-neutral `AsyncVerificationService` and `AsyncVerificationCompletionAuthority` contracts, normally through `AsyncVerificationServiceAdapter` and `AsyncVerificationCompletionAuthorityAdapter`.

The adapters own no Verification state. They delegate to the same canonical services used by synchronous setup/offline code, so policy, request, result, audit, completion-gate and repair-lineage semantics remain owned by the existing Verification subsystem.

Canonical request creation outside `VerificationCompletionAuthority` uses `AsyncCanonicalVerificationService`. It extends the same shared Verification offload boundary rather than creating another executor, lock, or source of Verification state.

## Offload policy

`VerificationPersistenceOffload` owns a dedicated bounded `ThreadPoolExecutor` named `verification-persistence`; it does not use asyncio's process-wide default executor. Service, canonical-request and completion adapters wrapping the same canonical Verification service share one serialization boundary through `SharedPersistenceOffloadRegistry`.

This shared boundary matters because `SqliteVerificationCompletionAuthority` persists Task requirements while invoking the same `SqliteVerificationService` that persists policy/request/result state. Runtime operations spanning both authorities therefore remain ordered against one canonical service identity.

Pending submissions are bounded before executor submission. Queue exhaustion maps to retryable `TRANSIENT_FAILURE` instead of allowing unbounded executor growth.

## SQLite ownership and cancellation

SQLite connections continue to be opened by the existing Verification persistence implementation. Runtime persistence methods execute entirely in the dedicated Verification worker, so connection creation, transactions, snapshot writes and row materialization do not run on the asyncio event-loop thread. Constructor-time schema initialization and explicit synchronous setup remain bootstrap seams.

Once persistence has started, cancellation does not abandon the worker. The awaitable boundary shields the operation and waits for it to settle. If the worker fails while cancellation is pending, the persistence failure wins; otherwise the original cancellation is re-raised after the operation has completed. This prevents callers from observing an ambiguous half-finished mutation.

Unhandled SQLite busy/locked failures map to retryable `TRANSIENT_FAILURE`. Other unhandled SQLite failures map to `BACKEND_ERROR`. Existing backend-neutral `ContractError` values remain unchanged unless they wrap a SQLite failure that requires canonical mapping.

## Contract coverage

The awaitable service boundary covers runtime policy/request/result/history/audit reads, verifier preflight, request cancellation and canonical result submission. The awaitable completion boundary covers requirement reads/writes, canonical Verification/reverification creation, completion assessment and subject invalidation. The awaitable canonical-request boundary covers platform-owned release-gate request creation while preserving the same canonical `VerificationService` authority.

The synchronous authorities remain available for deterministic unit tests, bootstrap code and explicitly offline compatibility paths. Canonical async runtime code must not call persistence-bearing synchronous Verification methods inline.

The Verification cohort now routes these async consumers through the awaitable boundary:

- Verification Control Plane resources and review commands;
- kernel completion assessment and subject invalidation;
- reviewer/repair/runtime workflow helpers;
- policy-metadata reviewer routing used by the productive async automatic-reviewer workflow;
- automatic-reviewer startup recovery policy binding checks;
- Verification-backed Context evidence projection;
- Verification-backed Planning/replanning audit evidence when the canonical `VerificationService` is composed;
- application-release Verification gates, including restart recovery and canonical request creation;
- Verification observability timeline reads, including composite and Control Plane timeline projection.

The synchronous `PolicyMetadataReviewerResolver` remains a compatibility seam. `AsyncAutomaticReviewerWorkflow` resolves the same versioned policy and delegates to the same exact/scoped reviewer-routing logic after awaiting the shared Verification boundary. Likewise, `ReplanningEvidenceBridge` retains a narrow synchronous repository fallback for explicit offline/test composition, while production composition using the canonical Verification service automatically shares `runtime_verification_service(...)`.

## Repository provenance boundary

Two asynchronous coding-batch Verification consumers first read canonical #82 repository-run provenance: `CanonicalRepositoryOutputVerifier.ensure_request()` and `CanonicalCodingVerificationCoordinator.ensure_request()`. Repository/Git provenance owns that storage boundary, so Verification does not introduce a second provenance executor.

The Repository Provenance #892 track provides `AsyncRepositoryProvenanceGetReader` and `as_async_repository_provenance_get_reader(...)`. Both coding-batch Verification bridges normalize their synchronous compatibility reader through that Repository-owned runtime boundary and await it from `ensure_request()`. A native async Repository backend may be supplied directly through the same contract. Existing synchronous completion/offline methods continue to use the synchronous reader where their API is intentionally synchronous.

This keeps SQLite repository-provenance reads off the event loop while preserving storage ownership: Verification persistence uses `verification-persistence`, and Repository provenance persistence uses the separately owned bounded `repository-provenance-persistence` boundary.

## Verification evidence for #892

Integration coverage exercises:

- event-loop heartbeat responsiveness while an intentionally slow SQLite expiration write is active;
- worker-thread SQLite connection ownership;
- bounded queue capacity before executor submission;
- shared service/completion serialization for one canonical Verification service;
- cancellation after persistence has started;
- persistence failure precedence over pending cancellation;
- busy/locked and generic SQLite error mapping;
- durable async request/requirement state across SQLite restart;
- event-loop responsiveness and worker ownership for application-release Verification reconciliation;
- event-loop responsiveness and worker ownership for Verification-backed Context evidence reads;
- event-loop responsiveness and worker ownership for Verification timeline reads;
- event-loop responsiveness and worker ownership for policy-metadata reviewer resolution;
- event-loop responsiveness and worker ownership for reviewer-recovery policy binding checks;
- event-loop responsiveness and worker ownership for Verification-backed replanning audit reads;
- event-loop responsiveness and Repository-owned worker execution for combined/repair repository-output Verification requests;
- event-loop responsiveness and Repository-owned worker execution for workstream Verification requests.

These consumer regressions ensure that the existence of an async adapter is not treated as sufficient evidence by itself.
