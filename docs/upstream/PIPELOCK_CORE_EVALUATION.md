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

The platform must make the authorization and disclosure decision before the adapter is consulted.
Pipelock-native configuration, rule IDs, policy hashes, receipts and session state are adapter
metadata/evidence only. They cannot grant an action denied by #15 or #591 and cannot satisfy a
platform `REQUIRE_APPROVAL` outcome.

The initial platform-owned projection is implemented in
`ai_multi_agent_platform.adapters.pipelock`. It intentionally has no Pipelock package/runtime
import, so the normal local/reference path remains functional when Pipelock is absent.

## Exact license and build boundary

The reviewed revision contains two materially different license regions:

- the repository root `LICENSE` is Apache-2.0;
- `enterprise/LICENSE` is Elastic License 2.0;
- reviewed Enterprise source files use the Go build constraint `//go:build enterprise` and carry
  an Elastic License 2.0 notice;
- the upstream `Makefile` `build` target invokes `go build` without the `enterprise` build tag;
- the upstream repository `Dockerfile` explicitly invokes `go build -tags enterprise`.

Consequences for this evaluation:

1. **The repository Dockerfile is not treated as a Core-only artifact.** Building it at the reviewed
   revision compiles the Enterprise-tagged source path.
2. The no-paid-service evaluation baseline should use a source checkout pinned to the exact reviewed
   revision and the tag-free `make build` / equivalent `go build` path.
3. Prebuilt artifacts are not accepted as Core-only merely because paid functionality is inactive.
   Their build provenance must be established separately before they are used as evaluation evidence.
4. No Pipelock source is copied, vendored or selectively ported into this repository by #730.

This distinction is stricter than a repository-level license summary: the actual build path matters.

## Canonical decision mapping

The adapter mapping is deliberately asymmetric: Pipelock may add a stricter technical block, but it
may never turn a platform non-allow outcome into an allow.

| Canonical #591 outcome | Pipelock disabled | Audit-only profile | Enforced profile |
| --- | --- | --- | --- |
| `ALLOW` | skip adapter | audit/observe | mediate before egress |
| `DENY` | block | block before Pipelock | block before Pipelock |
| `REQUIRE_APPROVAL` | block pending #15 | block pending #15 | block pending #15 |
| `LOCAL_ONLY` | block external path | block external path | block external path |
| `UNKNOWN_BLOCKED` | fail closed | fail closed | fail closed |

A future/unmapped canonical outcome must fail closed. Approval remains owned by #15; after approval,
the platform must re-evaluate the canonical request rather than asking Pipelock to substitute its own
workflow.

The projection preserves the platform `policy_version` and creates a deterministic
`decision_digest` binding the request payload digest, target, profile reference and canonical
outcome. The latter is an evidence binding for #730, **not** a replacement for #591 policy identity.

## Audit-only pilot baseline

The first live pilot should be generated from the reviewed Core source rather than by copying the
large upstream preset into this repository:

```bash
git checkout f7d1816f1a5ad63d501b0c48f36066f836f59022
make build
./pipelock init --preset audit --output ./pipelock-audit.yaml --skip-canary
```

At the reviewed revision the upstream audit preset declares `mode: audit` and `enforce: false` and
uses warning actions for request/response findings. The upstream init tests also verify that generated
configuration provisions a flight recorder and Ed25519 signing material with target redaction enabled.

Before a pilot run is accepted as evidence, record:

- binary SHA-256 and `pipelock` version/build metadata;
- exact source revision and build command;
- generated configuration digest;
- signing public key fingerprint used for receipt verification;
- platform execution profile and canonical policy version;
- transport under test;
- whether host/network containment was active.

Audit mode is observational only for platform-canonical `ALLOW` traffic. Canonical denials are still
blocked by the platform before audit traffic reaches Pipelock.

## Transport audit

Source/documentation review at the pinned revision shows receipt/enforcement surfaces for the
following transports. These are **upstream capabilities, not yet platform conformance claims**.

| Transport | Upstream reviewed surface | Platform live validation |
| --- | --- | --- |
| HTTP fetch | URL/request/response scanning and receipts | pending |
| HTTP forward proxy | request/response, CONNECT/TLS interception paths | pending |
| WebSocket | frame/request scanning and receipts | pending |
| MCP stdio | input/tool/response scanning, tool-policy and chain detection | pending |
| MCP HTTP upstream/listener | MCP scanning/receipt surfaces | pending |
| MCP WebSocket | MCP response/scanning surface | pending |
| Redirect/private-target handling | documented proxy/scanner enforcement points | pending |
| DNS/rebinding/hostname-IP behavior | security corpus target for #730 | pending explicit test |

