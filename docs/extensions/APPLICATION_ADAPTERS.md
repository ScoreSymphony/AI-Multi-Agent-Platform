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

## Runtime boundary

`ApplicationRuntime` is the platform-owned lifecycle boundary. A concrete backend implements preparation, start, stop, restart, removal, status, health, endpoint resolution, logs and reconciliation/recovery.

A backend may be implemented with Docker Compose, Podman Compose, local processes, Kubernetes or a remote Worker/Node execution mechanism. Backend choice must not alter canonical IDs or require callers to understand backend-private handles.

Logical endpoints are declared by name. The runtime resolves them to concrete runtime endpoints, so applications do not depend on fixed host ports as canonical identity.

## Storage and secrets

Application manifests declare storage needs; installation binds those declarations to approved platform-managed resources. Workspace bindings reference canonical Workspace IDs. Persistent storage uses opaque platform-managed references. Runtime-ephemeral volumes are created and owned by the backend.

Host filesystem paths are not accepted as persistent-volume bindings by the canonical model. A backend that needs host-level materialization must resolve a platform-approved resource internally rather than allowing an Application manifest to request arbitrary host access.

Secrets are always bound through the existing platform `SecretReference` model. Plaintext secret values do not belong in Application manifests or canonical instance state.

## Placement

Applications may declare resource and placement requirements, but `applications` does not own a scheduler. Placement must reuse existing Node/Worker/distributed scheduling authority. A concrete Application runtime receives the selected placement and materializes the application there.

## Registry and Marketplace

Application definitions integrate with the existing `distribution` Registry/Marketplace boundary. The Application domain owns runtime lifecycle after a definition is selected/installed; the distribution domain continues to own generic catalog discovery, provenance and install routing. Issue #1174 may generalize Marketplace treatment across heterogeneous component types, but it does not replace #1173's Application lifecycle ownership.

## Security

Control Plane authorization remains authoritative for Application lifecycle and configuration changes. Privileged operations may require approval. Endpoint exposure is explicit. Application-provided web UIs are not security boundaries for platform resources.

Lifecycle/configuration mutations must remain auditable, and runtime implementations must not silently widen filesystem, network or secret access beyond the canonical declaration and approved bindings.
