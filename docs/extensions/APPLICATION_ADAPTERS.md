# Application Adapters

Application Adapters are the platform abstraction for independently deployable external applications and multi-service stacks that require lifecycle, health, endpoints, configuration, storage and runtime integration.

They are intentionally different from the platform's other extension concepts:

| Concept | Use it for |
| --- | --- |
| Tool / Capability | One bounded operation invoked by the platform or an agent |
| Skill | Reusable instructions, knowledge or workflow guidance |
| Connector | Access to an external API or service without owning that service's lifecycle |
| Plugin | Extension of platform behavior through supported extension points |
| Application Adapter | A complete managed application or service stack with independent runtime lifecycle |

A UI alone does not make something an Application. A built-in Tasks page, a PDF extraction operation or a single GitHub API call should remain owned by their existing platform concepts. JupyterLab, code-server, Grafana or a document-editing stack are representative Application candidates because they are independently deployable systems with lifecycle and runtime requirements.

## Canonical model

The canonical Application contract is provider-neutral. It contains the information the platform needs to reason about the application without treating Docker, Podman, Kubernetes, systemd or any other backend as public API.

The declarative manifest can describe:

- one or more services/components;
- image-based or process-based service definitions;
- commands and service dependencies;
- logical endpoints and endpoint exposure intent;
- endpoint- or command-based health checks;
- platform-managed persistent, workspace or runtime-ephemeral volumes;
- typed configuration fields and environment projection metadata;
- canonical `SecretReference` bindings without plaintext secret values;
- CPU, memory, GPU, disk, architecture, OS, capability and label requirements;
- UI endpoint/open-mode metadata;
- optional file/resource association metadata;
- compatibility/maturity and runtime requirements.

Provider-private container IDs, PIDs, Compose project IDs, pod IDs and equivalent runtime handles are not canonical Application or Application Instance identity.

## Application versus Application Instance

An installed `Application` is the canonical definition selected for a concrete runtime adapter. It retains the validated manifest, runtime adapter identity and optional source/provenance information.

An `ApplicationInstance` is a separately addressable running/prepared instance of that definition. Desired state and observed state are deliberately separate. For example:

```text
desired: running
observed: unhealthy
health: unhealthy
```

That distinction is required for crash recovery, runtime restart, node loss and reconciliation.

## Durable state and restart recovery

The single-node reference persistence adapter is `SqliteApplicationRepository`. It stores canonical installed Application definitions and Application Instance state in `db/applications.sqlite3` when Application Adapters are used.

The Application store is optional in the single-node backup inventory because Application Adapters are an optional/beta capability; deployments that never install an Application do not need an empty database. Once the store exists, backup/restore includes it like the platform's other optional durable stores.

Persistence contains canonical metadata only. Secret bindings are serialized as `SecretReference` objects and resolved secret values are never written to the Application repository. Provider-private process/container/runtime handles also remain outside canonical persistence.

`ApplicationLifecycleService` persists desired state before invoking an external runtime transition. Runtime operations are asynchronous because concrete adapters may perform process, network, secret or workspace I/O. A failed start can therefore leave `desired=running` with an observed failure. After a platform restart, reopening the repository and awaiting `recover_all()` supplies that persisted intent to the selected runtime adapter without changing canonical instance identity.

## Runtime boundary

`ApplicationRuntime` is the platform-owned asynchronous lifecycle boundary. A concrete backend implements preparation, start, stop, restart, removal, status, health, endpoint resolution, logs and reconciliation/recovery.

A backend may be implemented with Docker Compose, Podman Compose, local processes, Kubernetes or a remote Worker/Node execution mechanism. Backend choice must not alter canonical IDs or require callers to understand backend-private handles.

Logical endpoints are declared by name. The runtime resolves them to concrete runtime endpoints, so applications do not depend on provider-private endpoint handles as canonical identity.

### Local process reference backend

`LocalProcessApplicationRuntime` is the first concrete runtime backend. It is dependency-free and manages real local subprocesses without invoking a shell. For a PROCESS service, the executed argv is the manifest's `process` tuple followed by its optional `command` tuple.

The backend starts services in validated dependency order. A service with a health check must become healthy before dependent services are started. stdout and stderr are continuously drained into bounded canonical `ApplicationLogEntry` records, while process handles and PIDs remain private to the backend. Stop and restart operate in reverse dependency order where teardown ordering matters.

