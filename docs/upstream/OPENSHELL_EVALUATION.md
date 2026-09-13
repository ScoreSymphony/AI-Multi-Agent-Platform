# NVIDIA OpenShell evaluation (#963)

Status: **complete for source/contract evaluation; outcome `experimental_only`**  
Reviewed upstream: `NVIDIA/OpenShell`  
Pinned revision: `5b9daab9351b1e053f9a5e0ce4c899f5d3f674b0`  
Review date: 2026-09-13  
License at reviewed revision: Apache-2.0

This document evaluates OpenShell as an optional high-isolation execution/sandbox provider behind
the platform-owned Executor/Worker boundaries. It intentionally reuses the Agent-Sandbox evidence
model from #798/#829 instead of creating a second canonical execution, authorization, secret,
Workspace or egress-policy authority.

## Decision

**Outcome: `experimental_only`.**

OpenShell adds material capability beyond the currently evaluated Agent-Sandbox profile, especially:

- non-Kubernetes local runtime options (documented Docker and Podman drivers);
- a distinct experimental MicroVM isolation path;
- declarative filesystem, process, network and provider-access policy;
- network policy that can express HTTP method/path restrictions and be updated at runtime;
- provider/credential concepts that bind credentials to authorized destinations;
- one gateway abstraction spanning multiple compute drivers.

Those differences are substantial enough to justify keeping a first-party experimental adapter and
source-level evaluation surface. They are **not** sufficient for a supported production claim at the
reviewed revision because:

1. upstream identifies the project as alpha;
2. the Kubernetes deployment path is explicitly experimental;
3. the effective security boundary differs by compute driver, so one OpenShell API cannot be treated
   as one homogeneous isolation guarantee;
4. project-controlled live evidence for DNS/redirect/private/link-local/IPv6 bypass, credential
   visibility, cross-sandbox isolation, cancellation cleanup and host-mount edge cases has not been
   retained;
5. representative VPS cold-start, idle-RAM, density, cleanup and upgrade measurements have not been
   produced for this platform;
6. adopting OpenShell as another supported high-isolation provider now would increase maintenance
   surface before #904 has evidence that its operational value outweighs Agent-Sandbox/Containarium
   overlap.

No production-integration follow-up is opened by #963 because the decision is not
`adopt_optional_provider`. A later promotion issue may be created only after there is a concrete
workload/profile that justifies running the required live campaign.

## Canonical architecture boundary

```text
Task / Run / Authorization / #43/#591 egress decision / #34 SecretReference / Workspace
                                      |
                                      v
                           canonical Executor request
                                      |
                                      v
                              OpenShellExecutor
                                      |
                                      v
                    provider-private OpenShell client
                                      |
                                      v
                         OpenShell gateway / policy
                                      |
                        selected compute driver
                     Docker / Podman / VM / Kubernetes
                                      |
                                      v
                       canonical Result / Artifact evidence
```

The platform remains authoritative for:

- Task, Run, Step and correlation identity;
- retry, completion and recovery policy;
- Worker/Node placement and scheduling;
- Authorization and Approval;
- canonical egress/data/provider policy decisions;
- SecretReference identity, scope, purpose and authorization;
- Workspace, File and Artifact identity/durability;
- Browser/Capability ownership.

OpenShell may enforce a projected policy and manage provider-local sandbox lifecycle, but its sandbox,
gateway, runtime, policy and provider identifiers remain namespaced adapter metadata.

## Provenance and deployment model

The exact evaluated source is pinned in `upstream/openshell.yaml`.

Source facts verified at the pinned revision:

- repository: `https://github.com/NVIDIA/OpenShell`;
- commit: `5b9daab9351b1e053f9a5e0ce4c899f5d3f674b0`;
- license file: Apache License 2.0;
- project README marks the project **alpha**;
- supported host documentation in the README names Linux, Apple Silicon macOS and experimental
  Windows/WSL2;
- local runtime prerequisites name Docker, Podman or host virtualization for MicroVM-backed
  sandboxes;
- the README describes a gateway control plane with Docker, Podman, MicroVM and Kubernetes compute
  platforms;
- the Kubernetes/Helm deployment path is explicitly labelled experimental;
- GPU passthrough is explicitly labelled experimental.

The platform copies no OpenShell source. `OpenShellExecutor` is a dependency-free adapter PoC with a
provider-private `OpenShellClient` protocol and deterministic test doubles.

## Reviewed runtime profile

The PoC names **Docker** as the reviewed source-level runtime profile because it is a documented
local runtime and therefore demonstrates that OpenShell does not intrinsically make Kubernetes a
platform prerequisite.

