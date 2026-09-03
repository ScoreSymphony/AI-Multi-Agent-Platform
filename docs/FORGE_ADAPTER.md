# Forge execution adapter

Status: **Issue #9 Phase 4 adapter boundary implemented; concrete transport and Phase 5 recovery work remain.**

`ai_multi_agent_platform.adapters.forge.ForgeExecutor` is an optional execution adapter behind the platform-owned `Executor` contract.

It does not make Forge a platform lifecycle authority and it does not import legacy Forge Task, Execution, Event, database, workflow or daemon types.

## Boundary

The adapter is split into two layers:

- `ForgeExecutor` implements the canonical `Executor` interface.
- `ForgeClient` is a small platform-owned protocol for a future concrete Forge HTTP/process transport.

The current implementation intentionally does not choose or embed a concrete Forge transport. Tests use a fake client so translation behavior can be verified independently from deployment and network assumptions.

## Identity ownership

Canonical identifiers always remain those from `ExecutionRequest`:

- `task_id`
- `run_id`
- `step_id`
- `correlation_id`

A Forge execution ID is adapter-private and is returned only under `ExecutionResult.adapter_metadata["forge"]["execution_id"]`.

The canonical `run_id` is also used as the adapter request reference for best-effort backend cancellation. It is not replaced by a Forge ID.

## Lifecycle and retry ownership

Forge may report `succeeded`, `failed`, `timed_out` or `cancelled` execution outcomes. `ForgeExecutor` translates those outcomes into canonical `ExecutionStatus` values.

The adapter does **not** retry failed executions. Backend retryability and `retry_after_seconds` are normalized into canonical error/adapter metadata so the platform kernel or orchestrator can decide whether another attempt is allowed.

Forge does not update canonical Task/Run state directly.

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

## Provenance

The implementation was designed from the behavior audit recorded in `docs/FORGE_REUSE_AUDIT.md` and `upstream/forge-ai-agent-vps.yaml`.

No source from `ScoreSymphony/AI-Agent-VPS` is copied into this adapter. The current reuse mode is adapter integration plus reference-only behavioral influence.

## Remaining issue #9 work

Phase 4 is not the end of issue #9. Remaining work includes:

1. implement and select a concrete Forge transport only after its stable API/process boundary is verified;
2. add integration tests against a real Forge instance when that transport exists;
3. port historical-event read, deduplication/idempotency and restart reconciliation behavior into platform-owned event/recovery abstractions;
4. add recovery regression tests proving unfinished runs can be reconstructed without Forge becoming the canonical lifecycle kernel;
5. keep Forge disabled/removable without affecting reference execution or core startup.