Typed configuration values are projected only through configuration fields that explicitly declare an `environment_variable`. The backend does not expose arbitrary environment mutation through the manifest.

Configuration mutability is part of the canonical manifest contract. `application.configure` accepts only declared configuration fields and reuses the manifest's typed validation. Fields declared with `mutable: false` may be repeated with their existing value but cannot be changed after installation. Updates are persisted as a new Application Instance revision before runtime convergence. A stopped instance applies the new values on its next start; an instance whose desired state is `running` is restarted through the selected `ApplicationRuntime`, so the backend receives the updated canonical configuration without the frontend or Control Plane encoding backend-specific restart behavior. Removed instances cannot be reconfigured. A failed runtime restart leaves the new configuration intent durable while the observed instance is marked failed for later reconciliation.

When a `SecretProvider` is configured, `SecretReference` bindings may be projected only through secret fields that explicitly declare an `environment_variable`. Resolution happens immediately at the process execution boundary through `SecretAccessContext`; plaintext secret material is retained only in backend-private process environment state and never written into canonical Application or Application Instance persistence. stdout/stderr are redacted using the actually resolved secret values before an `ApplicationLogEntry` is created.

Secret leases are also execution authority, not merely metadata. The local process backend requests a bounded lease and tracks the earliest expiry across all resolved secrets. A long-running process is stopped when that lease expires and subsequent status reports the instance as failed/unhealthy until it is explicitly reconciled or restarted with freshly resolved material. This avoids leaving daemon-like Applications running indefinitely with expired credentials.

When a `LocalApplicationWorkspaceBinder` is configured, the local process backend can bind one canonical Workspace volume as a PROCESS service working directory. The manifest must mount that Workspace at `.`; arbitrary targets such as `/workspace` are intentionally rejected because a plain host subprocess has no mount namespace in which it could truthfully provide such a path. The binder calls the existing `WorkspaceProvider.materialize()` boundary, resolves the local path only through an executor-private callback such as `LocalWorkspaceProvider.local_path()`, and never stores that host path in Application or Application Instance state.

A controlled stop, restart or removal commits a READ_WRITE materialization through the canonical Workspace provider before releasing it. READ_ONLY Workspaces are materialized and released without a commit. Startup failure, cancellation and secret-lease expiry release the materialization without writing partial execution state back into the canonical Workspace. The local backend refuses a requested read-only projection of an otherwise READ_WRITE Workspace because ordinary subprocess `cwd` semantics cannot reliably enforce that narrowing without a sandbox or namespace boundary.

Before preparing a local PROCESS Application, the backend validates the manifest's resource requirements against a backend-private local host profile. The shipped profile detects architecture, operating system, logical CPU capacity, physical memory where the host exposes it, and free storage under the deployment data path using standard-library probes. GPU count, specialized compute capabilities and deployment labels are never guessed; they default conservatively and may be supplied only by an authoritative deployment-specific profile. Declared CPU/RAM/disk/GPU, architecture/OS, capability and label requirements therefore fail closed when the current host cannot prove suitability. Common architecture/OS aliases are normalized without changing canonical Application metadata.

This suitability check does not make the Application runtime a second scheduler. The local backend represents exactly one execution host and still rejects explicit remote `node_id` placement. A future remote Application Runtime must translate the same Application resource/placement intent into the existing distributed Node/Worker scheduler and reservations rather than selecting Nodes inside `applications`. Likewise, the local process backend still fails closed for persistent and runtime-ephemeral Application volume kinds, multiple Workspace volumes and arbitrary mount targets; those capabilities must reuse a platform storage authority rather than introducing backend-local host-directory ownership.

Because PIDs alone are not a safe durable ownership token, a newly created local-process runtime does not adopt an unknown process after a platform/runtime restart. If durable state says `desired=running` but the backend has lost its private process ownership, recovery reports a runtime failure instead of spawning a duplicate process or attaching to an unverified PID. A later supervised-process implementation may provide stronger verifiable adoption semantics without changing canonical Application identity.

For local PROCESS services there is no container network namespace, so declared endpoint target ports currently resolve directly to loopback ports. Container/remote backends may map the same logical endpoint references to different concrete ports or addresses.

