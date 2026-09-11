# Pipelock Core evaluation

Status: **candidate / proof of concept for #730**. This document does not approve or integrate
Pipelock as a required platform component.

## Candidate identity

- **Project:** Pipelock
- **Canonical upstream:** `https://github.com/luckyPipewrench/pipelock`
- **Reviewed revision:** `f7d1816f1a5ad63d501b0c48f36066f836f59022`
- **Review date:** 2026-09-11
- **Proposed category:** optional external self-hosted enforcement/evidence adapter
- **Platform boundary:** canonical #15 authorization + #591 egress decision -> optional adapter
- **Required for baseline:** no
- **Recurring paid service required:** no

## Architecture invariant

Pipelock is not a policy authority for this platform.

```text
#15 Authorization / Approval
            +
#591 canonical data-egress policy
            |
            v
      EgressDecision
            |
            v
 optional Pipelock adapter
            |
            +--> technical mediation
            +--> external evidence
```

The platform makes the authorization and disclosure decision first. Pipelock-native configuration,
rules, policy hashes, receipts and session state remain adapter metadata/evidence. They cannot grant
an action denied by #15 or #591 or satisfy a platform `REQUIRE_APPROVAL` outcome.

The initial platform-owned projection is implemented in
`ai_multi_agent_platform.adapters.pipelock`. It has no Pipelock package/runtime import, so the normal
local/reference path remains functional when Pipelock is absent.

## Exact license, build and release boundary

The reviewed revision contains materially different license/build regions:

- root `LICENSE`: Apache-2.0;
- `enterprise/LICENSE`: Elastic License 2.0;
- reviewed Enterprise source uses `//go:build enterprise`;
- upstream `Makefile` target `build` invokes `go build` without the `enterprise` tag;
- upstream repository `Dockerfile` invokes `go build -tags enterprise`;
- upstream `.goreleaser.yaml` sets `tags: [enterprise]` for the normal `pipelock` release binary.

Consequences:

1. **The repository Dockerfile is not a Core-only build path.**
2. **The normal GoReleaser binary/archive is not a Core-only build path at this revision.** Published
   release/container packaging derived from that binary must not be assumed Apache-Core-only merely
   because paid features are inactive.
3. The no-paid-service #730 baseline uses the exact pinned source revision and tag-free `make build`
   path. The compatibility workflow also inspects the built binary metadata for an Enterprise build
   tag.
4. No Pipelock source is copied, vendored or selectively ported into this repository.

This is intentionally stricter than a repository-level license summary: the build path of the actual
artifact matters.

## Canonical decision mapping

The mapping is asymmetric: Pipelock may add a stricter technical block, but it may never turn a
platform non-allow into an allow.

| Canonical #591 outcome | Pipelock disabled | Audit-only profile | Enforced profile |
| --- | --- | --- | --- |
| `ALLOW` | skip adapter | audit/observe | mediate before egress |
| `DENY` | block | block before Pipelock | block before Pipelock |
| `REQUIRE_APPROVAL` | block pending #15 | block pending #15 | block pending #15 |
| `LOCAL_ONLY` | block external path | block external path | block external path |
| `UNKNOWN_BLOCKED` | fail closed | fail closed | fail closed |

A future/unmapped canonical outcome fails closed. Approval stays owned by #15; after approval, the
platform re-evaluates the canonical request rather than delegating approval state to Pipelock.

The projection preserves `policy_version` and creates a deterministic `decision_digest` binding the
payload digest, target, profile reference and canonical result. The digest is #730 evidence binding,
not a replacement for #591 policy identity.

## Reproducible audit-only Core pilot

The candidate workflow `.github/workflows/pipelock-candidate-compat.yml` performs the first executable
compatibility gate against the exact reviewed source. Its intended equivalent is:

```bash
git checkout f7d1816f1a5ad63d501b0c48f36066f836f59022
make build
./pipelock init --preset audit --output ./pipelock-audit.yaml --skip-canary
```

The workflow verifies the root/Enterprise licenses, Enterprise build constraints, Dockerfile build
tag, tag-free `make build`, binary build metadata and generated audit configuration. It records SHA-256
fingerprints for the built binary and generated configuration as non-secret CI evidence.

At the reviewed revision the upstream audit preset declares `mode: audit` and `enforce: false`. The
upstream init tests also assert flight-recorder provisioning, Ed25519 signing material and target
redaction for generated configuration.

A later transport pilot should additionally retain the signing public-key fingerprint, platform
execution profile, canonical policy version, transport under test and whether host/network containment
was active.

Canonical denials are blocked by the platform before audit traffic reaches Pipelock.

## Transport audit

