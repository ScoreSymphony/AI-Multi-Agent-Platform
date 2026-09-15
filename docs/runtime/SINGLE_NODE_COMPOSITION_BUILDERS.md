# Single-node composition builders (#890)

The supported single-node deployment is assembled from explicit typed builders instead of one monolithic construction function. The builders expose construction dependencies directly and deliberately avoid a generic service locator or DI container.

## Base construction order

```text
SingleNodeConfig
  -> build_storage
  -> build_observability
  -> build_security
  -> build_runtime_services
  -> build_platform_services
  -> build_repository_foundation
  -> build_execution
  -> build_verification
  -> build_kernel
  -> build_repository_runtime
  -> build_evaluation
  -> build_control_plane
  -> build_http
  -> Base SingleNodeDeployment
```

The public durable profile then composes the owner-domain extensions around that base:

```text
build_connector_foundation
  -> base single-node composition
  -> build_reviewer
  -> build_egress_connectors
  -> build_application_distribution
  -> build_planning
  -> canonical Context composition
  -> Learning
  -> Handoffs
  -> durable SingleNodeDeployment
```

Repository construction is intentionally split in two. `build_repository_foundation()` creates the registry, durable catalog/provenance, management service and execution hooks before the kernel exists. `build_repository_runtime()` creates and binds `RepositoryRunIntegration` only after the canonical kernel exists. This is the explicit post-#892 seam and must not be collapsed into hidden late wiring.

Verification is constructed before the kernel. `VerificationBundle.completion_authority` is passed into `build_kernel()` and is carried publicly by `SingleNodeDeployment.verification_completion`. Higher deployment layers use that authority instead of reading `PlatformKernel` private state.

Execution uses a stable public `LifecycleBinding`. The kernel owns that binding for its whole lifetime. Durable Context composition may replace the startup delegate through `LifecycleBinding.bind()` without reading or mutating `PlatformKernel._lifecycle` or unwrapping private authorization internals. The original fallback lifecycle is also exposed explicitly as `execution_fallback_lifecycle` for Context composition.

Application release gates are constructed with `ApplicationDistributionService`; the public deployment wrapper only forwards the optional `ReleaseGatePolicy`. It no longer patches `gate_coordinator` after construction.

Automation and Notifications remain owned by the canonical Control Plane. The deployment composition passes their durable SQLite paths into `build_control_plane()`; it does not construct parallel automation or notification runtimes.

Worker processes remain outside the single-node composition root. `build_execution()` may select local reference execution or bind a `DistributedRuntime`, but it does not claim Worker lifecycle ownership.

Optional adapters remain optional constructor inputs. The reference base composition requires neither a secret provider nor a distributed runtime.

## Builder contracts

Each builder returns a typed dataclass bundle containing the authorities needed by later layers. Builders receive dependencies as explicit typed parameters. The base composition does not pass a completed `SingleNodeDeployment` back into lower base builders as a service locator.

The public deployment API remains source-compatible. The refactor adds explicit composition authorities (`verification_completion`, `execution_fallback_lifecycle`, and `execution_lifecycle`) while retaining the existing domain-service fields and persistence paths.

## Ownership boundary

This refactor changes composition, not domain-package ownership. Moving domain implementations between packages remains outside #890 and belongs to #895. Existing Context, Learning and Handoff owner-domain composition functions remain the authorities for those domains; the top-level root only orders them after their explicit prerequisites.

## Acceptance invariants

The refactor preserves:

- the same canonical Task/Run kernel and durable persistence paths;
- single-node reference execution without hosted services;
- optional distributed execution and optional secrets;
- restart-safe scope, repository, verification and evaluation state;
- canonical Control Plane module ownership and durable Automation/Notification state;
- readiness/health dependencies on orchestrator, selected lifecycle and files;
- public HTTP/ASGI composition only after the Control Plane is complete;
- release-gate policy binding at application-distribution construction time;
- no composition-time access to private kernel lifecycle/completion attributes.
