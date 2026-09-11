# SWE-ReX comparison matrix (#861)

This matrix is intentionally conservative. A property is treated as unknown until it has been measured or verified for the relevant backend in this repository.

| Area | Reference Executor | Forge | Containarium | Agent-Sandbox (#798) | SWE-ReX (#861) |
| --- | --- | --- | --- | --- | --- |
| Canonical Executor fit | Native baseline | Adapter exists | Candidate/alternative | PoC on #798 branch | PoC plus live bridge on #861 branch |
| Baseline dependency | Yes | Optional | Optional | Optional | Optional; no runtime dependency in platform package |
| Local host execution | Deterministic approved actions | Backend-specific | Backend-specific | No | Linux live works; explicitly unsandboxed |
| Container path | No production sandbox claim | Backend-specific | Primary area to compare | Kubernetes-native | Docker/Podman deployment; pinned-server evidence in progress |
| Remote abstraction | Through #14 Worker path | Forge transport/sidecar | Backend-specific | Provider API/cluster | Remote server plus cloud adapters; loopback live fixture added |
| Linux support | Platform baseline | Yes where backend supports | To verify | Kubernetes/Linux-oriented | Linux LocalDeployment live functional; container/remote evidence in progress |
| Native Windows local execution | Platform path semantics | Backend-specific | To verify | Not a primary Windows-local path | **Negative at reviewed pin:** LocalDeployment import fails on available Windows runner (`pexpect.spawn`) |
| Windows remote-client value | Platform-owned | Backend-specific | To verify | Provider/API path | Separate hypothesis; not yet demonstrated |
| Command stdout/stderr | Canonical result | Mapped | To verify | Mapped in PoC | Linux local live verified; canonical bridge added |
| Streaming stdout/stderr | Not required for baseline | Backend-specific | To verify | Pending live evidence | Not established by reviewed one-shot command API |
| Timeout | Canonical/tested | Mapped/tested | To verify | PoC/tested; live pending | Linux local provider timeout observed; canonical bridge/live cleanup still being measured |
| Cancellation | Canonical/tested | Mapped/tested | To verify | PoC/tested; live pending | PoC seam tested; provider kill/cleanup pending |
| Workspace ownership | Platform | Platform | Must remain platform | Platform | Platform; Linux Local provider itself does not enforce it |
| Artifact ownership | Platform | Platform | Must remain platform | Platform | Platform; live bridge performs explicit provider-to-canonical collection |
| Provider-private IDs | N/A | Namespaced | Must be namespaced | Namespaced | Namespaced; contract tests added |
| Local isolation strength | None claimed | Backend-specific | To verify | N/A | **None; live outside-Workspace read confirmed** |
| Docker/container isolation | N/A | Backend-specific | To verify | N/A | Backend-specific; authoritative pinned-server run pending |
| High-isolation option | No | Not assumed | Candidate property | Core evaluation goal | Not a uniform SWE-ReX property |
| Egress control | Policy hook only | Backend-specific | To verify | Critical live test | Backend-specific; default/`--network=none` experiment in progress |
| Credential boundary | #34/platform | Platform | Must remain platform | Environment projection blocked in PoC | Environment projection blocked in PoC; Linux provider env canary was visible when supplied directly |
| Remote auth | #36/#14 | Adapter/transport-specific | To verify | Provider/cluster-specific | X-API-Key; correct/wrong-token loopback fixture added |
| Remote transport security | Platform-owned | Transport-specific | To verify | Cluster/API-specific | HTTP supported; TLS/private transport must be supplied externally |
| Remote dependency completeness | Platform baseline | Adapter-specific | To verify | Provider-specific | **Gap at reviewed pin:** base package omits `aiohttp` required by RemoteRuntime import |
| Server/client revision coupling | Platform-owned | Adapter-specific | To verify | Provider-specific | **Not automatic:** Docker fallback can run unpinned `pipx run swe-rex`; #861 now builds exact pinned server image |
| File transfer primitives | Canonical Files/Artifacts | Adapter-specific | To verify | Provider-specific | Upstream read/write/upload APIs; Linux local live round-trip verified |
| Provider filesystem containment | Platform-enforced baseline | Backend-specific | To verify | Core sandbox property | Local/remote server APIs accept provider paths directly; Local live outside-Workspace read succeeded |
| Persistent shell sessions | No canonical requirement | Backend-specific | To verify | Backend-specific | Upstream bash sessions available; remain provider-private |
| Snapshot/pause/resume | Not canonical | Backend-specific | To verify | Explicit provider feature under evaluation | Not a reviewed generic SWE-ReX capability |
| Browser/computer use | Owned by #74 | Not canonical here | To verify | Explicit overlap with #74 | No canonical browser value claimed by #861 |
| Kubernetes dependency | No | No baseline requirement | To verify | Yes for reviewed Agent-Sandbox path | No |
| Docker/Podman dependency | No | Backend-specific | To verify | Kubernetes runtime dependency instead | Optional for container path |
| Paid external service required | No | No baseline requirement | To verify | No for self-hosted path | No for local/Docker/remote self-host; some cloud adapters can cost money |
| Operational burden | Lowest | Moderate/backend-specific | To measure | High/Kubernetes | Low for Linux local; moderate for Docker/remote plus dependency/provenance handling; provider-specific for cloud |
| Security burden | Platform baseline | Adapter/backend-specific | To measure | High but stronger-isolation candidate | High if runtime abstraction is mistaken for isolation; each backend needs its own security profile |
| Current outcome | Baseline | Existing optional adapter | Comparison pending | Evaluation in progress | `experimental_only` provisional |

## Interpretation

SWE-ReX's strongest potential advantage is not stronger isolation. It is a common runtime/deployment abstraction spanning direct local execution, Docker/Podman, a separately hosted server and multiple cloud provider adapters.

The first live evidence narrows that claim substantially:

- Linux local execution is functional, but it is trusted-host execution with no Workspace containment;
- native Windows LocalDeployment is not functional on the available runner at the reviewed revision;
- remote-backed paths expose a missing `aiohttp` dependency in the base package;
- Docker server provenance is not automatically coupled to the host library revision;
- isolation and egress remain deployment properties rather than guarantees of the SWE-ReX abstraction.

That abstraction can still be useful only if the platform continues to own:

- canonical execution lifecycle and IDs;
- Worker scheduling, dispatch and idempotency;
- Workspace materialization and Artifact durability;
- authorization/approval;
- secret delivery;
- egress policy and effective security profile;
- server/client provenance.

SWE-ReX should therefore be compared with Agent-Sandbox as a **different kind of option**, not a drop-in security equivalent. Agent-Sandbox is being evaluated primarily for high-isolation Kubernetes-native workloads; SWE-ReX is being evaluated primarily for backend/runtime portability and a common execution API.

## Decision gates for `supported_optional`

The provisional `experimental_only` classification may be promoted only if live evidence shows all of the following:

1. material backend simplification compared with direct platform adapters despite the native Windows limitation;
2. canonical contract behavior remains stable across the exact supported profile(s);
3. Workspace and Artifact boundaries fail closed at the platform boundary and the provider cannot bypass the effective supported containment model;
4. scoped credentials can be delivered without broad environment leakage;
5. required egress policy can be enforced without breaking the provider control channel;
6. timeout/cancellation/crash cleanup is operationally reliable;
7. remote transport can be deployed with acceptable authentication, provenance and network security;
8. operational cost is lower than, or justified relative to, maintaining equivalent direct adapters.

If only one or two backends are viable and SWE-ReX adds little beyond a thin wrapper around those runtimes, the correct outcome may be `reject/defer` even if the PoC itself works.
