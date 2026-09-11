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

### Run-specific fingerprints

- **Candidate binary SHA-256:**
  `3637525c41832e9eb2abb9f09e9bd6a8b023712a063f2fe90a8b5525d73d186f`
- **Generated audit configuration SHA-256:**
  `da732fc3e9800a3223634ef5aad3b960bf13ca32a879be3eb4705055dcee31ad`
- **Workflow evidence artifact SHA-256:**
  `0620f98e1ca3f0de36c10408d943be2be243675ce9367b77a2a69dfe87535e1f`

The generated configuration passed Pipelock's own validation with **68 passed, 0 failed** and declared
`mode: audit` with `enforce: false`.

A later green run at the same source pin/toolchain produced a different binary SHA-256 while retaining
the same generated audit-config SHA-256. These values are therefore treated as run fingerprints, not
as evidence of byte-for-byte reproducible builds. Deterministic binary reproducibility remains
unproven.

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

**Status: verified on GitHub Actions run `34570647932`.**

The successful run used the same exact upstream pin and tag-free build path, copied the generated
audit configuration, enabled `flight_recorder.require_receipts: true`, derived the corresponding
public key, performed an HTTP fetch, required an `X-Pipelock-Receipt` correlation header and verified
the complete proxy writer chain with the pinned public key.

### Retained evidence

- **Workflow run:** `34570647932`
- **Workflow evidence artifact digest:**
  `sha256:99e6fbbd073d6f234aa1a86de74af8717611ff9ac5572be7b5aa12bf1471d975`
- **Run-specific candidate binary SHA-256:**
  `fe6e0dd3e310fbf950c376324477486294e73ef23153df9ef45fdac5c221635d`
- **Generated audit configuration SHA-256:**
  `da732fc3e9800a3223634ef5aad3b960bf13ca32a879be3eb4705055dcee31ad`
- **Receipt action ID returned by Pipelock:**
  `01a08f31-fc04-784a-9614-51e214dc95b6`
- **Receipt-containing rotated segment:** `evidence-proxy-24.jsonl`
- **Receipt-containing segment SHA-256:**
  `a09d5f9fe70837fc4bbcbff3130c7cfe2ddf4295b6460265e37694fb961defe7`
- **Pinned Ed25519 public key:**
  `4368c51a94c85941bf0277a2c4dcda535f5400320b60a127ee3e9f3e68d3427e`

The verifier accepted the complete proxy session chain:

- receipts: **19**;
- final sequence: **18**;
- root hash: `249ce0676ad2efbe0b6077ae493cfb73859b1db2c713fff5de3b6b896a9abf6f`;
- recorded interval: `2026-09-11T06:40:08Z` through `2026-09-11T06:40:13Z`;
- signer matched the pinned public key above.

The workflow deliberately hashes the rotated segment that contains the returned action ID but verifies
`--chain` against the full recorder directory. This matters because the matching rotated segment began
after genesis; verifying that segment alone correctly failed the chain-genesis check in an earlier
harness attempt.

### Receipt-evidence limitations

The successful verifier explicitly reported containment as **UNKNOWN** because no posture capsule was
supplied. It also reports limitations including key-holder omission, compromised/forged signer keys,
malicious or disabled recorders, verifier drift, concurrent recorder writers and unproven
containment. Therefore E2 proves cryptographic integrity/provenance for the receipts in this writer
stream; it does **not** prove evidence completeness or that the process could not bypass Pipelock.

Private signing material and raw receipt payloads are deliberately not uploaded as CI artifacts. The
retained artifact is limited to the public key, action correlation ID, receipt-file digest,
verification output, build metadata and non-secret runtime logs.

### Harness history

Three fixture defects were resolved before E2 was promoted:

1. an initial configuration mutation damaged YAML indentation;
2. a later attempt selected an unrelated JSONL recorder directory and found no proxy receipts;
3. the next attempt found the correct rotated proxy segment but verified that non-genesis segment in
   isolation instead of the complete writer chain.

The final green run validates the corrected full-chain procedure rather than reclassifying those
harness failures as upstream product failures.

## Evidence E3: canonical platform MCP stdio through Pipelock

**Status: verified on GitHub Actions run `34570647932`.**

The repository's existing MCP SDK stdio fixture was launched through the exact pinned candidate using
`pipelock mcp proxy -- ...`. Invocation still flowed through the platform's canonical
`CapabilityRegistry` and `CapabilityInvoker`; Pipelock wrapped the external MCP stdio process rather
than replacing platform capability ownership.

The retained JUnit result records:

- tests: **1**;
- errors: **0**;
- failures: **0**;
- skipped: **0**;
- testcase: `test_pipelock_mcp_stdio_wraps_canonical_invocation_path`;
- testcase duration: **3.482 s** on the hosted runner.

E3 proves compatibility for this concrete MCP stdio invocation path and fixture. It does not prove MCP
HTTP/WebSocket behavior, arbitrary MCP-server compatibility, descriptor/response attack resistance or
that the wrapped child process cannot open a direct network socket outside Pipelock.

## Remaining #730 evidence

Still required before the issue can reach a final `adopt`, `optional_provider`, `reference_only` or
`reject` decision:

- representative HTTP forward/CONNECT behavior and redirects;
- WebSocket behavior;
- MCP HTTP and MCP WebSocket behavior;
- descriptor/tool drift and response-injection corpus;
- secret/DLP, encoding and multi-stage exfiltration corpus;
- loopback/private/link-local/metadata, DNS-rebinding and IPv6 corpus;
- direct-network bypass tests, including child-process direct sockets;
- controlled outage/recovery tests for audit-only and enforced profiles;
- baseline-vs-mediated latency plus CPU, RAM, startup and disk/log measurements;
- false-positive and false-negative measurements on the maintained corpus;
- an ordinary single-node/VPS measurement rather than only GitHub-hosted runners.
