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

The local process backend still fails closed for persistent and runtime-ephemeral Application volume kinds, multiple Workspace volumes, arbitrary mount targets and explicit remote Node placement. Those capabilities must reuse the platform's storage and distributed placement authorities rather than introducing backend-local substitutes.

Because PIDs alone are not a safe durable ownership token, a newly created local-process runtime does not adopt an unknown process after a platform/runtime restart. If durable state says `desired=running` but the backend has lost its private process ownership, recovery reports a runtime failure instead of spawning a duplicate process or attaching to an unverified PID. A later supervised-process implementation may provide stronger verifiable adoption semantics without changing canonical Application identity.

For local PROCESS services there is no container network namespace, so declared endpoint target ports currently resolve directly to loopback ports. Container/remote backends may map the same logical endpoint references to different concrete ports or addresses.

## Storage and secrets

Application manifests declare storage needs; installation binds those declarations to approved platform-managed resources. Workspace bindings reference canonical Workspace IDs. Persistent storage uses opaque platform-managed references. Runtime-ephemeral volumes are created and owned by the backend.

Host filesystem paths are not accepted as persistent-volume bindings by the canonical model. A backend that needs host-level materialization must resolve a platform-approved resource internally rather than allowing an Application manifest to request arbitrary host access. The local process Workspace binder follows this rule: only the canonical Workspace ID is persisted, while materialization IDs and host paths remain executor-private and are discarded when the execution materialization is released.

Secrets are always bound through the existing platform `SecretReference` model. Plaintext secret values do not belong in Application manifests or canonical instance state. Runtime adapters resolve material only at the narrow execution boundary and must redact any canonical text surface that could otherwise echo a resolved value.

## Placement

Applications may declare resource and placement requirements, but `applications` does not own a scheduler. Placement must reuse existing Node/Worker/distributed scheduling authority. A concrete Application runtime receives the selected placement and materializes the application there.

## Registry and Marketplace

Application definitions integrate with the existing `distribution` Registry/Marketplace boundary. Registry schema v2 accepts `application` as a first-class catalog item type, so available definitions can use the same version, compatibility, source/provenance, dependency, capability, trust and update-discovery metadata as other Registry entries. Application maturity and runtime/backend requirements remain declarative Application metadata; catalog entries may surface indexed summaries through tags and `required_capabilities` without making those summaries lifecycle authority.

An Application catalog entry is deliberately routed as `manual` today. The Registry can therefore discover and preview the definition, but generic Registry activation cannot silently route an Application artifact through plugin installation or portable import. Installation still enters the canonical `applications` boundary through `application.install`, where the validated Application manifest and selected runtime adapter become authoritative. Installed/running state remains owned by `ApplicationRepository` and the `applications` Control Plane resources rather than being shadowed in Distribution state.

Issue #1174 may generalize Marketplace treatment and cross-kind installation handoff across heterogeneous component types. That future dispatch must call the canonical owner rather than moving Application lifecycle into `distribution`; it is not required for Registry discovery under #1173.

## Security

Control Plane authorization remains authoritative for Application lifecycle and configuration changes. Privileged operations may require approval. Endpoint exposure is explicit. Application-provided web UIs are not security boundaries for platform resources.

Lifecycle/configuration mutations must remain auditable, and runtime implementations must not silently widen filesystem, network or secret access beyond the canonical declaration and approved bindings.
