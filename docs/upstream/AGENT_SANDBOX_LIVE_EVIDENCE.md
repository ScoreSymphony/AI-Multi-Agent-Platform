# Agent-Sandbox live evidence campaign (#798 handoff to #829)

Status: **capture/evidence contract ready; representative Kubernetes/VPS execution owned by #829**  
Pinned upstream revision: `d1b7ac007debcb1ba8de91c76afb49bee90d096a`

This document defines the live-evidence contract produced by #798 and consumed by #829. It keeps
live execution outside baseline installation and CI: Agent-Sandbox remains optional, and
GitHub-hosted runner measurements must not be presented as representative VPS performance evidence.

Missing live results remain deliberately fail-closed in the tooling. They block the Agent-Sandbox
live-validation block in #829 and any production-support claim, but they do not block completion of
the static/source #798 scope once this handoff contract and its tests are complete.

## Harness

The repository provides:

```text
scripts/benchmarks/issue798_agent_sandbox_live.py
```

The harness requires only Python and `kubectl`. It targets an **already provisioned evaluation
sandbox Pod** and writes raw JSON evidence. It does not install Agent-Sandbox and does not make
Kubernetes a platform dependency.

Basic example:

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

### Optional cross-token ownership probe

To exercise AS-SEC-009, create two disposable evaluation sandboxes through two distinct provider
API tokens/users. Put the tokens in environment variables rather than command-line arguments:

```bash
export AGENT_SANDBOX_TOKEN_A='<evaluation-token-a>'
export AGENT_SANDBOX_TOKEN_B='<evaluation-token-b>'

python scripts/benchmarks/issue798_agent_sandbox_live.py \
  --namespace agent-sandbox-eval \
  --sandbox-pod <sandbox-pod-name> \
  --provider-base-url http://127.0.0.1:10000/e2b/v1 \
  --sandbox-a-id <sandbox-created-by-a> \
  --sandbox-b-id <sandbox-created-by-b> \
  --output artifacts/issue798-agent-sandbox-live.json
```

`--token-a-env` and `--token-b-env` can select different environment-variable names. Token values
are never written to the JSON report. The first ownership probe is deliberately read-only: it
performs same-token and cross-token `GET /sandboxes/{sandboxID}` requests. A protected shared
provider must allow `A -> A` and `B -> B` while rejecting `A -> B` and `B -> A`. Merely rejecting
all four requests is **not** evidence of working tenant isolation. #829 must repeat the ownership
check for every read/write/lifecycle/router/snapshot surface used by a supported profile.

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
- synthetic-canary presence in ReplicaSet annotations;
- optional same-token/cross-token provider `GET` authorization results, without token values.

The harness output is **raw evidence**, not an automatic adoption decision.

## Machine-readable evidence gate

The repository also provides:

```text
scripts/benchmarks/issue798_agent_sandbox_evidence_gate.py
scripts/benchmarks/issue798_agent_sandbox_campaign.example.json
```

Under #829, copy the example campaign manifest to the retained evidence directory. Fill its
`environment` object from the actual representative run; do not copy placeholder values from
another host or campaign. Change a scenario from `not_run` only after its live fixture has actually
been executed. `pass`, `fail` and `unsupported` are terminal evaluation states and require at least
one evidence reference; a missing scenario is normalized back to `not_run`.

After the raw capture and lifecycle campaign, run:

```bash
python scripts/benchmarks/issue798_agent_sandbox_evidence_gate.py \
  --evidence artifacts/issue798-agent-sandbox-live.json \
  --campaign artifacts/issue798-agent-sandbox-campaign.json \
  --output artifacts/issue798-agent-sandbox-gate.json
```

The gate verifies the raw-evidence schema and pinned provider identity, derives protected-profile
hard gates from Pod/network/credential/authorization evidence, checks that every required lifecycle,
isolation, browser, snapshot and representative-VPS scenario has a terminal result, and requires the
representative host/runtime metadata listed below. It also cross-checks the declared runtime class
and sandbox image against raw Pod evidence so metadata from a different run cannot silently satisfy
readiness.

The provider-authorization hard gate is intentionally two-sided: both legitimate same-token GETs
must succeed **and** both known-ID cross-token GETs must be rejected. Transport errors or missing
directional results stay `not_run`; a deployment that simply rejects every request fails rather
than being credited with tenant isolation.

Its fields have deliberately narrow meanings:

