# Single-node composition builders (#890)

The supported base single-node deployment is assembled from explicit typed builders instead of one monolithic construction function. The builders expose construction dependencies directly and deliberately avoid a generic service locator or DI container.

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
  -> SingleNodeDeployment
```

Repository construction is intentionally split in two. `build_repository_foundation()` creates the registry, durable catalog/provenance, management service and execution hooks before the kernel exists. `build_repository_runtime()` creates and binds `RepositoryRunIntegration` only after the canonical kernel exists. This is the explicit post-#892 seam and must not be collapsed into hidden late wiring.

Verification is also constructed before the kernel. `VerificationBundle.completion_authority` is passed into `build_kernel()` and is carried publicly by `SingleNodeDeployment.verification_completion`. Higher deployment layers must use that public authority rather than reading `PlatformKernel` private state.

Automation and Notifications remain owned by the canonical Control Plane. The deployment composition passes their durable SQLite paths into `build_control_plane()`; it does not construct parallel automation or notification runtimes.

Worker processes remain outside the single-node composition root. `build_execution()` may select local reference execution or bind a `DistributedRuntime`, but it does not claim Worker lifecycle ownership.

Optional adapters remain optional constructor inputs. The reference base composition requires neither a secret provider nor a distributed runtime.

## Builder contracts

Each builder returns a frozen dataclass bundle that contains only the authorities required by later layers. Builders receive their dependencies as explicit typed parameters. The base composition does not pass a completed `SingleNodeDeployment` back into lower layers as a service locator.

The public base deployment API remains source-compatible except for the additive `verification_completion` authority. Existing public fields continue to resolve to the same domain services and persistence locations.

## Ownership boundary

This refactor is a composition change, not a package-domain relocation. Moving domain implementations between packages remains outside #890. The durable connector/context/planning/learning/handoff layer is decomposed separately while retaining the same public `deployment.build_single_node_deployment()` entry point.

## Acceptance invariants

The refactor must preserve:

- the same canonical Task/Run kernel and durable persistence paths;
- single-node reference execution without hosted services;
- optional distributed execution and optional secrets;
- restart-safe scope, repository, verification and evaluation state;
- canonical Control Plane module ownership and durable Automation/Notification state;
- readiness/health dependencies on orchestrator, selected lifecycle and files;
- public HTTP/ASGI composition only after the Control Plane is complete;
- no composition-time access to private kernel/runtime attributes.
