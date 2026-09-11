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

The live runs record per-run binary hashes. Multiple tag-free builds at the same source pin/toolchain
have not produced the same SHA-256, so byte-for-byte reproducible builds are **not** currently
claimed. The generated audit configuration hash has remained stable across the recorded runs.

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

The candidate workflow `.github/workflows/pipelock-candidate-compat.yml` is an executable compatibility
gate against the exact reviewed source. Its intended equivalent begins with:

```bash
git checkout f7d1816f1a5ad63d501b0c48f36066f836f59022
make build
./pipelock init --preset audit --output ./pipelock-audit.yaml --skip-canary
```

The workflow verifies the root/Enterprise licenses, Enterprise build constraints, Dockerfile build
tag, tag-free `make build`, binary build metadata and generated audit configuration. It records SHA-256
fingerprints for the built binary and generated configuration as non-secret CI evidence.

At the reviewed revision the generated upstream audit profile declares `mode: audit` and
`enforce: false`. The generated configuration passes the upstream validation exercised by
`pipelock init`; the recorded pilot reports **68 passed, 0 failed**.

Two green live runs now cover different parts of the matrix:

- run `34570647932`: audit-only HTTP `/fetch`, canonical platform MCP stdio, signed allow receipt and
  pinned-key proxy writer-chain verification;
- run `34577649075`: canonical MCP Streamable HTTP and MCP WebSocket upstreams, plaintext HTTP forward
  proxy, HTTPS CONNECT without TLS interception, plus another successful strict `/fetch` receipt-chain
  verification.

The generated `pipelock init --preset audit` configuration used in CI did not enable the forward proxy,
so the forward/CONNECT probe uses a controlled copy in which only `forward_proxy.enabled` is changed
to `true`. Its SHA-256 is recorded separately. This is a test-profile mutation, not a claim that the
unmodified generated profile exposes forward proxying.

Exact hashes, receipt identifiers, chain statistics, transport results and measurement limitations are
recorded in `PIPELOCK_CORE_LIVE_EVIDENCE.md`.

Canonical denials are blocked by the platform before audit traffic reaches Pipelock.

## Transport audit

Source/documentation review at the pin exposes these upstream enforcement/receipt surfaces. A row is
marked live-validated only where the platform candidate workflow has exercised that exact class.

| Transport | Upstream reviewed surface | Platform live validation |
| --- | --- | --- |
| HTTP fetch | URL/request/response scanning and receipts | **validated, audit-only** (`34570647932`, `34577649075`) |
| HTTP forward | absolute-URI request/response path | **validated, audit-only** (`34577649075`) |
| HTTPS CONNECT | tunnel admission and metadata; opaque without TLS interception | **validated for tunnel connectivity** (`34577649075`); CONNECT-specific signed receipt remains unresolved |
| Generic WebSocket `/ws` | request/frame scanning and receipts | pending |
| MCP stdio | input/tool/response scanning, policy and chain detection | **validated for canonical platform fixture** (`34570647932`, later runs retain it) |
| MCP Streamable HTTP upstream | MCP scanning/remote-upstream wrapper | **validated for canonical platform fixture** (`34577649075`) |
| MCP WebSocket upstream | MCP scanning/remote-upstream wrapper | **validated for canonical platform fixture** (`34577649075`) |
| Redirect/private target | documented scanner/proxy decision points | pending |
| DNS/rebinding/hostname-IP | explicit #730 security-corpus target | pending |

The CONNECT result must be interpreted narrowly. TLS interception was off, so Pipelock did not inspect
inner HTTPS headers, request bodies or response content. The run proved successful tunnel admission
and closure only. It also logged a best-effort CONNECT allow-receipt emission error (`chain sealed:
transcript root already emitted`). Because `require_receipts` was false for that probe, transport
continued as designed; however, CONNECT-specific signed-receipt completeness is **not** claimed.

Untested/unsupported platform profiles remain labelled unsupported. The MCP results do not imply that
a wrapped child process is prevented from creating an independent network socket.

## Receipt / Flight Recorder assessment

At the pin, Pipelock documents Ed25519-signed action receipts in a tamper-evident chain. Receipt
metadata includes action ID, verdict, transport, target, layer, request correlation, Pipelock
`policy_hash`, signer key and chain linkage. Verification with a pinned public key is stronger than an
unpinned structural check.

Run `34570647932` provides the first live evidence for this boundary. With `require_receipts: true`, the
candidate returned an action correlation ID and produced a proxy writer chain that the pinned verifier
accepted against the externally retained public key:

