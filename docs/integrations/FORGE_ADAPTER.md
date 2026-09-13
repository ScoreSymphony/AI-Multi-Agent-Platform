# Forge execution adapter

Status: **Deprecated compatibility backend pending the removal gates in [`FORGE_RETENTION_DECISION.md`](FORGE_RETENTION_DECISION.md) (#991).** Forge is not the reference/default executor and new platform features must not depend on it.

Issue #9 completed the optional execution-only Forge transport and adapter boundary. That implementation remains useful as compatibility and runtime evidence during the deprecation window, but the current real sidecar evidence does not establish a non-null Forge CLI execution capability that justifies long-term `supported` status.

`ai_multi_agent_platform.adapters.forge.ForgeExecutor` is an optional execution adapter behind the platform-owned `Executor` contract.

It does not make Forge a platform lifecycle authority and it does not import legacy Forge Task, Execution, Event, database, workflow or daemon types.

## Boundary

The adapter is split into two layers:

- `ForgeExecutor` implements the canonical `Executor` interface.
- `ForgeClient` is a small platform-owned protocol implemented by the execution-only
  `ForgeHttpClient` transport.

`ForgeHttpClient` targets the pinned `forge-executor-sidecar/v1` runtime and accepts only
unauthenticated loopback HTTP URLs. Tests retain a fake client so translation behavior can be
verified independently from deployment and network assumptions.

## Identity ownership

Canonical identifiers always remain those from `ExecutionRequest`:

- `task_id`
- `run_id`
- `step_id`
- `correlation_id`

A Forge execution ID is adapter-private and is returned only under `ExecutionResult.adapter_metadata["forge"]["execution_id"]`.

`ExecutorLifecycleBackend` carries adapter metadata into its canonical handle/snapshot so external execution identity can be persisted in canonical kernel events without becoming a canonical ID. For Step Runs, the bridge keeps the owning canonical Task ID as `task_id` and carries the Step ID separately as `step_id`.

The canonical `run_id` is also used as the adapter request reference for best-effort backend cancellation. It is not replaced by a Forge ID.

## Lifecycle and retry ownership

Forge may report `succeeded`, `failed`, `timed_out` or `cancelled` execution outcomes. `ForgeExecutor` translates those outcomes into canonical `ExecutionStatus` values.

The adapter does **not** retry failed executions. Backend retryability and `retry_after_seconds` are normalized into canonical error/adapter metadata so the platform kernel or orchestrator can decide whether another attempt is allowed.

Forge does not update canonical Task/Run state directly.

## Historical events, idempotency and recovery

The valuable legacy Forge event/recovery behavior is reused through the platform-owned kernel rather than by introducing a second Forge event store or lifecycle state machine.

The canonical kernel already provides:

- ordered historical event reads;
- transactional SQLite event persistence;
- durable command/idempotency records;
- replay-based Task/Run reconstruction;
- duplicate command/callback handling;
- restart recovery and external-job reconciliation;
- explicit orphaned-running detection without blind redispatch.

The former Forge-specific regression file `tests/regression/forge/test_forge_kernel_regressions.py` was retired under #991 after its generic guarantees were proven outside Forge:

1. `tests/integration/kernel/test_executor_kernel_integration.py` now verifies `ExecutorLifecycleBackend` Task/Run/Step/correlation identity propagation with a backend-neutral recording executor;
2. the same integration module verifies namespaced adapter metadata and backend references survive canonical SQLite replay without any Forge type;
3. `tests/unit/kernel/test_kernel.py` already verifies orphaned-running recovery requires reconciliation and does not redispatch, plus generic SQLite event/adapter-metadata replay and idempotency behavior.

This removes Forge as the sole carrier of those platform guarantees before executable Forge removal is attempted.

## Workspace and artifact boundary

Before dispatch, `ForgeExecutor` resolves the requested workspace below one configured workspace root and rejects missing directories or traversal outside the root.

Forge-returned artifact paths are resolved again below the selected execution workspace before they can become canonical `ExecutionArtifact` evidence. Evidence that escapes the workspace is rejected as an adapter/backend contract failure.

## Cancellation

A pre-cancelled canonical `CancellationToken` stops dispatch immediately.

For an in-flight execution, the adapter races the backend call against the canonical cancellation token. When cancellation wins, it performs best-effort `ForgeClient.cancel(run_id)` and returns canonical `CANCELLED` state. Timeout handling similarly attempts backend cancellation and returns canonical `TIMED_OUT` state.

## Health

`ForgeClient.health()` is translated into an `ExecutorDescriptor`. Backend health/capabilities may be reported, but metadata explicitly records that canonical lifecycle ownership is `platform`.

A health transport failure marks the Forge executor unhealthy rather than breaking core startup.

## Contract coverage

`tests/contract/execution/test_forge_executor.py` subclasses the reusable `ExecutorContractSuite` and covers:

- success and canonical identity preservation;
- controlled failure/error mapping;
- timeout;
- cancellation;
- unsupported capability;
- missing workspace;
- workspace traversal rejection;
- artifact evidence/write boundary;
- namespaced Forge execution IDs;
- health translation;
- in-flight cancellation forwarding;
- backend availability/retry hints without adapter-owned retries.

The same reusable contract suite is applied to `ReferenceExecutor`, so generic timeout, cancellation, capability, workspace and artifact-boundary semantics are not Forge-owned.

`tests/contract/forge/test_forge_optionality.py` proves importing the execution core does not import the Forge adapter.

## Provenance

The implementation was designed from the behavior audit recorded in `docs/integrations/FORGE_REUSE_AUDIT.md` and `upstream/forge-ai-agent-vps.yaml`.

No source from `ScoreSymphony/AI-Agent-VPS` is copied into this adapter. The current reuse mode is adapter integration plus reference-only behavioral influence.

## Concrete runtime coverage

`tests/integration/forge/test_forge_http.py` validates protocol and identity translation for the concrete HTTP client. `tests/integration/forge/test_sidecar.py`, run by the `forge-sidecar-integration` CI job, builds the exact pinned Rust sidecar and verifies real health, execution and cancellation behavior.

The proven sidecar integration profile currently exercises the sidecar's `null` executor. It therefore proves the transport/runtime boundary but does **not** by itself prove a non-null Forge CLI executor family as a supported platform capability. This evidence distinction is the basis for the #991 deprecation decision.

The sidecar is loopback-only and optional; removing it does not affect core startup or reference execution. No replacement backend is promoted by this deprecation decision.