Unsupported or untested platform profiles must remain labelled unsupported. Documentation coverage is
not evidence of complete mediation in this platform.

## Receipt / Flight Recorder assessment

At the pinned revision Pipelock documents Ed25519-signed action receipts in a tamper-evident chain.
Receipt metadata includes an action ID, verdict, transport, target, layer, request correlation,
Pipelock configuration `policy_hash`, signer key and chain linkage. Verification with a pinned public
key is materially stronger than an unpinned structural check.

The platform adapter therefore treats receipt data as **external enforcement evidence** only:

- canonical request/correlation/task/run/agent/capability references come from the platform
  projection, not from the receipt;
- canonical `policy_version` and `decision_digest` are retained beside the upstream evidence;
- Pipelock `policy_hash` remains namespaced adapter evidence and is not relabelled as #591 policy;
- cryptographic verification state must be supplied by an external verifier result; a receipt cannot
  mark itself verified;
- raw receipt `target` values are reduced to a SHA-256 destination digest by the initial normalizer;
- raw rule/pattern text is not persisted by the normalizer, avoiding accidental secret/protected-data
  retention.

Pipelock documents that receipt emission is best-effort by default. Its `require_receipts` option can
make allow-path receipt failure block before forwarding. An enforced platform profile that relies on
receipts should use fail-closed semantics; audit-only degradation may remain profile-configurable.

## Bypass-resistance assessment

A proxy/stdio wrapper does not by itself prove complete host-level mediation. In particular, a child
process launched through MCP stdio can potentially create an independent socket unless the execution
profile also constrains network access outside the proxy.

Therefore #730 must not claim protected-profile enforcement until tests prove the complete path.
Recommended interpretation:

- **audit-only:** direct paths may be measured and reported, but they are not called protected;
- **enforced HTTP/MCP profile:** all outbound paths must be forced through the mediation boundary by
  process/container/network controls;
- **MCP stdio child direct-network access:** unsupported for enforcement until OS/container-level
  containment proves that direct egress cannot bypass the proxy;
- **alternate Hermes/tool/browser/connector paths:** each requires an explicit mediation test rather
  than inheriting a claim from another transport.

If complete mediation cannot be demonstrated, the profile remains unsupported rather than silently
falling back to direct network access.

## Failure semantics

| Profile | Pipelock unavailable | Required behavior |
| --- | --- | --- |
| disabled | irrelevant | canonical platform path continues |
| audit-only | degraded evidence | canonical policy still applies; explicit audit degradation may continue |
| enforced | enforcement unavailable | fail closed; do not use an unmediated fallback |

Recovery must not replay or recreate canonical policy state. Pipelock state/receipts are evidence;
Task/Run lifecycle and #15/#591 state remain platform-owned.

## Security corpus to execute

The live phase must add reproducible fixtures for:

- tool-description poisoning and changed tool metadata/schema after discovery;
- prompt/instruction injection in MCP results;
- known-secret, token/high-entropy and protected-data patterns;
- encoding/normalization variants and multi-request exfiltration chains;
- loopback, RFC1918/private, link-local and cloud metadata targets;
- redirect to a private target;
- DNS rebinding and hostname/IP resolution changes;
- IPv6 equivalents;
- WebSocket request/response exfiltration;
- Pipelock outage during audit and enforced profiles;
- direct-network bypass from ordinary HTTP, MCP HTTP, MCP stdio child, WebSocket and representative
  Hermes/tool/browser/connector paths.

## Performance evidence still required

No performance/adoption claim is made by this source-audit/projection stage. The live pilot must still
measure baseline versus mediated workloads for request latency, MCP latency, WebSocket overhead, CPU,
RAM, startup, log/disk growth, false positives and false negatives on an ordinary single-node/VPS
profile.

## Comparison baseline

The measured phase should compare three paths:

1. canonical #591 application-layer gate only;
2. the smallest direct/reference mediation available for the same transport;
3. canonical gate plus Pipelock Core mediation/evidence.

The useful question is whether Pipelock materially improves technical mediation and verifiable
evidence, not whether it has more features.

## Current decision status

**No final #730 adoption outcome yet.** Pipelock remains a `candidate` until the live transport,
bypass, failure and performance corpus is executed. A final issue decision must be exactly one of
`adopt`, `optional_provider`, `reference_only` or `reject` and must cite the measured evidence.

The current source review supports continuing the proof of concept because the architecture can stay
optional and the tag-free Core build has a separable Apache-2.0 path, but it does **not** yet justify
production adoption.