This is not a statement that Docker is the strongest OpenShell isolation profile. In particular:

- a container boundary is not equivalent to a MicroVM boundary;
- a Kubernetes profile has different service-account, namespace, CNI, controller and cluster blast
  radius concerns;
- a Podman profile can differ in daemon/rootless/network behavior;
- a MicroVM profile introduces a stronger-looking VM boundary but also a different driver and host
  virtualization dependency.

Every supported profile would therefore need its own retained effective-runtime evidence. The
shared OpenShell API does not make those boundaries security-equivalent.

## Minimal Executor PoC

`src/ai_multi_agent_platform/adapters/openshell.py` proves the smallest provider-neutral seam:

`ExecutionRequest -> OpenShellExecutor -> OpenShellClientRequest -> OpenShellClient -> OpenShellClientResult -> ExecutionResult`

The deterministic contract tests prove:

- canonical Task/Run/Step/correlation IDs are preserved unchanged;
- provider request references are private and unique;
- sandbox/gateway/runtime IDs remain under `adapter_metadata["openshell"]`;
- provider policy projection remains adapter-private;
- the default PoC egress projection is fail-closed;
- direct `ExecutionRequest.environment` projection is rejected until a #34-compatible credential
  mapping is proven;
- Workspace traversal is rejected before provider dispatch;
- returned Artifact paths are validated against the canonical Workspace before promotion;
- timeout and cancellation are forwarded through provider-private request references;
- no adapter-owned retry loop exists;
- infrastructure/provider failures are normalized and sensitive provider metadata is allow-listed.

The PoC deliberately does not install the OpenShell CLI/SDK, gateway, Docker, Podman, Kubernetes or
MicroVM dependencies.

## Isolation model

### Source-level capabilities

The reviewed README describes defense in depth across four policy domains:

| Domain | Reviewed source-level behavior | Platform interpretation |
| --- | --- | --- |
| Filesystem | reads/writes constrained by policy; static at sandbox creation | enforcement mechanism only; canonical Workspace remains #37-owned |
| Network | outbound policy, including application-level method/path control; runtime updates supported | projection of canonical #43/#591 decision |
| Process | privilege-escalation/syscall restrictions; static at creation | runtime hardening, not Authorization authority |
| Providers | endpoint-bound credential/network access; runtime attachment/update | delivery mechanism beneath #34, not canonical secret identity |

The architecture/source tree also describes policy enforcement involving sandbox-local mechanisms
such as proxying, OPA, Landlock and seccomp. These are useful security primitives, but source review
cannot establish their effective kernel/runtime coverage on every supported compute driver.

### Evidence still required before promotion

A supported high-isolation claim must retain runtime-profile-specific evidence for:

- namespace/cgroup and effective process boundary;
- host mount and device visibility;
- privileged/capability configuration;
- seccomp/Landlock enforcement and fail-closed behavior;
- container/socket/driver/control-plane exposure;
- cross-sandbox reads/writes/network reachability;
- privilege escalation and host escape attempts;
- cleanup after success, failure, timeout, cancellation and host/runtime restart.

Until that exists, OpenShell remains experimental rather than being described as strictly stronger
than Agent-Sandbox or Containarium.

## Workspace / Files / Artifacts

Canonical ownership is unchanged:

- #37 owns Workspace identity and materialization;
- provider-local sandbox files are execution state, not canonical File storage;
- Artifact promotion validates a relative path against the exact canonical Workspace;
- traversal and outside-Workspace returned paths fail closed;
- provider caches/snapshots/runtime state, if used later, remain provider metadata/state;
- cleanup of a sandbox must never be the durability event for canonical Files/Artifacts.

The OpenShell compute-driver architecture explicitly warns that arbitrary host bind mounts can negate
workspace isolation/filesystem-policy controls. A production profile must therefore keep platform
mounting narrowly defined and must not expose Workspace parents or unrelated host paths.

## Credentials and secrets

OpenShell's provider model is one of its most interesting differentiators, but it is also the area
where platform ownership must be clearest.

Reviewed source describes:

- named providers that hold credential bundles;
- gateway-side provider credential storage/delivery;
- provider profiles that add endpoint policy;
- endpoint-bound credential injection after policy admits the request;
- credentials injected for sandbox processes rather than deliberately persisted into sandbox files.

That does **not** prove #34 compatibility by itself. If used naively, the OpenShell gateway would
become an additional credential store and provider lifecycle authority.

The #963 PoC therefore rejects direct environment projection. A future live/promotion profile must
instead prove a mapping such as:

