# Agent-Sandbox live evidence campaign (#798)

Status: **capture harness ready; representative Kubernetes/VPS run still required**  
Pinned upstream revision: `d1b7ac007debcb1ba8de91c76afb49bee90d096a`

This document defines how live evidence for #798 is captured. It deliberately keeps the live
campaign outside baseline installation and CI: Agent-Sandbox remains optional, and GitHub-hosted
runner measurements must not be presented as representative VPS performance evidence.

## Harness

The repository provides:

```text
scripts/benchmarks/issue798_agent_sandbox_live.py
```

The harness requires only Python and `kubectl`. It targets an **already provisioned evaluation
sandbox Pod** and writes raw JSON evidence. It does not install Agent-Sandbox and does not make
Kubernetes a platform dependency.

Example:

```bash
python scripts/benchmarks/issue798_agent_sandbox_live.py \
  --namespace agent-sandbox-eval \
  --sandbox-pod <sandbox-pod-name> \
  --controller-service-account agent-sandbox \
  --canary ISSUE798-SYNTHETIC-CANARY \
  --allowed-host <explicitly-allowed-test-host> \
  --peer-pod-ip <second-sandbox-pod-ip> \
  --output artifacts/issue798-agent-sandbox-live.json
```

Never use a real production credential as `--canary`. The canary is intentionally synthetic and is
used only to detect accidental persistence in provider/Kubernetes metadata.

## Evidence captured automatically

The current harness records:

- client/server Kubernetes version evidence;
- sandbox Pod identity and namespace;
- `automountServiceAccountToken` state;
- host network/PID/IPC namespace flags;
- Pod/container `runAsNonRoot` state;
- `allowPrivilegeEscalation` state;
- read-only-root-filesystem state;
- seccomp profile;
- dropped Linux capabilities;
- effective `runtimeClassName`;
- sandbox ServiceAccount name;
- configured image and runtime-reported image ID;
- CPU/memory resource requests/limits;
- Pod creation -> Ready transition latency when Kubernetes timestamps are available;
- whether the standard ServiceAccount token path is readable from the sandbox;
- whether common ambient host/runtime paths are readable;
- Internet reachability probe;
- cloud-metadata/link-local reachability probe;
- optional explicitly allowed-host reachability probe;
- optional peer-sandbox reachability probe;
- effective controller ServiceAccount `kubectl auth can-i --list` output;
- synthetic-canary presence in ReplicaSet annotations.

The harness output is **raw evidence**, not an automatic adoption decision.

## Protected-profile expectations

For a profile that claims high isolation, the following are expected before it can be marked
supported:

| Evidence | Expected result |
|---|---|
| sandbox ServiceAccount token readable | no |
| ambient Docker/containerd socket readable | no |
| platform-created `/host` mount readable | no |
| unrestricted Internet target reachable | no |
| metadata/link-local target reachable | no |
| synthetic secret canary in ReplicaSet annotation | no |
| host network/PID/IPC namespaces | disabled |
| privilege escalation | disabled |
| non-root execution | enabled unless a separately reviewed workload requires otherwise |
| seccomp | explicit reviewed profile |
| Linux capabilities | minimal; protected baseline should drop `ALL` before selective additions |
| runtime class | platform-pinned/allowlisted, not model-controlled |
| image identity | immutable/reviewed and runtime identity retained as evidence |

An allowed-host success is meaningful only if the same profile also proves deny behavior for
unapproved destinations. A failed network connection alone is not proof of complete mediation.

## Remaining live cases not fully automated by the first harness

The following still require additional provider/API lifecycle fixtures around the raw capture:

1. create sandbox through the Agent-Sandbox provider API;
2. benign shell task -> canonical Artifact round trip through `AgentSandboxExecutor`;
3. CPU runaway and cgroup/admission enforcement;
4. memory runaway/OOM behavior and canonical error mapping;
5. timeout/cancel followed by orphan-process and sandbox cleanup verification;
6. attempted read/write outside the canonical Workspace materialization;
7. DNS, redirects, IPv6 and alternate-port/protocol egress bypass cases;
8. internal Kubernetes service/control-plane reachability;
9. synthetic scoped credential delivery, use, revocation and exfiltration attempt;
10. browser/download/screenshot path through the canonical #74 boundary;
11. pause/resume process-state behavior;
12. snapshot restore, stale snapshot and incompatible-image behavior;
13. sandbox crash/restart and retained-state cleanup;
14. repeated cold starts and warm-pool allocation latency;
15. idle CPU/RAM/disk measurement;
16. concurrent sandbox density on the representative VPS class;
17. malicious/untrusted repository execution fixture.

These cases must use the same pinned upstream revision and record any platform-owned hardened
blueprint/profile revision used for the run.

## Required environment metadata for a representative result

Every retained VPS result must identify at least:

- capture date;
- host/VPS class and vCPU/RAM/disk;
- Linux distribution/kernel;
- Kubernetes distribution and version;
- container runtime and version;
- runtime class used by sandbox Pods;
- Agent-Sandbox revision/image digest;
- sandbox image digest;
- platform commit/PR under evaluation;
- network plugin/CNI relevant to egress enforcement;
- whether the profile uses default upstream manifests or a platform-hardened derivative.

Measurements missing this context may be useful diagnostics but are not sufficient comparison
evidence.

## Decision rule

#798 must not conclude `adopt` or `optional_provider_only` merely because the API works. The final
recommendation requires both:

1. canonical adapter correctness; and
2. effective runtime evidence showing that the selected protected profile materially improves
   isolation without unacceptable credential, egress, lifecycle or VPS-operability regressions.

If complete mediation cannot be shown for a claimed protected profile, that profile is explicitly
unsupported. Agent-Sandbox can still remain a lower-trust/experimental provider if the evidence
supports that narrower scope.
