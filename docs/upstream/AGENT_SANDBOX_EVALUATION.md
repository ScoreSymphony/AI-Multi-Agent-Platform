# Agent-Sandbox evaluation (#798)

Status: **in progress / candidate only**  
Reviewed upstream: `agent-sandbox/agent-sandbox`  
Pinned revision: `d1b7ac007debcb1ba8de91c76afb49bee90d096a`  
Review date: 2026-09-11  
License at reviewed revision: Apache-2.0

This document records evidence for issue #798. It intentionally separates source-level facts,
platform adapter evidence, and live Kubernetes evidence. A feature advertised by upstream is not
considered proven for this platform until the corresponding scenario has been exercised.

## Decision state

No adoption decision is made by this initial slice.

Allowed final outcomes for #798 are:

- `adopt`;
- `optional_provider_only`;
- `reject/defer`.

Until the live isolation, egress, credential, persistence and resource tests are complete,
Agent-Sandbox remains **candidate/evaluation-only**. It is not a baseline dependency and is not a
supported production isolation claim.

## Canonical architecture boundary

The intended boundary is:

```text
Task / Run / authorization / egress decision / Workspace
                         |
                         v
              canonical Executor request
                         |
                         v
                AgentSandboxExecutor
                         |
                         v
           provider-private client/API
                         |
                         v
              Agent-Sandbox / Kubernetes
                         |
                         v
          canonical result / Artifact evidence
```

The platform remains authoritative for:

- Task and Run identity/lifecycle;
- Worker/Node scheduling;
- Authorization and Approval;
- secret references and scoped credential policy;
- Workspace identity and scope;
- File/Artifact identity and persistence;
- Browser/Capability ownership;
- retry policy and completion authority.

Agent-Sandbox may own provider-local sandbox lifecycle mechanics, but sandbox, session, template
and snapshot identifiers remain namespaced adapter metadata.

## Initial proof-of-concept seam

`src/ai_multi_agent_platform/adapters/agent_sandbox.py` provides a dependency-free proof of the
adapter boundary. It deliberately does not import the E2B SDK, Kubernetes client or Agent-Sandbox
runtime.

The proof-of-concept currently demonstrates:

- canonical `ExecutionRequest` -> adapter-private request translation;
- canonical Task/Run/Step/correlation identity preservation;
- provider sandbox/session/snapshot identity confined to
  `ExecutionResult.adapter_metadata["agent_sandbox"]`;
- canonical status/error mapping;
- timeout and cancellation forwarding to the provider client;
- canonical Workspace-root validation before dispatch;
- rejection of returned Artifact evidence that escapes the canonical Workspace;
- health/capability translation;
- no adapter-owned retry policy;
- a provider-private network/security profile with Internet access denied by default.

The provider-private `workspace_path` is a staging seam for a future concrete client. A production
client must upload/materialize only the exact canonical Workspace or use an explicitly authorized
volume mapping. It must not expose or mount the Workspace parent or unrelated host paths merely
because the adapter has a local staging path.

## Upstream facts verified from the pinned source

At the pinned revision, upstream source/documentation establishes the following:

- the repository license file is Apache-2.0;
- upstream describes the service as self-hosted and Kubernetes-native;
- the documented installation requires Kubernetes 1.28 or newer;
- the service exposes REST/E2B-compatible paths and advertises code, shell, browser and
  computer/desktop workloads;
- upstream advertises pools, pause/resume, snapshots, scale-to-zero, leader election, events and
  metrics;
- upstream networking documentation states that sandboxes have full Internet access by default and
  exposes `allow_internet_access` plus `deny_out` / `allow_out` controls;
- upstream shared-storage documentation states that sandbox container filesystems are ephemeral
  across Pod recreation/pause-resume/scale-to-zero, and persistent workloads require external
  network storage;
- the upstream pause/resume proposal explicitly notes that process replay can be incomplete when
  restore fails and that process replay cannot recover runtime-generated files from an ephemeral
  filesystem.

These are source-level facts, not yet proof that the effective deployment boundary meets #43.

## Initial security findings

### Egress default is unsuitable for protected platform workloads

The upstream default of full Internet access is too permissive for a high-isolation provider used
with untrusted/model-generated code. The proof-of-concept therefore defaults
`allow_internet_access=False`.

That default alone is **not** sufficient for a production claim. Live validation still must prove:

- deny-all behavior for IPv4 and IPv6;
- DNS behavior;
- redirect behavior;
- loopback/private/link-local/cluster-service/metadata endpoint handling;
- explicit allow rules;
- behavior when network enforcement is unavailable;
- whether an alternate direct path can bypass the configured boundary.

Canonical policy must be decided by #15/#43 and the platform egress boundary. Provider-native
network rules are enforcement configuration, not policy authority.

### Kubernetes is not itself an isolation proof

A Kubernetes deployment can provide resource and namespace boundaries, but #798 must inspect and
exercise the actual effective security context before claiming strong isolation. The live review
must record at least:

- container runtime/runtime class;
- privilege and Linux capabilities;
- seccomp/AppArmor/SELinux where applicable;
- service-account token mounting and RBAC;
- hostPath/host namespace exposure;
- pod-to-pod and sandbox-to-control-plane network reachability;
- cgroup resource enforcement;
- cross-sandbox access attempts;
- image/template provenance and mutable-tag behavior.

Stronger runtimes such as gVisor/Kata may be relevant, but compatibility with this exact upstream
revision is currently **unknown** and must not be inferred from a related Kubernetes sandbox
project.

### Persistence is not equivalent to canonical Workspace durability