## Storage and secrets

Application manifests declare storage needs; installation binds those declarations to approved platform-managed resources. Workspace bindings reference canonical Workspace IDs. Persistent storage uses opaque platform-managed references. Runtime-ephemeral volumes are created and owned by the backend.

Host filesystem paths are not accepted as persistent-volume bindings by the canonical model. A backend that needs host-level materialization must resolve a platform-approved resource internally rather than allowing an Application manifest to request arbitrary host access. The local process Workspace binder follows this rule: only the canonical Workspace ID is persisted, while materialization IDs and host paths remain executor-private and are discarded when the execution materialization is released.

Secrets are always bound through the existing platform `SecretReference` model. Plaintext secret values do not belong in Application manifests or canonical instance state. Runtime adapters resolve material only at the narrow execution boundary and must redact any canonical text surface that could otherwise echo a resolved value.

## Placement

Applications may declare resource and placement requirements, but `applications` does not own a scheduler. Placement must reuse existing Node/Worker/distributed scheduling authority. A concrete Application runtime receives the selected placement and materializes the application there.

## Registry and Marketplace

Application definitions integrate with the existing `distribution` Registry/Marketplace boundary. The current Registry schema accepts `application` as a first-class catalog item type, so available definitions can use the same version, compatibility, source/provenance, dependency, capability, trust and update-discovery metadata as other Registry entries. Application maturity and runtime/backend requirements remain declarative Application metadata; catalog entries may surface indexed summaries through tags and `required_capabilities` without making those summaries lifecycle authority.

A manifest-backed Application catalog entry now uses the generic Marketplace owner-handler route. Marketplace validates the catalog artifact as a canonical Application manifest and asks `ApplicationRuntimeRegistry` to select an unambiguous compatible runtime; installation then delegates to `ApplicationLifecycleService.install()`. The Marketplace does not provision services, select placement itself, start/stop/restart Applications, or maintain a parallel Application lifecycle. Manifestless Application entries remain manual.

Generic Marketplace installation currently applies only when the canonical `ApplicationInstallRequest` can be constructed without unresolved required configuration, secret or volume bindings. Applications that need those bindings continue through the canonical Application installation flow where the caller can supply them explicitly. Artifact-version update is also not synthesized by Marketplace: #1173 has configuration/restart operations but no canonical Application version-migration operation, so the Application kind advertises update as unsupported.

`ApplicationRepository` remains the source of truth for installed definitions, instances, desired/observed state, health and recovery. Marketplace installation state records only the distribution-side source/version/provenance needed for catalog status and uninstall dispatch. Removal delegates to `ApplicationLifecycleService.remove()`; successfully removed historical instances remain Application-owned history and are not treated as an active Marketplace installation.

## Security

Control Plane authorization remains authoritative for Application lifecycle and configuration changes. Application commands use the canonical `application` resource type rather than the generic fallback: installation maps to `create`, configuration to `modify`, start/stop/restart to `execute`, removal to `delete`, and reconciliation to `administer`. Credential scopes, authorization policies and approval rules can therefore target managed Applications without widening authority over unrelated generic resources.

Command payloads are digest-bound before authorization, so approval of one configuration mutation does not authorize a different configuration payload. Authorization and approval decisions emit the existing value-free security audit records; successful lifecycle/configuration completion is separately projected through Application observability so an authorization decision is not mistaken for proof that a runtime transition completed.

Privileged operations may require approval. Endpoint exposure is explicit. Application-provided web UIs are not security boundaries for platform resources.

Successful lifecycle/configuration mutations are projected after the canonical command completes into the Application-owned durable `application-audit-events` collection. The projection records actor/request/correlation identity, command, canonical Application/Instance identifiers and resulting lifecycle state, but deliberately excludes configuration values, secret bindings, volume binding details and runtime-private handles. The same post-success observer emits structured log, metric and timeline records through the existing Telemetry boundary with the `application_adapter` component classification. Authorization/approval audit therefore answers whether a mutation was permitted, while Application audit/telemetry answers whether the canonical mutation actually completed successfully.

Lifecycle/configuration mutations must remain auditable, and runtime implementations must not silently widen filesystem, network or secret access beyond the canonical declaration and approved bindings.
