# Pipelock Core live evaluation evidence

This document records executable evidence for issue #730. It is intentionally narrower than the
final adoption decision: passing evidence here proves only the exact build/profile/transport named in
each entry.

## Evidence E1: tag-free Core build and audit-only HTTP fetch

- **Platform PR:** #754
- **GitHub Actions run:** `34563334446`
- **Workflow:** `Pipelock candidate compatibility`
- **Job:** `pinned-core-audit-pilot`
- **Upstream repository:** `https://github.com/luckyPipewrench/pipelock`
- **Pinned upstream revision:** `f7d1816f1a5ad63d501b0c48f36066f836f59022`
- **Go toolchain:** `go1.25.0`
- **Execution environment:** GitHub-hosted Ubuntu runner
- **Profile:** generated upstream `audit` preset
- **Transport exercised:** Pipelock HTTP `/fetch`
- **Destination:** `https://example.com/`

### Build and license-boundary assertions

The run checked the reviewed repository state before execution:

- root license contains Apache License 2.0 text;
- `enterprise/LICENSE` contains Elastic License 2.0 text;
- reviewed Enterprise source is guarded by `//go:build enterprise`;
- the repository Dockerfile explicitly builds with `-tags enterprise`;
- the normal GoReleaser `pipelock` build is Enterprise-tagged;
- the `Makefile` `build` target does not enable the Enterprise build tag;
- `go version -m` on the produced candidate binary contained no Enterprise build tag.

The evaluated binary was therefore built from the exact source pin through the tag-free Core build
path rather than taken from the repository Dockerfile or normal release archive.

### Reproducibility fingerprints

- **Candidate binary SHA-256:**
  `3637525c41832e9eb2abb9f09e9bd6a8b023712a063f2fe90a8b5525d73d186f`
- **Generated audit configuration SHA-256:**
  `da732fc3e9800a3223634ef5aad3b960bf13ca32a879be3eb4705055dcee31ad`
- **Workflow evidence artifact SHA-256:**
  `0620f98e1ca3f0de36c10408d943be2be243675ce9367b77a2a69dfe87535e1f`

The generated configuration passed Pipelock's own validation with **68 passed, 0 failed** and declared
`mode: audit` with `enforce: false`.

### Live HTTP result

The workflow started the tag-free candidate on loopback and issued a real request through
`/fetch?url=https://example.com/`.

Observed on this one GitHub-hosted runner sample:

- request result: allowed / HTTP 200 from the destination;
- returned content size reported by Pipelock: 559 bytes;
- curl end-to-end request time through `/fetch`: **0.050663 s (50.663 ms)**;
- Pipelock request log `duration_ms`: **36.345871 ms**.

These numbers are a smoke measurement, not a performance baseline. They include external-network and
runner variability, contain only one measured request, and have no same-run direct-control sample.
They must not be used to claim production latency overhead or VPS suitability.

### What E1 proves

E1 supports only these claims:

1. the exact pinned source revision can build through the reviewed tag-free Core path;
2. the resulting binary can generate and load an audit-only configuration;
3. the generated configuration passes the upstream validation suite exercised by `pipelock init`;
4. the candidate can perform one real HTTP fetch while running in audit mode;
5. ordinary platform operation does not require adding Pipelock as a Python dependency.

E1 does **not** prove complete mediation, WebSocket or MCP compatibility, SSRF/DLP resistance,
network-bypass resistance, fail-closed enforcement, representative latency/resource cost or an
adoption recommendation.

## Evidence E2: signed allow receipt verification

**Status: pending successful rerun.**

The next compatibility revision adds a controlled copy of the generated audit configuration with
`flight_recorder.require_receipts: true`, derives the corresponding public key, performs an HTTP fetch,
requires an `X-Pipelock-Receipt` correlation header, locates the generated JSONL evidence and verifies
the receipt chain using `pipelock verify-receipt --chain ... --key ...`.

An initial attempt correctly failed before execution because the workflow's text mutation damaged YAML
indentation. That was a test-fixture defect, not an upstream runtime result. The mutation now preserves
the generated indentation and E2 remains unproven until the corrected workflow is green.

Private signing material and raw receipt payloads are deliberately not uploaded as CI artifacts. The
intended retained evidence is limited to the public key, action correlation ID, receipt-file digest,
verification output, build metadata and non-secret runtime logs.

## Remaining #730 evidence

Still required before the issue can reach a final `adopt`, `optional_provider`, `reference_only` or
`reject` decision:

- representative HTTP forward/CONNECT behavior and redirects;
- WebSocket behavior;
- MCP stdio and MCP HTTP behavior;
- descriptor/tool drift and response-injection corpus;
- secret/DLP, encoding and multi-stage exfiltration corpus;
- loopback/private/link-local/metadata, DNS-rebinding and IPv6 corpus;
- direct-network bypass tests, including child-process direct sockets;
- controlled outage/recovery tests for audit-only and enforced profiles;
- baseline-vs-mediated latency plus CPU, RAM, startup and disk/log measurements;
- false-positive and false-negative measurements on the maintained corpus;
- an ordinary single-node/VPS measurement rather than only GitHub-hosted runners.
