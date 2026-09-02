# Platform execution contract

Execution is a platform-owned abstraction. Forge, a local process runner, a container runtime, or a remote worker may implement it later, but none of those systems defines the canonical request/result model.

## Canonical boundary

`ai_multi_agent_platform.execution.Executor` receives an `ExecutionRequest` containing platform concepts only: task/run/step/correlation identity, action, arguments, workspace reference, filtered environment/config input, timeout, cancellation, policy context and expected artifact behavior.

`ExecutionResult` preserves the canonical identities and reports normalized status, result code, structured output, stdout/stderr evidence, artifacts, timing/resource metadata, canonical error information and namespaced adapter diagnostics.

Executor implementations do not silently choose platform retry policy. They return normalized failure information so the kernel/orchestrator can decide whether another attempt is appropriate.

## Reference executor

`ReferenceExecutor` is deliberately not a shell. It exposes only an allow-list of deterministic actions:

- `echo` — return deterministic text evidence;
- `write_artifact` — create a text artifact inside the selected workspace;
- `fail` — produce a controlled non-zero execution failure;
- `sleep` — deterministic timeout/cancellation testing.

The reference executor advertises its capabilities and health metadata and explicitly reports that arbitrary commands are disabled.

## Workspace and write boundary

A reference executor is configured with a single workspace root. Every request supplies a workspace relative to that root.

Rules:

1. The configured root is resolved to an absolute path when the executor is created.
2. The requested workspace is resolved underneath that root.
3. A missing/non-directory workspace produces a canonical `workspace_error`; executors do not create an arbitrary requested workspace implicitly.
4. `..`, absolute-path tricks and symlink/resolution escapes that leave the configured root are rejected.
5. Artifact paths are resolved underneath the selected execution workspace and may not escape it.
6. Parent directories for valid artifact paths may be created inside the workspace.
7. The reference executor does not delete the workspace automatically. Cleanup remains an explicit platform/workspace lifecycle responsibility so evidence is not silently destroyed.
8. Artifact evidence is returned as canonical relative paths plus media type/size metadata; storage backends may later materialize them into durable artifact references.

## Security boundary

The reference executor is intentionally unsuitable as an unrestricted command gateway. Adding a shell/process executor requires a separate adapter with explicit command allow-lists, environment filtering, approval/policy checks, sandbox/container boundaries and resource controls.

The contract already carries `policy_context`, `environment`, timeout and cancellation fields so those controls can be implemented without changing orchestrator/task contracts.

## Configuration-driven selection

`ExecutorRegistry` maps configuration-owned names to `Executor` instances. Callers select an executor by configured name rather than importing a concrete implementation. Forge can therefore later be registered as another executor implementation.

## Kernel integration

`ExecutorLifecycleBackend` adapts the new executor abstraction to the lifecycle seam currently consumed by `PlatformKernel`. The integration test `tests/test_executor_kernel_integration.py` demonstrates:

`Task -> Run -> LifecycleBackend -> Executor -> canonical ExecutionResult -> Run/Task terminal state`

No Forge type or Forge runtime is involved.

## Contract-test coverage

`tests/test_reference_executor.py` covers success, controlled non-zero failure, timeout, cancellation, unsupported capability, missing workspace, traversal isolation, artifact evidence, canonical identity preservation, health/capability metadata and configuration-driven selection. These scenarios are intentionally backend-neutral so the same expectations can be applied to a future Forge executor adapter.
