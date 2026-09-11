# SWE-ReX comparison matrix (#861)

This matrix is intentionally conservative. `unknown` means the property has not been measured or verified for the relevant backend in this repository.

| Area | Reference Executor | Forge | Containarium | Agent-Sandbox (#798) | SWE-ReX (#861) |
| --- | --- | --- | --- | --- | --- |
| Canonical Executor fit | Native baseline | Adapter exists | Candidate/alternative | PoC on #798 branch | PoC on #861 branch |
| Baseline dependency | Yes | Optional | Optional | Optional | Optional |
| Local host execution | Deterministic approved actions | Backend-specific | Backend-specific | No | Yes, but unsandboxed |
| Container path | No production sandbox claim | Backend-specific | Primary area to compare | Kubernetes-native | Docker/Podman deployment |
| Remote abstraction | Through #14 Worker path | Forge transport/sidecar | Backend-specific | Provider API/cluster | Remote server plus cloud adapters |
| Linux support | Platform baseline | Yes where backend supports | To verify | Kubernetes/Linux-oriented | Upstream abstraction supports local/container/remote; live #861 Linux evidence pending |
| Windows-path value | Platform path semantics | Backend-specific | To verify | Not a primary Windows-local path | Potential differentiator; live #861 evidence pending |
| Command stdout/stderr | Canonical result | Mapped | To verify | Mapped in PoC | One-shot upstream command response exposes stdout/stderr; live mapping pending |
| Streaming stdout/stderr | Not required for baseline | Backend-specific | To verify | Pending live evidence | Not established by reviewed one-shot command API |
| Timeout | Canonical/tested | Mapped/tested | To verify | PoC/tested; live pending | PoC/tested; live per-backend pending |
| Cancellation | Canonical/tested | Mapped/tested | To verify | PoC/tested; live pending | PoC seam tested; provider kill/cleanup pending |
| Workspace ownership | Platform | Platform | Must remain platform | Platform | Platform |
| Artifact ownership | Platform | Platform | Must remain platform | Platform | Platform |
| Provider-private IDs | N/A | Namespaced | Must be namespaced | Namespaced | Namespaced |
| Local isolation strength | None claimed | Backend-specific | To verify | N/A | **None for LocalDeployment** |
| Docker/container isolation | N/A | Backend-specific | To verify | N/A | Backend-specific; live evidence pending |
| High-isolation option | No | Not assumed | Candidate property | Core evaluation goal | Not a uniform SWE-ReX property |
| Egress control | Policy hook only | Backend-specific | To verify | Critical live test | Backend-specific; no uniform guarantee verified |
| Credential boundary | #34/platform | Platform | Must remain platform | Environment projection blocked in PoC | Environment projection blocked in PoC |
| Remote auth | #36/#14 | Adapter/transport-specific | To verify | Provider/cluster-specific | X-API-Key at reviewed remote runtime; platform trust remains separate |
| Remote transport security | Platform-owned | Transport-specific | To verify | Cluster/API-specific | HTTP supported; TLS/private transport must be supplied externally |
| File transfer primitives | Canonical Files/Artifacts | Adapter-specific | To verify | Provider-specific | Upstream read/write/upload APIs available |
| Persistent shell sessions | No canonical requirement | Backend-specific | To verify | Backend-specific | Upstream bash sessions available; remain provider-private |
| Snapshot/pause/resume | Not canonical | Backend-specific | To verify | Explicit provider feature under evaluation | Not a reviewed generic SWE-ReX capability |
| Browser/computer use | Owned by #74 | Not canonical here | To verify | Explicit overlap with #74 | No canonical browser value claimed by #861 |
| Kubernetes dependency | No | No baseline requirement | To verify | Yes for reviewed Agent-Sandbox path | No |
| Docker/Podman dependency | No | Backend-specific | To verify | Kubernetes runtime dependency instead | Optional for container path |
| Paid external service required | No | No baseline requirement | To verify | No for self-hosted path | No for local/Docker/remote self-host; some cloud adapters can cost money |
| Operational burden | Lowest | Moderate/backend-specific | To measure | High/Kubernetes | Low for local host mode; moderate for Docker/remote; provider-specific for cloud |
| Security burden | Platform baseline | Adapter/backend-specific | To measure | High but stronger-isolation candidate | High if abstraction is mistaken for isolation; must classify each backend |
| Current #861/#798 outcome | Baseline | Existing optional adapter | Comparison pending | Evaluation in progress | `experimental_only` provisional |

## Interpretation

SWE-ReX's strongest potential advantage is not stronger isolation. It is a common runtime/deployment abstraction spanning direct local execution, Docker/Podman, a separately hosted server and multiple cloud provider adapters.

That can be useful for cross-platform/provider interoperability only if the platform continues to own:

- canonical execution lifecycle and IDs;
- Worker scheduling and dispatch;
- Workspace materialization and Artifact durability;
- authorization/approval;
- secret delivery;
- egress policy and effective security profile.

SWE-ReX should therefore be compared with Agent-Sandbox as a **different kind of option**, not a drop-in security equivalent. Agent-Sandbox is being evaluated primarily for high-isolation Kubernetes-native workloads; SWE-ReX is being evaluated primarily for backend portability and a common execution API.

## Decision gates for `supported_optional`

The provisional `experimental_only` classification may be promoted only if live evidence shows all of the following:

1. material cross-platform or cross-backend simplification compared with direct platform adapters;
2. canonical contract behavior remains stable across exercised backends;
3. Workspace and Artifact boundaries fail closed;
4. scoped credentials can be delivered without broad environment leakage;
5. required egress policy can be enforced for the supported profile(s);
6. timeout/cancellation/crash cleanup is operationally reliable;
7. remote transport can be deployed with acceptable authentication and network security;
8. operational cost is lower than, or justified relative to, maintaining equivalent direct adapters.

If only one or two backends are viable and SWE-ReX adds little beyond a thin wrapper around those runtimes, the correct outcome may be `reject/defer` even if the PoC itself works.
