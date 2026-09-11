# Agent-Sandbox decision rule (#798)

Status: **decision framework defined; final outcome pending live evidence**  
Reviewed provider: `agent-sandbox/agent-sandbox`  
Pinned revision: `d1b7ac007debcb1ba8de91c76afb49bee90d096a`

Issue #798 requires one final outcome: `adopt`, `optional_provider_only`, or `reject/defer`. This file defines those outcomes before the representative live campaign is complete so that the result cannot be chosen by changing the standard after measurements are known.

## Invariants shared by every outcome

Regardless of the final choice:

- canonical Executor, Worker, Task, Run, Workspace, File, Artifact, Browser and Capability ownership remains platform-owned;
- Kubernetes remains optional for baseline/local platform operation;
- Agent-Sandbox provider identifiers remain namespaced metadata;
- #15 authorization, #34 secret handling, #37 Workspace ownership, #43 security policy and #74 Browser/Web ownership remain authoritative;
- an unsupported provider feature is not silently promoted to a canonical capability;
- missing or contradictory evidence is not treated as a pass.

## Outcome meanings

### `adopt`

`adopt` means Agent-Sandbox is accepted as the **preferred supported high-isolation provider** for the workload classes proven by #798.

It still does **not** mean:

- Kubernetes becomes a baseline dependency;
- all execution migrates to Agent-Sandbox;
- provider-native identity or policy becomes canonical;
- every advertised provider capability is supported.

An `adopt` result requires evidence that the protected Agent-Sandbox profile materially improves the security/capability envelope for high-risk workloads and that its operational/resource cost is acceptable relative to the reference path and the evaluated Containarium alternative.

### `optional_provider_only`

`optional_provider_only` means Agent-Sandbox is accepted as a **supported opt-in provider for a narrower workload/profile set**, but is not the preferred/default high-isolation choice.

This outcome is appropriate when the provider passes the security and canonical-integration gates for a useful scope but one or more of the following remain true:

- Kubernetes/operational burden is materially higher than alternatives;
- resource density/startup cost limits where the provider is economical;
- browser, snapshot, pause/resume, shared multi-tenancy or another advertised feature remains unsupported;
- the provider is useful only behind strict platform mediation or a dedicated topology;
- Containarium or another simpler profile is preferable for some overlapping workloads;
- the demonstrated benefit is real but specialized rather than broad.

Unsupported capabilities must be named explicitly in the final scope.

### `reject/defer`

`reject/defer` means the project does not establish a supported Agent-Sandbox high-isolation provider path from the evidence collected in #798.

`reject` is appropriate when evidence shows an unacceptable property with no credible platform-owned mitigation inside the intended architecture. `defer` is appropriate when the candidate may become viable later but required evidence, upstream behavior, infrastructure support or a safe integration mechanism is not available now.

## Hard blockers for `adopt`

`adopt` is unavailable if any of the following remains true at decision time:

1. the machine-readable #798 campaign is not `decision_ready`;
2. `adoption_eligible_from_this_gate` is false;
3. the protected profile permits host/platform-data access outside the intended boundary;
4. required deny-by-default egress can be bypassed through an exercised IPv4/IPv6/DNS/redirect/private/link-local/cluster/metadata/direct-socket path;
5. effective workload credentials or Kubernetes ServiceAccount credentials are exposed beyond the approved scope or remain usable after required revocation/cleanup;
6. canonical Workspace/Artifact boundaries can be escaped or provider state becomes canonical identity/durability by implication;
7. timeout/cancellation/crash leaves unacceptable orphaned work or retained sensitive state;
8. the browser path bypasses #74 or cannot return required canonical evidence safely when browser support is claimed;
9. the evaluated profile allows model/user-controlled runtime-class, image or privileged deployment choices outside canonical policy;
10. representative resource/latency/density evidence is missing;
11. the comparison against the reference path and Containarium does not demonstrate a material benefit for the workloads for which `adopt` is proposed;
12. baseline platform regression requires Agent-Sandbox/Kubernetes to be present.