- `protected_profile_gate=pass` means only that all machine-checkable hard gates captured by the current raw harness passed;
- `decision_ready=true` means the required campaign, representative-environment metadata and hard-gate capture are complete and mutually consistent enough for #829 to choose one of `adopt`, `optional_provider_only` or `reject/defer`; failures may still be present;
- `adoption_eligible_from_this_gate=true` is stricter and requires no hard-gate failures, failed scenarios, unsupported required scenarios, missing environment metadata, environment/raw-evidence mismatches or missing hard-gate evidence;
- none of those fields performs the final architecture/security recommendation; #829 applies `AGENT_SANDBOX_DECISION_RULE.md`.

This separation is intentional: a complete campaign that exposes a vulnerability must become
**decision-ready for rejection**, not be mislabeled as incomplete, while missing live work must
never look like successful validation.

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
| same-token provider GET for each disposable tenant sandbox | succeeds |
| cross-token provider GET for another tenant's known sandbox ID | rejected |
| host network/PID/IPC namespaces | disabled |
| privilege escalation | disabled |
| non-root execution | enabled unless a separately reviewed workload requires otherwise |
| seccomp | explicit reviewed profile |
| Linux capabilities | minimal; protected baseline should drop `ALL` before selective additions |
| runtime class | platform-pinned/allowlisted, not model-controlled |
| image identity | immutable/reviewed and runtime identity retained as evidence |

An allowed-host success is meaningful only if the same profile also proves deny behavior for
unapproved destinations. A failed network connection alone is not proof of complete mediation.
Likewise, a blocked cross-token `GET` is necessary but does not by itself prove ownership checks on
mutating provider endpoints.

## Remaining live cases owned by #829

The following require provider/API lifecycle fixtures around the raw capture:

1. create sandbox through the Agent-Sandbox provider API;
2. benign shell task -> canonical Artifact round trip through `AgentSandboxExecutor`;
3. CPU runaway and cgroup/admission enforcement;
4. memory runaway/OOM behavior and canonical error mapping;
5. timeout/cancel followed by orphan-process and sandbox cleanup verification;
6. attempted read/write outside the canonical Workspace materialization;
7. attempted read of unrelated host/platform data;
8. DNS, redirects, IPv6 and alternate-port/protocol egress bypass cases;
9. internal Kubernetes service/control-plane reachability;
10. synthetic scoped credential delivery, use, revocation and exfiltration attempt;
11. cross-token ownership for native/provider/E2B file/log/terminal/router/lifecycle/snapshot/delete operations;
12. browser/download/screenshot path through the canonical #74 boundary;
13. pause/resume process-state behavior;
14. snapshot restore, stale snapshot and incompatible-image behavior;
15. sandbox crash/restart and retained-state cleanup;
16. repeated cold starts and warm-pool allocation latency;
17. idle CPU/RAM/disk and disk/snapshot growth measurement;
18. concurrent sandbox density and multi-sandbox failure cleanup on the representative VPS class;
19. malicious/untrusted repository execution fixture;
20. same-class reference Executor and Containarium comparison where applicable.

These cases must use the same pinned upstream revision and record any platform-owned hardened
blueprint/profile revision used for the run.

## Required environment metadata for a representative result

Every retained #829 Agent-Sandbox campaign must identify at least:

- capture date;
- host/VPS class and vCPU/RAM/disk;
- Linux distribution/kernel;
- Kubernetes distribution and version;
- container runtime and version;
- runtime class used by sandbox Pods;
- Agent-Sandbox revision and digest-pinned image identity;
- digest-pinned sandbox image identity;
- platform commit under evaluation;
- network plugin/CNI relevant to egress enforcement;
- whether the profile uses default upstream manifests or a platform-hardened derivative;
- the exact profile revision/configuration identity used for the run.

The campaign template represents these as `capture_date`, `host_class`, `vcpu`, `memory_gib`,
`disk_gib`, `linux_distribution`, `kernel`, `kubernetes_distribution`, `kubernetes_version`,
`container_runtime`, `runtime_class`, `agent_sandbox_revision`, `agent_sandbox_image_digest`,
`sandbox_image_digest`, `platform_commit`, `cni`, `profile_kind` and `profile_revision`.

Measurements missing this context may be useful diagnostics but are not sufficient comparison
evidence and cannot make the machine-readable gate `decision_ready`.

## Decision ownership

#798 may conclude its static/source evaluation once the adapter, reviews, deterministic tests and
this reproducible evidence contract are complete and CI is green.

#829 must not conclude `adopt` or `optional_provider_only` merely because the API works. Its final
Agent-Sandbox recommendation requires both:

1. canonical adapter correctness established by #798; and
2. retained effective runtime evidence showing that the selected protected profile materially improves isolation without unacceptable credential, egress, lifecycle or VPS-operability regressions.

If complete mediation cannot be shown for a claimed protected profile, that profile is explicitly
unsupported. Agent-Sandbox can still remain a narrower optional/experimental provider only if the
retained evidence supports that scope.
