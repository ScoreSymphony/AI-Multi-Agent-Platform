# Agent-Sandbox evaluation (#798)

Status: **static/source evaluation complete enough for handoff; live validation owned by #829**  
Reviewed upstream: `agent-sandbox/agent-sandbox`  
Pinned revision: `d1b7ac007debcb1ba8de91c76afb49bee90d096a`  
Review date: 2026-09-11  
License at reviewed revision: Apache-2.0

This document records evidence produced by issue #798. It intentionally separates source-level facts,
platform adapter evidence, and live Kubernetes evidence. A feature advertised by upstream is not
considered proven for this platform until the corresponding live scenario has been exercised.

## Ownership split

Issue #798 owns:

- exact upstream provenance;
- source/architecture/security review;
- canonical adapter fit and deterministic tests;
- comparison methodology;
- live-campaign schema, harnesses, probes and evidence gate;
- the provisional recommendation that determines whether live validation is justified.

Issue #829 owns every conclusion that requires a real Kubernetes/VPS/reference host, including the
execution and retention of the live campaign and the final evidence-backed Agent-Sandbox outcome:
`adopt`, `optional_provider_only`, or `reject/defer`.

Missing live evidence therefore remains fail-closed in the campaign tooling but no longer blocks the
static/source Definition of Done for #798.

## Decision state

The #798 static evaluation supports continuing Agent-Sandbox as a **candidate/evaluation-only
optional provider for live validation under #829**.

No production-support or runtime-isolation claim is made here. The reviewed default upstream
installation/profile is not accepted unchanged as a protected production profile. The final
`adopt` / `optional_provider_only` / `reject/defer` classification is selected under #829 after the
live campaign passes through the #798 evidence and decision framework.

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

The proof-of-concept demonstrates:

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

These are source-level facts, not proof that the effective deployment boundary meets #43.

## Initial security findings

### Egress default is unsuitable for protected platform workloads

The upstream default of full Internet access is too permissive for a high-isolation provider used
with untrusted/model-generated code. The proof-of-concept therefore defaults
`allow_internet_access=False`.

That default alone is **not** sufficient for a production claim. #829 must prove live:

- deny-all behavior for IPv4 and IPv6;
- DNS behavior;
- redirect behavior;
- loopback/private/link-local/cluster-service/metadata endpoint handling;
- explicit allow rules;
- behavior when network enforcement is unavailable;
- whether an alternate direct path can bypass the configured boundary.

Canonical policy remains owned by #15/#43 and the platform egress boundary. Provider-native network
rules are enforcement configuration, not policy authority.

### Kubernetes is not itself an isolation proof

A Kubernetes deployment can provide resource and namespace boundaries, but source review alone does
not establish strong isolation. #829 must record and exercise the actual effective profile,
including:

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

### Credentials remain a live-validation area

The adapter contains no secret-delivery implementation. The reviewed upstream `EnvVars` path is not
accepted for platform secrets because sandbox state is serialized into the `sandbox-data`
ReplicaSet annotation. The adapter therefore fails closed on direct environment projection.

Any secret-bearing supported profile must be proven under #829 using a different #34-compatible
scoped delivery mechanism with evidence for intended access, absence from metadata/evidence,
redaction, egress/exfiltration behavior, revocation and cleanup.

## #829 live evaluation matrix

The following matrix is the handoff contract. `partial` means #798 has platform/source evidence but
the effective provider/runtime behavior remains unproven until #829 executes it.

| Scenario | Source review | Adapter fixture | Live Kubernetes | Status |
|---|---:|---:|---:|---|
| Canonical identity/result mapping | yes | yes | #829 | partial |
| Benign shell task + Artifact | advertised | simulated | #829 | partial |
| Runaway CPU | no | no | #829 | pending |
| Runaway memory | no | no | #829 | pending |
| Timeout | API seam | yes | #829 | partial |
| Cancellation/process cleanup | API seam | yes | #829 | partial |
| Workspace traversal/write escape | design | yes | #829 | partial |
| Read unrelated host/platform data | no | no | #829 | pending |
| Internet deny | documented control | request projection | #829 | partial |
| Scoped egress allow | documented control | request projection | #829 | partial |
| Private/cluster/metadata endpoint isolation | unknown | no | #829 | pending |
| Scoped credential use/exfiltration | unsafe default path identified | fail-closed | #829 | pending |
| Browser download -> canonical Artifact | advertised | ownership resolved through #74 | #829 | pending |
| Pause/resume | advertised/documented | metadata model defined | #829 | pending |
| Snapshot restore | advertised/documented | metadata model defined | #829 | pending |
| Crash/restart cleanup | unknown | no | #829 | pending |
| Cross-sandbox isolation | advertised | no | #829 | pending |
| Concurrent density | unknown | no | #829 | pending |
| Malicious repository fixture | no | no | #829 | pending |

## Resource and operational measurements delegated to #829

The final production/support classification requires reproducible measurements for a representative
deployment, including:

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

The final #829 decision compares three execution profiles. Unknown cells remain unknown rather than
being filled from marketing material.

| Area | Reference Executor | Containarium | Agent-Sandbox |
|---|---|---|---|
| Platform contract | canonical reference | pinned comparison evidence required | PoC maps to canonical Executor |
| Arbitrary shell/code | intentionally no | live comparison where applicable | advertised; #829 live test pending |
| Browser/computer use | no | unknown here | advertised; #74 runtime evidence pending #829 |
| Workspace persistence | platform-local | comparison evidence required | provider FS documented ephemeral; external persistence required |
| Snapshot/pause-resume | no provider concept | comparison evidence required | advertised/documented; #829 semantics pending |
| Network egress | no arbitrary workload network | comparison evidence required | default Internet allow; controls documented; enforcement pending #829 |
| Credential isolation | no arbitrary secret-bearing sandbox path | comparison evidence required | unsafe default EnvVar path identified; safe live path pending #829 |
| Resource limits | deterministic test executor | comparison evidence required | template/Kubernetes controls advertised; live enforcement pending #829 |
| Kubernetes required | no | backend-dependent | yes, >= 1.28 per reviewed docs |
| Baseline dependency | yes | no | no |
| Operational complexity | low | to measure where applicable | materially higher by design; quantify under #829 |

## Production recommendation gate owned by #829

A final `adopt` or `optional_provider_only` result requires retained live evidence for:

1. exact upstream revision/license/provenance;
2. effective Pod/runtime/RBAC/service-account/filesystem isolation review;
3. cross-sandbox and host/platform data isolation tests;
4. deny + allow egress tests including internal/private/metadata destinations;
5. scoped synthetic credential delivery/exfiltration test through a safe delivery mechanism;
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

## #798 provisional recommendation

**Proceed to real validation under #829 as an evaluation-only optional provider candidate.** The
source review and adapter fit are sufficient to justify that handoff, but they are not sufficient to
claim production isolation, provider-native shared multi-tenancy, safe secret-bearing workloads or
acceptable representative VPS cost.

The reviewed default upstream deployment/profile must not be adopted unchanged as a production
high-isolation profile. The final evidence-backed classification is intentionally deferred to #829.