Provider pause/resume/snapshot features do not replace #37 Workspace/File/Artifact persistence.
Because the documented container filesystem is ephemeral, any future production integration must
make the durability boundary explicit. A provider snapshot/process replay identifier must never
become the canonical Workspace or Run identity.

### Credentials remain an unresolved high-risk area

The initial adapter contains no secret-delivery implementation. A live integration must prove that:

- only the exact scoped secret material authorized for the execution is delivered;
- Kubernetes/service-account credentials are not unnecessarily exposed to workload code;
- credentials are not serialized into canonical request/result/evidence;
- termination/revocation removes effective access;
- browser credentials follow the same scoped policy;
- a synthetic secret exfiltration attempt is either blocked by effective egress policy or recorded
  as an explicit unsupported protected profile.

## Required live evaluation matrix

| Scenario | Source review | Adapter fixture | Live Kubernetes | Status |
|---|---:|---:|---:|---|
| Canonical identity/result mapping | yes | yes | not yet | partial |
| Benign shell task + Artifact | advertised | simulated | not yet | partial |
| Runaway CPU | no | no | not yet | pending |
| Runaway memory | no | no | not yet | pending |
| Timeout | API seam | yes | not yet | partial |
| Cancellation/process cleanup | API seam | yes | not yet | partial |
| Workspace traversal/write escape | design | yes | not yet | partial |
| Read unrelated host/platform data | no | no | not yet | pending |
| Internet deny | documented control | request projection | not yet | partial |
| Scoped egress allow | documented control | request projection | not yet | partial |
| Private/cluster/metadata endpoint isolation | unknown | no | not yet | pending |
| Scoped credential use/exfiltration | unknown | no | not yet | pending |
| Browser download -> canonical Artifact | advertised | no | not yet | pending |
| Pause/resume | advertised/documented | no | not yet | pending |
| Snapshot restore | advertised/documented | no | not yet | pending |
| Crash/restart cleanup | unknown | no | not yet | pending |
| Cross-sandbox isolation | advertised | no | not yet | pending |
| Concurrent density | unknown | no | not yet | pending |
| Malicious repository fixture | no | no | not yet | pending |

`partial` means the platform seam has evidence but the effective provider/runtime behavior has not
been proven.

## Resource and operational measurements still required

The final evaluation must retain reproducible measurements for a representative deployment:

- cluster/node CPU and RAM baseline;
- Agent-Sandbox control-plane CPU/RAM;
- idle per-sandbox overhead;
- cold sandbox creation latency;
- warm-pool allocation latency;
- shell command latency after allocation;
- browser/desktop startup and steady-state cost;
- disk/image growth;
- snapshot/persistence growth;
- cleanup/reclamation after success, timeout, cancellation and crash;
- concurrent sandbox density until configured resource admission rejects additional work;
- install/upgrade/recovery/debugging burden.

Results must identify the host/VPS class, Kubernetes distribution/version, container runtime and
Agent-Sandbox revision. GitHub-hosted CI numbers must not be relabelled as representative VPS
measurements.

## Comparison baseline

The final decision compares three execution profiles. Unknown cells remain unknown rather than
being filled from marketing material.

| Area | Reference Executor | Containarium | Agent-Sandbox |
|---|---|---|---|
| Platform contract | canonical reference | to verify from its own evidence | PoC maps to canonical Executor |
| Arbitrary shell/code | intentionally no | unknown here | advertised; live test pending |
| Browser/computer use | no | unknown here | advertised; #74 mapping pending |
| Workspace persistence | platform-local | unknown here | provider FS documented ephemeral; external persistence required |
| Snapshot/pause-resume | no | unknown here | advertised/documented; semantics pending live test |
| Network egress | no arbitrary workload network | unknown here | default Internet allow; controls documented; enforcement pending |
| Credential isolation | no arbitrary secret-bearing sandbox path | unknown here | pending live test |
| Resource limits | deterministic test executor | unknown here | template/Kubernetes controls advertised; live enforcement pending |
| Kubernetes required | no | unknown here | yes, >= 1.28 per reviewed docs |
| Baseline dependency | yes | no | no |
| Operational complexity | low | unknown here | materially higher by design; quantify in live trial |

Containarium values must be populated from its own pinned implementation/evaluation evidence rather
than guessed during the Agent-Sandbox review.

## Acceptance gate before a production recommendation

A final `adopt` or `optional_provider_only` result requires all of the following to have retained
evidence:

1. exact upstream revision/license/provenance;
2. effective Pod/runtime/RBAC/service-account/filesystem isolation review;
3. cross-sandbox and host/platform data isolation tests;
4. deny + allow egress tests including internal/private/metadata destinations;
5. scoped synthetic credential delivery/exfiltration test;
6. real shell lifecycle, timeout, cancellation and cleanup;
7. Workspace upload/download + canonical Artifact round trip;
8. #74-compatible browser evidence if browser capability is claimed;
9. pause/resume/snapshot correctness and stale/incompatible state handling if claimed;
10. representative resource/density/latency measurements;
11. failure/restart cleanup and provider-unavailable behavior;
12. baseline platform regression with Agent-Sandbox absent/disabled;
13. comparison against the current lightweight path and pinned Containarium evidence.

If a protected execution profile can bypass the effective isolation/egress boundary, that profile
must be marked unsupported rather than hidden behind a successful API smoke test.

## Current recommendation

**Pending.** The source review shows enough capability overlap to justify continuing the
experimental adapter and live evaluation, but there is not yet enough evidence to recommend
production adoption. The most important unresolved gates are the effective Kubernetes isolation
boundary, network enforcement/bypass resistance, credential exposure, durable Workspace mapping
and representative VPS resource cost.
