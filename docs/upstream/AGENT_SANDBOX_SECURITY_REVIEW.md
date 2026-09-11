# Agent-Sandbox static security review (#798)

Reviewed upstream: `agent-sandbox/agent-sandbox`  
Pinned revision: `d1b7ac007debcb1ba8de91c76afb49bee90d096a`  
Review date: 2026-09-11

This review is limited to the pinned source/configuration. It is not a runtime penetration test and
must not be used as evidence that a Kubernetes deployment is a strong isolation boundary.

## Summary

The upstream architecture remains worth evaluating, but the reviewed default deployment and
sandbox blueprint are **not acceptable unchanged as the platform's protected high-isolation
profile**. The main blockers are default credential/configuration handling and missing explicit Pod
hardening in the default blueprint.

A future supported integration would need a platform-owned hardened deployment/blueprint profile,
strictly scoped provider credentials, explicit egress projection and live bypass/isolation tests.

## Findings

### AS-SEC-001 — Default installation contains a static system token

**Severity:** high for deployments that apply the manifest unchanged.  
**Evidence:** pinned `install.yaml` sets `SYSTEM_TOKEN` to the literal value
`sys-2492a85b10ed4cb083b2c76b181eac96`.

**Impact:** anyone who can reach an unchanged deployment and knows the public manifest value may be
able to authenticate with system-level authority, depending on the endpoint/authentication path.

**Platform requirement:** never deploy the reviewed literal token. Generate/store provider API
credentials through #34, inject them from a Kubernetes Secret or equivalent, rotate them, and keep
them out of repository/config evidence.

**Live gate:** prove an unchanged/default token is rejected by the platform deployment profile and
verify the effective provider authorization boundary.

### AS-SEC-002 — Controller ServiceAccount has broad namespace-local control

**Severity:** high blast radius after controller compromise; expected capability but must be
explicitly accepted.  
**Evidence:** `install.yaml` creates a namespace `Role` allowing get/list/watch/create/update/patch/
delete across leases, Pods, `pods/exec`, logs/status, events, ReplicaSets, ConfigMaps and Services,
with `apiGroups: ["*"]` for the listed resources. The controller Deployment uses this
ServiceAccount.

**Impact:** compromise of the Agent-Sandbox control plane gives substantial control over sandbox
resources in its namespace, including exec and lifecycle operations. This is not cluster-wide RBAC
in the reviewed manifest, but it is a large namespace-level blast radius.

**Platform requirement:** dedicate an isolation namespace, minimise the Role against observed API
calls, deny unrelated workloads in that namespace, and keep the Agent-Sandbox API/control-plane
network unreachable from sandbox workloads except where explicitly necessary.

**Live gate:** capture effective RBAC with `kubectl auth can-i --list`, then attempt access to
resources outside the dedicated namespace and to unrelated platform namespaces.

### AS-SEC-003 — Default sandbox blueprint does not explicitly harden Pod/container security

**Severity:** high for a product whose value proposition is stronger isolation.  
**Evidence:** pinned `config/sandbox.yaml` does not set `securityContext`, seccomp profile,
`allowPrivilegeEscalation: false`, dropped Linux capabilities, `runAsNonRoot`, read-only root
filesystem, host namespace restrictions or an explicit ServiceAccount policy.

**Impact:** effective confinement depends on cluster defaults, the container runtime and the chosen
image rather than a self-contained hardened sandbox profile.

**Platform requirement:** use a platform-reviewed blueprint that explicitly defines the intended
security context. Treat stronger runtime classes (for example gVisor/Kata) as separate execution
profiles that require their own compatibility and bypass tests.

**Live gate:** inspect the rendered Pod, runtime class, seccomp/capabilities/UID, mount list and host
namespace flags; then run the malicious-workload fixtures.

### AS-SEC-004 — Sandbox Pod does not disable ServiceAccount token automount

**Severity:** medium/high depending on namespace/default-ServiceAccount RBAC.  
**Evidence:** the pinned sandbox blueprint does not set `automountServiceAccountToken: false` and
does not specify a dedicated sandbox ServiceAccount.

**Impact:** Kubernetes defaults can mount a ServiceAccount token into sandbox workloads. A default
ServiceAccount often has little or no RBAC, but exposing an authenticated cluster credential to
untrusted code is unnecessary and becomes dangerous if bindings change later.

**Platform requirement:** explicitly disable token automount for untrusted sandbox Pods unless a
specific workload profile proves it is required. Never inherit ambient namespace credentials.

**Live gate:** verify no projected ServiceAccount token is readable from a protected sandbox and
verify Kubernetes API calls fail as expected.

### AS-SEC-005 — Environment variables are serialized into ReplicaSet annotation data

