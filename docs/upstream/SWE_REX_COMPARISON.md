# SWE-ReX comparison matrix (#861)

Final #861 classification: **`experimental_only`**. This comparison stays conservative: unknown/unexercised provider behavior remains unknown.

| Area | Reference Executor | Forge | Containarium | Agent-Sandbox (#798) | SWE-ReX (#861) |
| --- | --- | --- | --- | --- | --- |
| Canonical Executor fit | Native baseline | Existing optional adapter | Candidate/alternative | Source-backed adapter fit proven; live validation delegated to #829 | PoC + live Local and Docker bridges |
| Baseline dependency | Yes | Optional | Optional | Optional | Optional; no SWE-ReX runtime dependency in platform package |
| Local host execution | Approved baseline actions | Backend-specific | Backend-specific | Not primary model | Linux functional; explicitly unsandboxed |
| Native Windows local | Platform-owned path | Backend-specific | Unknown | Not primary model | **Unavailable at evaluated revision** (`pexpect.spawn` import failure) |
| Docker/container path | No generic sandbox claim | Backend-specific | Primary comparison area | Kubernetes-native | Pinned Docker raw execution and canonical bridge pass; security properties remain backend/deployment-specific |
| Remote abstraction | Canonical Worker path | Sidecar/transport | Backend-specific | Provider/cluster API | `swerex-remote` works in loopback; HTTP/external protection required |
| Workspace ownership | Platform | Platform | Must remain platform | Must remain platform | Platform; Local/Docker/Remote provider paths can access outside selected Workspace |
| Artifact ownership | Platform | Platform | Must remain platform | Must remain platform | Platform; adapter accepts only collected in-Workspace files |
| Provider-private IDs | N/A | Namespaced | Must be namespaced | Must be namespaced | Namespaced adapter metadata |
| Timeout result mapping | Canonical/tested | Mapped/tested | Unknown | Evaluation requirement | Provider timeout signaling works; mapped canonically |
| In-flight cancellation | Canonical/tested | Backend-specific | Unknown | Evaluation requirement | Adapter seam maps it, but Local provider blocks in synchronous `subprocess.run` |
| Process-tree cleanup | Platform/backend specific | Backend-specific | Unknown | Core evaluation requirement; live behavior pending #829 | **Negative evidence:** Docker child survives parent timeout |
| Local isolation | None claimed | Backend-specific | Unknown | N/A | **None**; outside-Workspace access live-confirmed |
| Container filesystem containment | N/A | Backend-specific | Unknown | Core sandbox property; live behavior pending #829 | Provider API read `/etc/hostname`; not a canonical Workspace boundary |
| Default Internet egress | Policy-owned | Backend-specific | Unknown | Critical evaluation item; live behavior pending #829 | **Allowed** in default Docker fixture (HTTP 200) |
| Functional deny-egress profile | Policy/deployment-owned | Backend-specific | Unknown | Critical evaluation item; live behavior pending #829 | **Not demonstrated**; `network=none`/`--internal` break DockerDeployment control path |
| Remote auth | Platform/service identity | Transport-specific | Unknown | Provider/cluster-specific | X-API-Key correct/wrong-token behavior live-verified |
| Remote transport security | Platform-owned | Transport-specific | Unknown | Cluster/API-specific | HTTP in reviewed loopback; TLS/private network external |
| Credential boundary | #34/platform | Platform | Must remain platform | Core evaluation item; unsafe default EnvVar path rejected by #798 | Direct canonical env blocked; raw synthetic env is visible when directly supplied |
| Environment persistence | Platform-controlled | Backend-specific | Unknown | Live behavior pending #829 | Synthetic Docker command env did not persist into next exercised command |
| Dependency completeness | Baseline managed | Adapter-specific | Unknown | Provider-specific | Reviewed RemoteRuntime needs undeclared `aiohttp`; evaluation installs explicitly |
| Server/client revision coupling | Platform-owned | Adapter-specific | Unknown | Provider-specific | Not automatic; #861 builds exact pinned server image and uses `pull=never` |
| Concurrent remote commands | Platform Worker model | Backend-specific | Unknown | Provider-specific | 4 concurrent loopback commands passed |
| Streaming stdout/stderr | Baseline-dependent | Backend-specific | Unknown | Evaluation item | Not established by reviewed one-shot API; no claim |
| Snapshot/pause/resume | Not canonical baseline | Backend-specific | Unknown | Explicit #798/#829 evaluation item | No generic #861 claim |
| Browser/computer use | #74-owned | Not canonical here | Unknown | Explicit #798/#829 overlap | No canonical browser value claimed |
| Kubernetes dependency | No | No baseline requirement | Unknown | Yes for reviewed path | No |
| Docker/Podman dependency | No | Backend-specific | Unknown | Kubernetes runtime path | Optional container path |
| Paid external service required | No | No baseline requirement | Unknown | No for self-hosted path | No for local/Docker/self-hosted remote; cloud adapters may cost money |
| Operational burden | Lowest | Moderate/backend-specific | Unknown | Higher/Kubernetes | Low Local; moderate Docker/Remote plus dependency, transport and provenance handling |
| Security positioning | Platform baseline | Backend-specific | Candidate to measure | High-isolation candidate; production/live claims pending #829 | Runtime portability, **not high-isolation** |
| Current outcome | Baseline | Existing optional adapter | Comparison pending | #798 complete; final evidence-backed support classification pending #829 | **`experimental_only` final** |

## Interpretation

SWE-ReX's demonstrated advantage is a common runtime/deployment abstraction, not stronger isolation. Linux Local, pinned Docker and loopback Remote show useful functional coverage, while the same evidence exposes important limits:

- Local is trusted-host execution with no Workspace containment;
- native Windows Local fails at the evaluated revision;
- default Docker permits Internet egress and broad provider filesystem access;
- Docker parent timeout does not clean descendant processes;
- simple Docker deny-egress network modes also break SWE-ReX's own control channel;
- remote transport protection and canonical Workspace enforcement remain external;
- the remote-backed dependency set needs explicit correction/pinning;
- server-side provenance must be pinned independently from the host client.

This makes SWE-ReX fundamentally different from Agent-Sandbox. #798 is now complete as the source-backed architecture/security/adapter evaluation for a possible high-isolation optional provider and hands all production-shaped runtime claims to #829. That handoff does not upgrade Agent-Sandbox to a proven production isolation profile, but it is enough to close the #861 comparison dependency: SWE-ReX remains the lighter cross-backend runtime abstraction, while Agent-Sandbox remains the separate high-isolation candidate whose final support classification depends on #829 live evidence. #861 must not be treated as a substitute for that decision.

## Discovery/product mapping

#799 is completed. Its candidate taxonomy uses `recommended`, `supported`, `experimental/evaluate`, and `reject/defer`. The #861 result maps to **`experimental/evaluate`**, and only to Advanced/Custom selection with backend-specific warnings/capabilities.

Discovery metadata must distinguish at least:

- runtime availability from isolation strength;
- Linux Local from native Windows Local;
- default Docker Internet access from any future protected network profile;
- raw provider file access from canonical Workspace authorization;
- timeout signaling from descendant cleanup;
- HTTP remote reachability from trusted transport/service identity.

## Why not `supported_optional` or `reject/defer`?

`supported_optional` is not justified because the evaluated profiles do not provide a uniform security, Windows portability, egress, cleanup or transport contract.

`reject/defer` is unnecessarily strong because the runtime abstraction does work for Linux Local, pinned Docker and loopback Remote, and the platform can keep canonical ownership while making the provider completely optional.

The balanced decision is **`experimental_only`** until a future evaluation proves one narrowly defined profile worthy of promotion.