Source/documentation review at the pin exposes these upstream enforcement/receipt surfaces. Until a
live platform test exercises a row, it is **not** a platform conformance claim.

| Transport | Upstream reviewed surface | Platform live validation |
| --- | --- | --- |
| HTTP fetch | URL/request/response scanning and receipts | pending |
| HTTP forward/CONNECT | request/response and intercepted paths | pending |
| WebSocket | request/frame scanning and receipts | pending |
| MCP stdio | input/tool/response scanning, policy and chain detection | pending |
| MCP HTTP upstream/listener | MCP scanning/receipt surfaces | pending |
| MCP WebSocket | MCP scanning/receipt surface | pending |
| Redirect/private target | documented scanner/proxy decision points | pending |
| DNS/rebinding/hostname-IP | explicit #730 security-corpus target | pending |

Untested/unsupported platform profiles remain labelled unsupported.

## Receipt / Flight Recorder assessment

At the pin, Pipelock documents Ed25519-signed action receipts in a tamper-evident chain. Receipt
metadata includes action ID, verdict, transport, target, layer, request correlation, Pipelock
`policy_hash`, signer key and chain linkage. Verification with a pinned public key is stronger than an
unpinned structural check.

The platform normalizer treats this as external evidence only:

- canonical request/correlation/task/run/agent/capability references come from the platform;
- canonical `policy_version` and `decision_digest` remain beside the evidence;
- Pipelock `policy_hash` stays adapter evidence and is not relabelled as #591 policy;
- verification state is supplied by an external verifier result, never trusted from the receipt;
- raw receipt targets become SHA-256 destination digests;
- raw rule/pattern text is not persisted by the initial normalizer.

Pipelock documents receipt emission as best-effort by default. `flight_recorder.require_receipts` can
make allow-path receipt failure fail closed before upstream forwarding. An enforced platform profile
that depends on receipts should use fail-closed behavior.

## Bypass-resistance assessment

A proxy or MCP stdio wrapper is not proof of complete host-level mediation. A child process can in
principle create an independent socket unless the execution environment also constrains direct network
access.

Therefore:

- **audit-only:** direct paths may be observed but are not called protected;
- **enforced profile:** all outbound paths must be forced through the mediation boundary by
  process/container/network controls;
- **MCP stdio child direct-network access:** unsupported for enforcement until OS/container network
  containment proves it cannot bypass the proxy;
- **Hermes/tool/browser/connector alternatives:** each requires an explicit mediation test.

If complete mediation is not demonstrable, the profile stays unsupported instead of silently falling
back to direct egress.

## Failure semantics

| Profile | Pipelock unavailable | Required behavior |
| --- | --- | --- |
| disabled | irrelevant | canonical path continues |
| audit-only | degraded evidence | canonical policy still applies; explicit degradation may continue |
| enforced | enforcement unavailable | fail closed; no unmediated fallback |

Recovery does not recreate canonical policy state. Pipelock state/receipts are evidence; Task/Run and
#15/#591 state remain platform-owned.

## Security corpus still required

The live phase must execute reproducible fixtures for:

- tool-description poisoning, descriptor/schema drift and MCP response injection;
- known-secret, high-entropy/token and protected-data patterns;
- encoding/normalization variants and multi-request exfiltration chains;
- loopback, RFC1918/private, link-local and cloud-metadata targets;
- redirect to private targets, DNS rebinding and hostname/IP changes;
- IPv6 equivalents;
- WebSocket exfiltration;
- audit/enforced Pipelock outage;
- direct-network bypass from HTTP, MCP HTTP, MCP stdio child, WebSocket and representative
  Hermes/tool/browser/connector paths.

## Performance evidence still required

No production/performance claim follows from source-build compatibility. The measured phase must
compare baseline and mediated paths for request/MCP/WebSocket latency, CPU, RAM, startup, log/disk
growth, false positives and false negatives on an ordinary single-node/VPS profile.

Comparison should cover:

1. canonical #591 application-layer gate only;
2. the smallest direct/reference mediation for the same transport where available;
3. canonical gate plus Pipelock Core mediation/evidence.

The decision criterion is added enforcement strength and verifiable evidence, not feature count.

## Current decision status

**No final #730 adoption outcome yet.** Pipelock remains `candidate` until transport, bypass, failure
and performance evidence is executed. The final issue decision must be exactly one of `adopt`,
`optional_provider`, `reference_only` or `reject` and cite the measured evidence.

The current review supports continuing the proof of concept: the canonical architecture remains
independent and a tag-free Apache-2.0 source path exists. It does **not** justify production adoption,
and the Enterprise-tagged Dockerfile/GoReleaser paths make a source-built Core baseline the safer and
clearer licensing choice for this project.