`authorized scoped SecretReference -> short-lived provider binding/delivery -> exact Run/action/purpose -> revocation/cleanup`

and retain evidence that:

- only the intended sandbox/action can use the credential;
- broad platform credentials are never forwarded;
- plaintext does not enter canonical telemetry, adapter metadata or retained evidence;
- provider/gateway logs and state do not retain values beyond the intended lifetime;
- process environment/inspection visibility is understood for the selected delivery mechanism;
- refresh/revocation is bounded and auditable without logging plaintext;
- an attached provider cannot silently expand canonical authorization or egress scope.

## Network and egress policy

OpenShell provides materially richer source-level enforcement concepts than the reviewed
Agent-Sandbox profile. The reviewed README demonstrates minimal outbound access, explicit policy
updates, destination controls and HTTP method/path decisions.

For the platform, the ownership rule is:

`#43/#591 decide what is allowed -> OpenShell may enforce the translated decision`

A future live campaign must exercise at minimum:

- default deny;
- explicit host allow;
- method/path allow and deny;
- DNS resolution and DNS-based bypass attempts;
- redirects;
- alternate ports and non-HTTP protocols relevant to the profile;
- loopback;
- RFC1918/private networks;
- link-local and metadata-style endpoints;
- gateway/control-plane/internal services;
- IPv6;
- runtime policy update/revocation.

A configured deny policy that is bypassable in the selected runtime is a failed protected profile,
not a documentation discrepancy.

## Browser / computer-use ownership

No provider-native browser/desktop surface becomes agent-facing merely because OpenShell can host an
agent that uses such tools. If a future OpenShell profile exposes browser/computer-use behavior, it
must remain behind #74:

- canonical capability authorization remains platform-owned;
- browser/session IDs remain provider metadata;
- downloads/uploads/screenshots become canonical File/Artifact evidence;
- session credentials/state cannot cross sandboxes;
- provider-native control APIs do not become a second Browser API.

## Lifecycle, recovery and cleanup

The adapter maps provider outcomes into canonical `ExecutionStatus` and forwards timeout/cancel.
It does not own retries.

Source/contract evaluation is insufficient for the following provider-runtime claims and therefore
they remain promotion gates:

- CPU runaway and configured resource enforcement;
- OOM behavior and cleanup;
- background/orphan process cleanup after cancel/timeout;
- gateway/controller failure while a sandbox is running;
- driver/runtime crash;
- host restart/recovery behavior;
- stale sandbox discovery/removal;
- repeated failure cycles without state/credential leakage.

## Resource and VPS suitability

No project-controlled representative VPS benchmark is claimed by #963. That is intentional rather
than hidden with invented values.

The source review establishes that a local non-Kubernetes deployment is possible and therefore
OpenShell has a potentially better VPS/local operational fit than a Kubernetes-only candidate.
However, the following remain **unknown for this platform** until measured on the selected runtime:

- cold sandbox creation;
- warm/reused startup where applicable;
- gateway idle RAM/CPU;
- per-sandbox RAM/CPU;
- disk/state growth;
- concurrent density;
- cleanup/reclamation latency;
- install/upgrade/restart/debugging burden.

These unknowns block `adopt_optional_provider`; they do not block the narrower
`experimental_only` outcome because that classification makes no production capacity or support
claim.

## Comparison against existing execution options

This matrix distinguishes platform evidence from upstream capability descriptions. `unknown` means
not measured or not semantically comparable under the same fixture.

