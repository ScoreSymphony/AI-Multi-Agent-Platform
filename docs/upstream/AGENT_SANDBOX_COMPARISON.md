# Agent-Sandbox comparison baseline (#798 -> #829)

Review date: 2026-09-11

This comparison separates **verified source/design facts** from measurements that still require the
same platform-owned live fixtures. #798 defines the comparison baseline and source-level evidence;
#829 owns the real reference-host execution and the final evidence-backed comparison. No
third-party claim becomes a platform security guarantee merely because it appears in upstream
documentation.

## Compared implementations

### Platform reference Executor

The reference Executor is the canonical low-complexity contract implementation used to prove
platform-owned Task/Run/Workspace/Artifact semantics. It is not intended to provide arbitrary
high-isolation untrusted-code execution, browser/desktop execution or provider-native snapshots.
It is the baseline for adapter removability and contract correctness.

### Agent-Sandbox

- upstream: `agent-sandbox/agent-sandbox`
- evaluated revision: `d1b7ac007debcb1ba8de91c76afb49bee90d096a`
- license: Apache-2.0
- deployment model: Kubernetes-native external service
- reviewed Kubernetes minimum: 1.28

### Containarium

- upstream: `FootprintAI/Containarium`
- comparison revision: `1922c228d11b85361ee506e3e43b20cd281e58d9`
- release at that revision: `v0.76.2`
- release published: 2026-09-11
- license: Apache-2.0
- deployment models described by upstream: LXC/Incus and Kubernetes

Containarium is included as a **comparison candidate**, not as an adopted platform component. The
pinned source describes persistent SSH-reachable per-agent boxes, an in-box `agent-box` MCP server,
LXC and Kubernetes backends, and network-isolation controls. Those claims still require our own
threat-model and live-fixture validation before we treat them as guarantees.

## Architecture fit

| Area | Reference Executor | Agent-Sandbox | Containarium |
|---|---|---|---|
| Canonical platform contract | canonical implementation | PoC maps behind `Executor` | no platform adapter evaluated in #798 |
| Baseline dependency | yes | no | no |
| Provider identity | none | sandbox/session/snapshot IDs must remain metadata | box/backend IDs would have to remain metadata |
| Primary runtime shape | local deterministic executor | Kubernetes sandbox Pod | persistent LXC box or Kubernetes backend |
| Agent-facing control surface | canonical platform request | provider API / E2B-compatible paths | SSH + in-box MCP; admin CLI/MCP |
| Kubernetes required | no | yes for reviewed upstream | no; LXC/Incus path exists |
| Canonical Workspace authority | platform | platform must materialize/collect workspace | platform would need an adapter/mapping |
| Canonical retry/completion authority | platform | platform | platform if ever integrated |

## Isolation and security comparison

| Area | Reference Executor | Agent-Sandbox | Containarium | Evidence state |
|---|---|---|---|---|
| Arbitrary untrusted shell/code | intentionally not the high-isolation path | advertised/supported by provider | advertised/supported by provider | #829 live comparison pending |
| Default network posture | no arbitrary provider workload network | upstream Internet access defaults to allowed | upstream describes per-tenant isolation and explicit egress policy | Agent-Sandbox source-verified; live enforcement pending #829 |
| Kubernetes workload credential exposure | not applicable | default sandbox blueprint does not explicitly disable SA-token automount | upstream states agent box holds SSH key rather than kube-apiserver token | #829 live validation pending |
| Controller/admin blast radius | platform-local | reviewed controller Role has broad namespace-local lifecycle/exec permissions | backend-specific; not audited in #798 yet | partial |
| Secret delivery | platform-owned | reviewed default EnvVar path is unsafe because values are serialized into ReplicaSet annotation data | not evaluated in #798 | Agent-Sandbox blocker source-verified |
| Pod/container hardening | not applicable | default blueprint lacks explicit hardened `securityContext` | backend-specific | live profile evidence pending #829 |
| Runtime-class isolation | not applicable | blueprint can select `runtimeClassName` from metadata | Kubernetes backend exists; exact runtime isolation not evaluated here | pending #829 |
| Cross-tenant isolation | not applicable | advertised, not proven by our fixture | explicitly claimed by upstream, not proven by our fixture | pending #829 |
| Image provenance | platform package/runtime controlled | default blueprint uses `IfNotPresent`; protected profile needs immutable policy | not evaluated here | partial |