**Severity:** high for secret-bearing workloads.  
**Evidence:** the upstream `Sandbox` struct contains `EnvVars map[string]string`. `rs_manager.go`
JSON-serializes the full `Sandbox` object into `RawData`, and the default blueprint stores
`RawData` in the ReplicaSet `sandbox-data` annotation.

**Impact:** credentials supplied through sandbox environment variables can be duplicated into
Kubernetes object metadata. Any principal able to read the ReplicaSet/annotation may recover those
values. This conflicts with #34's requirement to minimise effective secret material and avoid
persisting secrets in broad control-plane metadata.

**Platform requirement:** do **not** project platform secrets through upstream `EnvVars` on the
reviewed default blueprint. A supported profile needs a secret-delivery mechanism that does not
serialize secret values into `sandbox-data` (for example a custom hardened blueprint using scoped
Secret references or an ephemeral delivery channel) and must prove cleanup/revocation.

**Live gate:** inject a synthetic canary credential using the proposed platform path and prove it is
absent from ReplicaSet annotations, events, logs, API responses and retained evidence.

### AS-SEC-006 — Runtime class is selected from sandbox metadata

**Severity:** medium; potentially high if arbitrary callers can select privileged runtime classes.  
**Evidence:** the default blueprint conditionally sets `runtimeClassName` from
`.Sandbox.Metadata.runtimeClassName`; sandbox metadata overrides template metadata in the reviewed
source.

**Impact:** allowing arbitrary agent/user metadata to reach this field would make the effective
isolation runtime caller-selectable.

**Platform requirement:** runtime class is a deployment/profile decision, never free-form agent
input. The platform adapter/deployment layer must pin or allowlist approved runtime classes and
strip provider-specific runtime metadata from model-controlled input.

**Live gate:** attempt to request an unapproved runtime class and prove it is rejected before
provider dispatch.

### AS-SEC-007 — Default sandbox image pull policy does not guarantee immutable provenance

**Severity:** medium.  
**Evidence:** the default blueprint uses `imagePullPolicy: IfNotPresent`, while sandbox/template
configuration supplies the image reference.

**Impact:** mutable tags and cached node images can produce non-reproducible or stale execution
images. This weakens forensic evidence and security update guarantees.

**Platform requirement:** protected profiles should use digest-pinned images (or another explicit
immutable provenance policy), record the resolved image identity, and define a controlled update
process.

**Live gate:** verify the running Pod image ID/digest matches the approved profile and cannot be
overridden by untrusted request data.

### AS-SEC-008 — Network posture is allow-by-default upstream

**Severity:** high for untrusted code if deployed without policy projection.  
**Evidence:** upstream API/networking documentation states `allowInternetAccess` defaults to true and
documents deny/allow rules.

**Impact:** arbitrary/model-generated code can exfiltrate data unless the platform deliberately
overrides the provider default and the effective enforcement cannot be bypassed.

**Platform requirement:** protected profiles default to deny. #15/#43 remain policy authority;
provider rules are only an enforcement projection. The proof-of-concept adapter therefore starts
with Internet access disabled.

**Live gate:** verify IPv4/IPv6, DNS, redirects, private/link-local/cluster/metadata destinations and
a direct-socket bypass fixture. Fail closed if enforcement is unavailable.

## Positive source-level properties worth retaining in the evaluation

The static review also found useful properties:

- the controller RBAC in the reviewed manifest is a namespace `Role`, not a `ClusterRole`;
- sandbox CPU and memory requests/limits are rendered into the default Pod spec;
- runtime class can be selected by deployment/profile metadata, enabling stronger-runtime
  experiments without changing canonical platform contracts;
- the provider exposes explicit network configuration inputs;
- the platform can remain independent because the PoC requires no upstream dependency in core.

These properties justify continuing the evaluation, but none cancels the findings above.

## Protected-profile minimum before live acceptance

A representative live profile should at minimum use:

1. a dedicated Kubernetes namespace;
2. generated/rotated provider API credentials rather than the public manifest token;
3. a dedicated hardened sandbox blueprint;
4. `automountServiceAccountToken: false` for untrusted workloads;
5. explicit non-root/seccomp/capability/privilege controls where compatible;
6. a pinned approved runtime class for the profile;
7. digest-pinned approved images;
8. deny-by-default egress plus canonical allow projection;
9. no secret values in provider metadata/annotations;
10. explicit CPU/memory/ephemeral-storage/process constraints;
11. network separation between sandbox workloads, provider control plane and unrelated platform
    services;
12. retained evidence for rendered Pod spec, effective RBAC and each isolation/bypass fixture.

## Current security recommendation

Continue #798 as an **evaluation-only optional provider**. Do not adopt the reviewed default
`install.yaml` + `config/sandbox.yaml` unchanged as a production high-isolation profile. A final
`adopt`/`optional_provider_only` recommendation requires a hardened profile plus live evidence for
all high-severity findings above.