## Hard blockers for any supported shared multi-tenant profile

A shared Agent-Sandbox service cannot be declared multi-tenant supported unless the retained authorization campaign proves, for every required sandbox-addressed surface:

- legitimate same-tenant operation succeeds;
- A→B is rejected when token/user A is given B's known sandbox identifier;
- B→A is rejected when token/user B is given A's known sandbox identifier;
- transport failure or blanket denial does not count as success;
- read, file/log, router/terminal, lifecycle, snapshot and delete surfaces used by the supported profile are covered.

If provider-native ownership fails but platform mediation can provably prevent cross-tenant dispatch, `optional_provider_only` may still be possible with **provider-native multi-tenancy explicitly unsupported**. If safe mediation cannot be established, the shared-provider profile must be `reject/defer`.

## Hard blockers for any secret-bearing protected profile

A secret-bearing protected profile cannot be supported while secret values are projected through the reviewed upstream `EnvVars` path because the pinned controller serializes sandbox state into the `sandbox-data` ReplicaSet annotation.

Support requires a different scoped delivery mechanism with retained evidence that:

- secret values are absent from provider/Kubernetes metadata, logs and canonical evidence;
- the workload receives only the intended scoped material;
- exfiltration behavior is constrained/observable according to canonical egress policy;
- revocation/termination removes effective access.

If no safe delivery mechanism is proven, secret-bearing workloads are outside the supported Agent-Sandbox scope even if non-secret workloads pass.

## Evidence interpretation

### A failed security scenario

A real isolation/authorization/egress/credential failure is evidence, not an incomplete test. A fully executed campaign can therefore be `decision_ready` while being ineligible for adoption. The final outcome should then normally narrow to `optional_provider_only` with the failing capability excluded, or `reject/defer` if the failure affects the core protected profile.

### An unsupported scenario

`unsupported` is a valid terminal evaluation result but cannot silently satisfy an adoption claim for that capability. The final scope must either exclude the capability or select `reject/defer` if the capability is necessary to justify the provider.

### A not-run scenario

`not_run` is never positive evidence. Required `not_run` scenarios keep #798 incomplete.

## Comparison rule

The decision must compare the same relevant workload class across:

1. the canonical lightweight/reference path;
2. the pinned Containarium comparison profile where that provider claims the workload;
3. the protected Agent-Sandbox profile.

The comparison should distinguish:

- security/isolation boundary;
- canonical adapter fit;
- startup latency;
- idle CPU/RAM/disk overhead;
- concurrent density;
- cleanup/failure behavior;
- browser/state capabilities where applicable;
- network/credential controls;
- operational and patching burden;
- Kubernetes dependency and representative VPS suitability.

Unknown/unmeasured values remain unknown. Marketing claims do not satisfy the comparison.

## Decision selection

After all required evidence is retained:

```text
campaign decision_ready?
  no  -> issue remains open; no final outcome yet
  yes -> core protected-profile security failures?
           yes -> can a narrower, safe, useful platform-mediated scope be proven?
                    no  -> reject/defer
                    yes -> optional_provider_only
           no  -> material benefit over reference + Containarium demonstrated?
                    no  -> optional_provider_only or reject/defer, based on utility/cost
                    yes -> operational/resource burden acceptable for preferred high-isolation use?
                              yes -> adopt
                              no  -> optional_provider_only
```

The final issue/PR update must name the selected outcome literally, enumerate the supported and unsupported capability scope, link the retained evidence artifacts, state the representative infrastructure used, and record residual risks.

## Current state

No final outcome is selected yet. The current branch supports continued evaluation only. Representative Kubernetes/VPS isolation, authorization, egress, credential, lifecycle, browser/state and resource measurements remain required before #798 can pass through the decision tree above.
