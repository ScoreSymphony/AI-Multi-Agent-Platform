# Forge execution adapter

Status: **Issue #9 is complete: the optional execution-only Forge transport and adapter boundary are implemented.**

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

`ExecutorLifecycleBackend` carries adapter metadata into its canonical handle/snapshot so Forge external identity can be persisted in canonical kernel events without becoming a canonical ID. For Step Runs, the bridge keeps the owning canonical Task ID as `task_id` and carries the Step ID separately as `step_id`.

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

`tests/test_forge_kernel_regressions.py` binds those mechanisms to the Forge adapter and proves that:

1. a namespaced Forge execution ID survives canonical SQLite event replay and restart;
2. retrying the original canonical create command after restart returns the existing Task rather than creating another one;
3. Step execution preserves distinct Task and Step identities at the Forge boundary;
4. if a canonical Run is `RUNNING` after restart but the fresh lifecycle adapter cannot find the Forge job, recovery marks reconciliation as required and does not redispatch the work.

This deliberately preserves the useful mechanisms identified in the legacy Forge audit while keeping canonical state ownership in `PlatformKernel`.

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

`tests/test_forge_executor.py` subclasses the reusable `ExecutorContractSuite` and covers:

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

`tests/test_forge_optionality.py` proves importing the execution core does not import the Forge adapter.

`tests/test_forge_kernel_regressions.py` covers canonical persistence/replay, Step identity propagation and restart reconciliation with a Forge-backed executor boundary.

## Provenance

The implementation was designed from the behavior audit recorded in `docs/FORGE_REUSE_AUDIT.md` and `upstream/forge-ai-agent-vps.yaml`.

No source from `ScoreSymphony/AI-Agent-VPS` is copied into this adapter. The current reuse mode is adapter integration plus reference-only behavioral influence.

## Concrete runtime coverage

`tests/test_forge_http.py` validates protocol and identity translation for the concrete HTTP
client. `tests/test_forge_sidecar_integration.py`, run by the `forge-sidecar-integration` CI job,
builds the exact pinned Rust sidecar and verifies real health, execution and cancellation behavior.
The sidecar is loopback-only and optional; removing it does not affect core startup or reference
execution.
