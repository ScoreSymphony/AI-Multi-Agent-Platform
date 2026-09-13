# OpenShell threat model (#963)

Reviewed upstream revision: `5b9daab9351b1e053f9a5e0ce4c899f5d3f674b0`  
Decision context: [`OPENSHELL_EVALUATION.md`](OPENSHELL_EVALUATION.md)

This threat model describes how OpenShell may be used as an optional execution-enforcement provider
without becoming a second canonical policy, identity, secret or lifecycle authority.

## Trust boundary

```text
Untrusted user/model/tool input
             |
             v
Platform control plane
Task / Run / #15 Authorization / #34 SecretReference / #43+#591 policy
             |
             v
Canonical Executor boundary
             |
             v
OpenShellExecutor
             |
             v
Provider-private OpenShell client
             |
             v
OpenShell gateway ---------------- provider credential/state store
             |
             v
Policy / supervisor / selected compute driver
             |
        +----+--------------------+
        |         |       |       |
      Docker    Podman  MicroVM  Kubernetes
        |
        v
Untrusted sandbox workload
```

Only the left side through the canonical Executor is platform authority. Everything below the
adapter is an external enforcement/runtime implementation and must be treated as replaceable.

## Protected assets

The integration must protect:

- canonical Task/Run/Step/correlation identities;
- Authorization/Approval decisions;
- canonical egress/data classification decisions;
- SecretReference identity, value and scope;
- Workspace data and unrelated host/platform data;
- File/Artifact durability and provenance;
- Browser/session state if browser-like workloads are introduced later;
- Worker/Node/control-plane credentials;
- provider/gateway management credentials;
- execution evidence and logs from plaintext secret contamination.

## Threats and required controls

| Threat | Risk | Required platform/provider control |
| --- | --- | --- |
| Provider identity becomes canonical | Run recovery becomes coupled to sandbox/gateway IDs | keep IDs only under `adapter_metadata["openshell"]` |
| Policy authority inversion | agent/provider policy silently broadens #15/#43/#591 decisions | platform decides; adapter only projects a bounded provider enforcement policy |
| Secret-store inversion | OpenShell provider records become canonical secret identity/lifecycle | #34 remains canonical; use short-lived scoped delivery bindings only |
| Plaintext credential leakage | env/process/log/provider state exposes values | reject direct environment projection in PoC; live fixture must inspect process/provider/log surfaces |
| Workspace escape | sandbox reads/writes parent/unrelated host paths | canonical root validation plus restricted materialization/mount profile |
| Host bind-mount bypass | arbitrary bind mounts negate filesystem policy | do not expose Workspace parent/unrelated host state; review driver mount configuration |
| Artifact smuggling | provider returns `../`/symlinked path as canonical Artifact | validate resolved path before promotion and revalidate on materialization boundary |
| Egress bypass | DNS/redirect/IPv6/private/link-local path escapes declared policy | run live deny/allow/bypass matrix against selected driver |
| Control-plane reachability | sandbox reaches gateway, Docker socket, kube API or internal services | explicit network isolation and no privileged/control sockets in workload namespace |
| Cross-sandbox access | one workload reads/attacks another | driver-specific namespace/filesystem/network isolation fixture |
| Privilege escalation | sandbox gains elevated host/kernel capability | process/capability/seccomp/Landlock/runtime-class evidence per profile |
| Driver equivalence assumption | Docker evidence is misapplied to MicroVM/Kubernetes | classify evidence by exact compute driver/profile; never merge security claims |
| Cancellation leak | canonical timeout/cancel returns while child workload survives | provider cancel plus live orphan/process cleanup verification |
| Provider failure data leak | raw gateway/runtime exception enters canonical telemetry | normalize infrastructure failures and allow-list provider metadata |
| Retry authority drift | provider retries create duplicate canonical effects | adapter performs no retry loop; platform retains retry authority |
| Stale provider state | old sandbox/provider binding survives new Run or authorization | bounded cleanup/revocation and no canonical recovery by provider ID alone |
| Browser bypass | direct provider session bypasses #74 authorization/evidence | expose browser behavior only through canonical Browser/Capability boundary |

## Runtime-profile-specific risk

### Docker

The reviewed local profile is operationally attractive because Kubernetes is not required, but the
security boundary is still a container/host-runtime boundary. Promotion requires evidence for
Docker socket exposure, mounts, Linux capabilities, seccomp/Landlock behavior, cgroups, network
namespaces and root/rootless configuration as applicable.

### Podman

Do not infer Docker-equivalent behavior. Rootless/rootful configuration, user namespaces, network
stack and socket exposure must be recorded explicitly for the selected profile.

### MicroVM

A VM boundary may reduce kernel sharing with the host, but the reviewed project marks GPU support
and some runtime paths as experimental and the VM driver has its own host virtualization/control
surface. Promotion requires its own lifecycle, filesystem, network, resource and cleanup evidence.

### Kubernetes

The Kubernetes path is explicitly experimental in the reviewed upstream README. A Kubernetes claim
must additionally cover service accounts/RBAC, namespace isolation, CNI/network policy, runtime
class, hostPath/host namespaces, node/control-plane exposure and controller blast radius. It cannot
inherit security evidence from Docker or MicroVM profiles.

## Credential-provider boundary

OpenShell provider access is potentially useful because credentials can be bound to approved
endpoints after policy admission. For this platform, the safe direction is strictly:

```text
#15 Authorization + #34 scoped SecretReference + #43/#591 egress decision
                              |
                              v
                   adapter delivery mapping
                              |
                              v
          provider-local short-lived binding/injection
```

The reverse direction is forbidden. OpenShell provider IDs, credential refresh records or stored
values must not define canonical platform authorization, secret identity or Run recovery.

The PoC therefore rejects non-empty `ExecutionRequest.environment`. Promotion requires a synthetic
secret fixture proving intended access, revocation/expiry, no plaintext canonical logging and known
visibility through process/provider inspection.

## Network policy boundary

OpenShell's application-level destination/method/path controls are useful enforcement primitives,
but canonical policy remains external to OpenShell. The adapter must never accept arbitrary
agent-authored provider policy as authorization.

A protected live profile must test:

- deny by default;
- one explicit allow destination;
- allowed vs denied HTTP methods/paths;
- DNS and redirects;
- alternate ports/protocols;
- loopback/private/link-local/metadata/internal services;
- IPv4 and IPv6;
- update/revocation while a sandbox exists.

Any effective bypass means that profile is unsupported regardless of declarative configuration.

## Residual risk and decision

At the pinned alpha revision, the source design is promising enough to justify
`experimental_only`, particularly because it offers non-Kubernetes drivers plus richer policy and
credential concepts than the current Agent-Sandbox evaluation. The unmeasured runtime-specific
boundary, credential visibility and operational/resource behavior are still too broad for a
supported production claim.