- receipts: 19;
- final sequence: 18;
- root hash: `249ce0676ad2efbe0b6077ae493cfb73859b1db2c713fff5de3b6b896a9abf6f`;
- signer: `4368c51a94c85941bf0277a2c4dcda535f5400320b60a127ee3e9f3e68d3427e`.

Run `34577649075` independently verified another strict `/fetch` writer chain:

- receipts: 43;
- final sequence: 42;
- root hash: `89777ad5822ebd60e84ea50ff06e35bc9e293f91e935ffd95cb8a8698a114413`;
- signer: `b634a7019e61d9476a9aaa4e1b55268a13dafe44a4b250cf7707f87c89c6f47a`.

Both verifier runs report containment as **UNKNOWN**. This is the intended #730 interpretation: receipt
validity is evidence integrity/provenance, not complete-mediation evidence.

The second transport run also exposes an evidence-quality caveat: its successful CONNECT tunnel logged
a best-effort receipt-emission failure (`chain sealed: transcript root already emitted`). The strict
`/fetch` chain later in that run verifies that the recorder/verifier path itself remained functional,
but it does not prove the CONNECT action had a valid signed receipt. A dedicated strict CONNECT
reproduction is therefore required before CONNECT receipt coverage can be called validated.

The platform normalizer treats receipts as external evidence only:

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

The successful receipt verifier reinforces this limitation by reporting `L-CONTAINMENT-UNPROVEN` and
`Containment: UNKNOWN` for the hosted candidate runs.

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

The adapter contract tests exercise these mappings. A live process-outage/recovery pilot for an
enforced deployment is still pending.

## Security corpus still required

The live phase must execute reproducible fixtures for:

- tool-description poisoning, descriptor/schema drift and MCP response injection;
- known-secret, high-entropy/token and protected-data patterns;
- encoding/normalization variants and multi-request exfiltration chains;
- loopback, RFC1918/private, link-local and cloud-metadata targets;
- redirect to private targets, DNS rebinding and hostname/IP changes;
- IPv6 equivalents;
- generic WebSocket `/ws` exfiltration/frame handling;
- audit/enforced Pipelock outage;
- direct-network bypass from HTTP, MCP HTTP, MCP stdio child, WebSocket and representative
  Hermes/tool/browser/connector paths.

## Performance evidence still required

The hosted candidate runs provide smoke timings only. Recorded transport samples include:

- `/fetch`: 50.663 ms / 36.345871 ms Pipelock log in an early run; 64.231 ms in run
  `34577649075`;
- HTTP forward in `34577649075`: 53.987 ms curl end-to-end, 42.589163 ms Pipelock request log;
- HTTPS CONNECT in `34577649075`: 46.614 ms curl end-to-end, 55.935855 ms tunnel duration reported by
  Pipelock;
- MCP remote compatibility JUnit: 1.601 s for the Streamable HTTP testcase and 0.622 s for the MCP
  WebSocket testcase, both including fixture/process startup.

These samples include external network/runner variance and have no same-run direct-control baseline,
so they are **not** used as a production overhead measurement.

The measured phase must compare baseline and mediated paths for request/MCP/WebSocket latency, CPU,
RAM, startup, log/disk growth, false positives and false negatives on an ordinary single-node/VPS
profile.

Comparison should cover:

1. canonical #591 application-layer gate only;
2. the smallest direct/reference mediation for the same transport where available;
3. canonical gate plus Pipelock Core mediation/evidence.

The decision criterion is added enforcement strength and verifiable evidence, not feature count.

## Current decision status

**No final #730 adoption outcome yet.** Pipelock remains `candidate` until the remaining generic
WebSocket, redirect/private-target, bypass, failure, adversarial and performance evidence is executed
and the CONNECT receipt anomaly is understood. The final issue decision must be exactly one of
`adopt`, `optional_provider`, `reference_only` or `reject` and cite the measured evidence.

The current evidence supports continuing the proof of concept: the canonical architecture remains
independent, a tag-free Apache-2.0 source path exists, HTTP fetch/forward and CONNECT transport work in
the tested audit profiles, canonical MCP stdio/Streamable-HTTP/WebSocket fixtures work through the
wrapper, and pinned-key receipt chains have been verified. It does **not** justify production adoption
or a protected-profile claim. The Enterprise-tagged Dockerfile/GoReleaser paths also keep a pinned
source-built Core artifact as the clearer licensing baseline for this project.