The important conclusion is not that one external provider is already "more secure". The current
evidence only proves that their **default assumptions and operational models differ**. A final
security ranking requires the same malicious-workload, credential, egress and cross-tenant tests on
representative deployments under #829.

## Workspace and state model

| Area | Reference Executor | Agent-Sandbox | Containarium |
|---|---|---|---|
| State intent | deterministic platform Workspace | sandbox container filesystem documented as ephemeral across Pod recreation | upstream intentionally describes persistent per-agent boxes |
| Pause/resume | no provider concept | advertised; process/filesystem semantics require #829 validation | start/stop/persistent box lifecycle exists at product level |
| Snapshots | no provider concept | provider snapshot support advertised | backend-specific; not evaluated here |
| Durable canonical Artifact | platform-owned | must be collected back into canonical store | would also need explicit adapter collection |
| Risk of provider state becoming canonical | low | high if snapshot/session IDs leak into Run semantics | high if persistent box identity becomes Task/Run/Workspace identity |

Agent-Sandbox therefore looks more naturally suited to **ephemeral/high-isolation execution jobs**
than to being the durability owner. Containarium's persistent-box model may be attractive for
long-lived coding environments, but it creates a different lifecycle-mapping problem: the platform
must not let a provider box become the canonical Workspace or Agent identity by accident.

## Browser/computer-use

Agent-Sandbox advertises browser/computer/desktop workloads. This is relevant to #74 but is not
accepted evidence that it should own the platform Browser domain. The correct live integration test
under #829 is whether a sandboxed browser session can be reached through #74-compatible capability
ownership, return downloads/screenshots through canonical Files/Artifacts and respect the same
egress/secret policy.

Containarium's reviewed README is primarily shell/file/SSH/MCP oriented. #798 does not infer a
browser/computer-use capability that has not been separately verified.

## Operational shape

| Area | Reference Executor | Agent-Sandbox | Containarium |
|---|---|---|---|
| Extra control plane | none/minimal | Agent-Sandbox controller/service + Kubernetes | Containarium daemon/sentinel + Incus/LXC or Kubernetes backend |
| Fast local baseline | yes | no | potentially LXC-based, but unmeasured for our platform |
| Cluster prerequisite | no | Kubernetes >=1.28 | optional depending on backend |
| Persistent storage complexity | canonical platform storage | external/network storage needed for durable sandbox filesystem | persistent box storage is part of provider model |
| Warm pools | no provider concept | advertised | not compared yet |
| Representative VPS measurements | baseline already cheap by design | #829 | #829 where applicable |

No performance or density winner is declared from source documentation. Cold start, idle RAM,
steady CPU, disk growth, cleanup and concurrency must be measured with the same host class and
workload fixtures under #829 before numerical ranking.

## #798 provisional implications

### Reference Executor

Keep as the baseline/canonical proof path. It is deliberately not the solution to arbitrary
high-risk untrusted execution.

### Agent-Sandbox

**Proceed to #829 live validation as an evaluation-only optional-provider candidate.** The adapter
fit is good enough to justify real testing, but the reviewed default upstream deployment is not
acceptable unchanged for a protected profile because of the static security findings recorded in
`AGENT_SANDBOX_SECURITY_REVIEW.md`.

### Containarium

Use as a serious comparison candidate rather than a placeholder. Its LXC option may reduce the need
to make Kubernetes part of a high-isolation deployment and its persistent-box model may suit a
different workload class. However, #798 has not built a canonical Containarium adapter or run the
same malicious-workload/resource suite, so no superiority claim is justified.

## Live head-to-head matrix owned by #829

Run the same fixture set against every profile that claims the capability:

1. benign shell + canonical Artifact round trip;
2. CPU and memory runaway;
3. timeout/cancel + orphan-process cleanup;
4. Workspace path/read/write escape;
5. cross-sandbox/tenant access;
6. host/control-plane/internal service reachability;
7. deny-all egress and scoped allow;
8. synthetic scoped credential exfiltration;
9. crash/restart cleanup;
10. cold start and warm start;
11. idle CPU/RAM/disk;
12. concurrent density on the same representative VPS class;
13. browser/download/evidence path where a provider claims browser support;
14. persistent/resume semantics where a provider claims durable state.

Until those #829 results exist, the comparison remains capability- and architecture-level rather
than a security/performance ranking. Their absence does not block completion of the #798 static
comparison baseline.