| Area | Lightweight/reference | Containarium | Agent-Sandbox (#798/#829) | OpenShell (#963) |
| --- | --- | --- | --- | --- |
| Canonical Executor fit | canonical baseline | no platform adapter in current comparison | deterministic PoC maps cleanly | deterministic PoC maps cleanly |
| Isolation boundary | intentionally not arbitrary high-risk sandboxing | LXC/Incus or Kubernetes described upstream | Kubernetes sandbox Pod; live profile owned by #829 | Docker/Podman plus experimental MicroVM/Kubernetes drivers; each requires separate evidence |
| Credential delivery | platform-owned; no arbitrary sandbox secret path | not established in current evidence | reviewed EnvVar path unsuitable for protected secret-bearing profile | provider/endpoint-bound credential mechanism is unique value, but #34 mapping/live exposure evidence still required |
| Network/egress enforcement | platform policy around allowed reference actions | explicit egress concepts described upstream | controls documented; effective live behavior owned by #829 | declarative minimal/default-deny egress plus method/path controls described at pinned source; bypass matrix unproven |
| Browser/stateful workloads | no high-isolation browser provider | primarily shell/SSH/MCP in current comparison | browser/computer workload advertised; #74/live evidence required | no second Browser authority accepted; hosting such workloads would still route through #74 |
| Cold/warm startup | low-complexity baseline | unknown | #829 where measured | unknown |
| Idle resources | low-complexity baseline | unknown | #829 where measured | unknown |
| Density | not comparable to arbitrary sandbox providers | unknown | #829 where measured | unknown |
| Operational burden | low | daemon + LXC/Incus or Kubernetes | Kubernetes/control-plane overhead | gateway + selected driver; potentially lower local burden with Docker/Podman, but unmeasured |
| Kubernetes requirement | no | no (LXC/Incus path exists) | yes for reviewed profile | no for local Docker/Podman/MicroVM paths; Kubernetes path optional/experimental |
| Non-Kubernetes support | yes | yes | no for reviewed profile | yes |
| Local/self-hosted cost fit | baseline fit | plausible, unmeasured | self-hosted but cluster-heavy | self-hosted and no recurring paid service required; operational cost still unmeasured |
| Project maturity at reviewed source | platform-owned | candidate | candidate | upstream README explicitly alpha |

## Incremental value versus Agent-Sandbox

OpenShell is not merely another spelling of Agent-Sandbox. The source-level incremental value is:

1. **runtime diversity without mandatory Kubernetes** — especially Docker/Podman and a distinct VM
   path;
2. **richer policy semantics** — filesystem/process/network/provider layers rather than only the
   sandbox lifecycle API plus network controls evaluated for Agent-Sandbox;
3. **endpoint-bound credential/provider model** — potentially useful as #34 delivery mechanics;
4. **hot-reloadable network/provider policy** — useful for revocation between or during long-lived
   sessions;
5. **single gateway abstraction across multiple drivers**.

The same differentiators also increase maintenance burden: more drivers and policy layers create a
larger compatibility/security matrix. At alpha maturity, that argues for experimental retention,
not another production support commitment.

## Required deterministic evidence in this change

The #963 contract suite covers:

- exact pin exposed by the adapter and provenance manifest;
- provider absence/disabled baseline by construction (no OpenShell dependency is imported by the
  reference path);
- reusable canonical Executor contract suite;
- canonical identity preservation;
- provider IDs namespaced to `adapter_metadata`;
- provider policy projection kept private;
- Workspace traversal rejection;
- Artifact outside-path rejection;
- timeout/cancellation forwarding;
- direct secret/environment projection rejection;
- provider failure normalization/redaction;
- no adapter-owned retry loop.

CPU/memory limits, process cleanup, live credential redaction/revocation, network bypass,
cross-sandbox isolation and restart recovery are explicitly **not fabricated as unit tests**. They
require a real selected OpenShell runtime and remain gates for any promotion beyond experimental.

## Acceptance-criteria disposition

- Exact source revision and Apache-2.0 license: **satisfied**.
- Reviewed runtime profile behind Executor/Worker boundary: **satisfied at source/adapter level
  using Docker; no live-isolation claim**.
- Provider-native identities/policies remain non-canonical: **satisfied by adapter contract tests**.
- Workspace/File/Artifact ownership/path escape behavior: **satisfied deterministically; live mount
  behavior remains promotion evidence**.
- Credential behavior mapped safely to #34: **satisfied fail-closed for the PoC; direct environment
  projection rejected; provider delivery remains live promotion evidence**.
- Egress enforcement against #43/#591: **source mapping complete; effective bypass behavior remains
  live promotion evidence**.
- Timeout/cancellation/failure/recovery semantics: **canonical forwarding/normalization proven;
  provider crash/restart cleanup remains live promotion evidence**.
- Isolation/resource/operational evidence sufficient for final decision: **sufficient to reject a
  supported/adopted claim and select `experimental_only`; numerical production claims remain
  intentionally absent**.
- #798/#829 evidence reused: **yes; comparison uses its existing source/live ownership split rather
  than re-running Agent-Sandbox analysis**.
- Incremental value explained: **yes**.
- Baseline unchanged/no paid service: **yes**.
- Exactly one outcome: **`experimental_only`**.

## Promotion gate

A later promotion from `experimental` to `supported` must not rely on this source review alone. It
must select one concrete protected runtime profile and retain project-controlled evidence for the
network/credential/isolation/lifecycle/resource matrices above, then compare that evidence against
the same-class Agent-Sandbox/Containarium evidence available at that time.

Until then, OpenShell is useful evaluation code and evidence, not a supported production dependency.
