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

## Evidence E4: HTTP forward/CONNECT and remote MCP transports

**Status: transport compatibility verified on GitHub Actions run `34577649075`.**

This run keeps the same exact upstream pin and tag-free build boundary and expands the live transport
matrix. Because `pipelock init --preset audit` generated a configuration with the forward proxy
disabled in this environment, the workflow creates a controlled copy for the forward/CONNECT probe
and changes only `forward_proxy.enabled` to `true`. The generated audit configuration itself remains
unchanged for the other probes.

### Run-specific fingerprints

- **Workflow run:** `34577649075`
- **Workflow evidence artifact digest:**
  `sha256:4bdbbce83dbab6e7d654383b24d4e5ed6e82b64bf262e661b5c5c5c44f1c86cb`
- **Run-specific candidate binary SHA-256:**
  `f4e5dc3d1d2a8bb0c52bd9d1a15dec46e82a9d529b699753946743fbc0961c5c`
- **Generated audit configuration SHA-256:**
  `da732fc3e9800a3223634ef5aad3b960bf13ca32a879be3eb4705055dcee31ad`
- **Forward-enabled audit-probe configuration SHA-256:**
  `27b3680db9f362b29c9ddc32bd6d17de0d2628b9bf789a1061beb4d773c36530`

### MCP Streamable HTTP and MCP WebSocket

Two deterministic local MCP fixtures were routed through the pinned binary using
`pipelock mcp proxy --upstream ...`, while invocation still entered through the platform's canonical
`CapabilityRegistry` and `CapabilityInvoker` path.

The retained JUnit result records:

- tests: **2**;
- errors: **0**;
- failures: **0**;
- skipped: **0**;
- Streamable HTTP testcase duration: **1.601 s**;
- MCP WebSocket testcase duration: **0.622 s**;
- total JUnit time: **3.625 s**.

These durations include fixture/process startup and are compatibility timings, not measurements of
Pipelock overhead. The WebSocket fixture is intentionally minimal and proves the MCP WebSocket
upstream path only; it does not validate Pipelock's generic `/ws` WebSocket proxy.

### HTTP absolute-URI forward proxy

The forward-enabled audit probe accepted a real plaintext forward-proxy request to
`http://example.com/`:

- destination result: HTTP 200;
- returned content size reported by Pipelock: 559 bytes;
- curl end-to-end time through the proxy: **0.053987 s (53.987 ms)**;
- Pipelock request log `duration_ms`: **42.589163 ms**.

### HTTPS CONNECT

The same probe admitted and completed a CONNECT tunnel to `example.com:443`:

- curl completed successfully;
- curl end-to-end time: **0.046614 s (46.614 ms)**;
- Pipelock recorded `tunnel_open` and `tunnel_close`;
- Pipelock tunnel duration: **55.935855 ms**;
- total tunneled bytes reported by Pipelock: **5623**.

TLS interception was deliberately not enabled. Therefore this proves CONNECT admission/tunneling and
host/tunnel-level mediation for the tested path, **not** request-body, header or HTTPS-response content
inspection inside the opaque TLS tunnel.

### CONNECT receipt anomaly

The successful CONNECT transport log also recorded a best-effort allow-receipt emission error for the
CONNECT action:

`chain sealed: transcript root already emitted`

The forward/CONNECT probe used the generated audit recorder with `require_receipts: false`, so the
transport correctly continued under best-effort evidence semantics. This observation is not promoted
to a claim that CONNECT has complete signed-receipt coverage. A dedicated strict CONNECT receipt test
is still required to determine whether this is a reproducible upstream limitation or an interaction in
this multi-process CI harness.

A separate strict `/fetch` receipt step in the **same run** remained healthy and produced a chain that
verified successfully with an out-of-band pinned public key:

- receipt action ID: `01a08f83-3e7c-7006-a5a2-107308358c0b`;
- matching segment: `evidence-proxy-65.jsonl`;
- matching segment SHA-256:
  `9878ed0a631d662183103f671d78c904d1d73ae5ed867d32ccbb10c7509f1c6c`;
- receipts: **43**;
- final sequence: **42**;
- root hash: `89777ad5822ebd60e84ea50ff06e35bc9e293f91e935ffd95cb8a8698a114413`;
- pinned signer public key:
  `b634a7019e61d9476a9aaa4e1b55268a13dafe44a4b250cf7707f87c89c6f47a`;
- recorded interval: `2026-09-11T08:08:47Z` through `2026-09-11T08:08:58Z`.

That strict chain again reported `Containment: UNKNOWN` / `L-CONTAINMENT-UNPROVEN`. Its validity shows
that the recorder/verifier path remained functional in the run; it does **not** erase the CONNECT-
specific receipt error or prove that the CONNECT action was represented by a valid receipt.

### What E4 proves and does not prove

E4 proves live compatibility for:

1. plaintext HTTP absolute-URI forwarding through Pipelock;
2. HTTPS CONNECT tunneling without TLS interception;
3. canonical platform MCP Streamable HTTP through Pipelock's remote-upstream wrapper;
4. canonical platform MCP WebSocket through that wrapper.

E4 does not prove generic `/ws` proxy compatibility, TLS-intercepted HTTPS content inspection,
CONNECT strict-receipt completeness, redirect/private-target safety, complete host-level mediation,
representative performance or production suitability.

## Evidence E5: generic WebSocket and SSRF redirect/link-local boundaries

**Status: verified on GitHub Actions run `34580183382`.**

This run uses the same exact upstream pin and tag-free Core build path and extends the existing
transport suite with a generic WebSocket proxy check plus two concrete SSRF boundary cases. The
positive WebSocket fixture explicitly allowlists loopback only for the controlled test; that setting
is not a production recommendation.

### Run-specific fingerprints and test result

- **Workflow run:** `34580183382`
- **Workflow evidence artifact digest:**
  `sha256:d3b0eeca4d8f94c225f29cc5ae79b1c68ae70a37a1eb4e3db1bb9f05be4bc8f9`
- **Run-specific candidate binary SHA-256:**
  `6996ff730d4fbed0af82771f7190572c6d287479b389201c465bbcbb06aeb025`
- **Generated audit configuration SHA-256:**
  `da732fc3e9800a3223634ef5aad3b960bf13ca32a879be3eb4705055dcee31ad`
- **JUnit transport/boundary suite:** **5 tests, 0 errors, 0 failures, 0 skipped**;
- total JUnit time: **4.012 s**.

Per-test hosted-runner durations were:

- MCP Streamable HTTP: **1.402 s**;
- MCP WebSocket: **0.632 s**;
- generic Pipelock `/ws`: **0.222 s**;
- redirect-to-private boundary: **0.244 s**;
- link-local metadata boundary: **0.120 s**.

These durations include fixture/process startup and are compatibility timings, not Pipelock-overhead
measurements.

### Generic `/ws` WebSocket path

A deterministic local WebSocket echo server was reached through Pipelock's
`/ws?url=...` endpoint. A clean text frame was sent through Pipelock and the identical echoed frame
was received successfully. Compression was disabled for the fixture so the test isolates the basic
bidirectional text-frame path.

The fixture configuration explicitly allowlists `127.0.0.0/8` and `::1/128` so the local test server
can be reached. This proves generic `/ws` transport compatibility for a clean text-frame round trip;
it does **not** prove production safety for loopback/private targets or adversarial frame scanning.

### Redirect to a private target

The redirect fixture separates the first and second hop deliberately:

1. the first hop is served on `127.0.0.2` and only `127.0.0.2/32` is placed in the test SSRF
   allowlist;
2. a direct fixture health check verifies that `/start` returns HTTP 302 with a `Location` pointing to
   a second server on `127.0.0.1`;
3. the second server writes a marker file if it is ever reached;
4. the same first-hop URL is fetched through Pipelock;
5. Pipelock returns a blocking response, and the private-target marker remains absent.

This proves that the evaluated `/fetch` path did not treat an allowlisted first hop as permission to
follow the tested redirect onto the non-allowlisted private loopback target.

### Link-local metadata-style target

The same suite sends `/fetch` directly toward
`http://169.254.169.254/latest/meta-data/`. The evaluated Pipelock path returns a blocking response;
the request is classified/logged through the SSRF/link-local boundary rather than being admitted as a
normal external fetch.

This is one concrete cloud-metadata-style link-local case. It is not a substitute for the broader
private-range, IPv6, DNS-rebinding or hostname-resolution corpus still required by #730.

### Reproduced CONNECT receipt anomaly

Run `34580183382` reproduced the E4 best-effort CONNECT allow-receipt error:

`chain sealed: transcript root already emitted`

The CONNECT tunnel itself again completed successfully, with Pipelock reporting **5625** tunneled
bytes and a tunnel duration of **34.816573 ms** in this hosted-runner sample. Because the transport
probe still uses `require_receipts: false`, this confirms reproducibility of the observation across
multiple live runs but does not yet establish whether the root cause is Pipelock itself or the current
multi-process evaluation harness.

The separate strict `/fetch` receipt verification in the same run remained valid:

- receipt action ID: `01a08f9f-5ee8-7d21-95b8-0ccf0f736f02`;
- matching receipt-segment SHA-256:
  `b0e4959e196d5d4c3e9efe89ce4c78eb0abd9e02cd60c9626d78f5b6c4c0efd9`;
- receipts: **43**;
- final sequence: **42**;
- root hash: `32d9159af6ae336116bba0d011a994226a46bc5a761891f9371c3190eb894afc`;
- pinned signer public key:
  `af586066111408bf932d42d81db967c6fe9c515320ff41d4f807572d5840177f`;
- recorded interval: `2026-09-11T08:39:30Z` through `2026-09-11T08:39:41Z`.

The verifier again reported `Containment: UNKNOWN` / `L-CONTAINMENT-UNPROVEN`. The valid strict fetch
chain therefore does not prove CONNECT receipt completeness or network-bypass resistance.

### What E5 proves and does not prove

E5 supports these additional claims for the exact pinned build and controlled fixtures:

1. Pipelock's generic `/ws` proxy can relay a clean bidirectional text-frame round trip;
2. the tested `/fetch` redirect cannot move from the explicitly allowlisted first hop onto the tested
   non-allowlisted private loopback target;
3. the tested `169.254.169.254` metadata-style link-local target is blocked;
4. the previously observed best-effort CONNECT receipt anomaly is reproducible across live runs.

E5 does **not** prove WebSocket DLP/injection resistance, DNS-rebinding resistance, IPv6/private-range
coverage, complete containment, direct-socket bypass resistance, strict CONNECT receipt validity,
representative performance or production suitability.

## Evidence E6: adversarial WebSocket DLP and response-injection corpus

**Status: verified on GitHub Actions run `34581525771`.**

This run keeps the exact pinned upstream revision and tag-free Core build boundary while exercising a
small deterministic adversarial corpus through Pipelock's generic `/ws?url=...` path. The test
configuration allows loopback only so local fixtures can be reached; it retains blocking request-body
DLP and blocking response-injection scanning for the WebSocket text-frame path.

### Run-specific fingerprints and JUnit result

- **Workflow run:** `34581525771`
- **Workflow evidence artifact digest:**
  `sha256:d57850671a3ac8a5be51989b7b8e47570bddda82549bbf26ff1ea13f1eee4c33`
- **Run-specific candidate binary SHA-256:**
  `83c439188e3d41d2b44a2b1409d34100a23249916782d1dfef4aeb8b8e9ada36`
- **Generated audit configuration SHA-256:**
  `da732fc3e9800a3223634ef5aad3b960bf13ca32a879be3eb4705055dcee31ad`
- **Adversarial JUnit:** **4 tests, 0 errors, 0 failures, 0 skipped**;
- total JUnit time: **1.143 s**.

Per-test hosted-runner durations were:

- plaintext synthetic AWS access-ID frame: **0.237 s**;
- base64-encoded form of the same synthetic secret: **0.223 s**;
- secret split across two separate WebSocket messages: **0.267 s**;
- server-to-client prompt-injection response: **0.270 s**.

These durations include process/socket fixture overhead and are not performance-overhead measurements.

### Client-to-server secret blocking

The first case sends a deterministic synthetic AWS-style access ID through the Pipelock WebSocket
proxy. The upstream fixture records every frame it receives. The connection is terminated by Pipelock
and the marker confirms that the secret-bearing frame never reached the upstream server.

The second case base64-encodes the same synthetic secret before sending it. Pipelock again terminates
the proxied connection before the encoded frame reaches the upstream fixture. This proves the tested
normalization path for this concrete base64 credential shape; it does not prove every encoding or
normalization variant.

### Cross-message secret blocking

The third case splits the synthetic credential across two separate WebSocket text messages. The first
fragment is individually harmless and reaches the upstream fixture, which returns an acknowledgement.
After the second fragment is sent, Pipelock's cross-message DLP path terminates the connection and the
fixture marker confirms that the second fragment did not reach the upstream.

This is evidence for the documented cross-message DLP behavior on the tested text-message sequence.
It is not evidence for every low-level WebSocket fragmentation, binary-frame or compression edge
case.

### Server-to-client response injection

The adversarial fixture receives a harmless trigger message and then emits a deterministic prompt-
injection string matching the configured response-scanning corpus. Its marker proves the response was
created by the upstream fixture, while the client observes connection termination rather than the
injection payload. The tested server response therefore did not cross the Pipelock boundary to the
client.

### Receipt continuity and CONNECT observation

The separate strict `/fetch` receipt verification in the same run remained valid:

- receipt action ID: `01a08fae-7917-76eb-8424-6f09806543d9`;
- matching receipt-segment SHA-256:
  `e3f2a730d131dacdf9c17e88b16f90b84b25bc326bdb85fb0726be3a7cbb9872`;
- receipts: **43**;
- final sequence: **42**;
- root hash: `0e1d4ddf809f5d336a1fed638c2edbb97d2d74592355fbeb56603b703ae82719`;
- pinned signer public key:
  `0596bfe4b052acafb2789c222d048e4ff9008b372de2257517667a048043cc7e`;
- recorded interval: `2026-09-11T08:55:56Z` through `2026-09-11T08:56:11Z`.

The verifier again reports `Containment: UNKNOWN` / `L-CONTAINMENT-UNPROVEN`.

The best-effort CONNECT allow-receipt error also reproduced again in this run:

`chain sealed: transcript root already emitted`

The CONNECT tunnel itself completed, with **5625** bytes reported and a tunnel duration of
**55.355249 ms**. This is now a repeat observation across three live evaluation runs. It strengthens
the case for isolating strict CONNECT receipt behavior next, but still does not establish whether the
root cause is the upstream implementation or the current multi-process evaluation harness.

### What E6 proves and does not prove

For the exact pinned build and controlled text-frame fixtures, E6 supports these claims:

1. the tested plaintext synthetic credential is blocked before the upstream WebSocket server receives
   it;
2. the tested base64 representation of that credential is also blocked before the upstream receives
   it;
3. the tested credential split across two WebSocket messages is detected before the second fragment
   reaches the upstream;
4. the tested server-generated prompt-injection response is blocked before the client receives it.

E6 does **not** establish complete DLP/injection coverage, low-level fragmented-frame resistance,
binary-frame behavior, all encoding/normalization variants, MCP descriptor/tool-drift resistance,
network containment, direct-socket bypass resistance, representative performance or production
suitability.

## Remaining #730 evidence

Still required before the issue can reach a final `adopt`, `optional_provider`, `reference_only` or
`reject` decision:

- generic `/ws` low-level fragmentation/binary/compression edge cases beyond the now-covered clean,
  plaintext-secret, base64-secret, cross-message-secret and text response-injection paths;
- strict CONNECT receipt root-cause isolation and verification under `require_receipts: true`;
- MCP descriptor/tool drift, tool-description poisoning and MCP response-injection corpus;
- broader secret/DLP encoding and multi-stage exfiltration corpus across HTTP/MCP surfaces beyond the
  tested WebSocket cases;
- broader loopback/private-range, DNS-rebinding and IPv6 corpus; one redirect-to-loopback and one
  `169.254.169.254` link-local metadata case are now covered;
- direct-network bypass tests, including child-process direct sockets;
- controlled outage/recovery tests for audit-only and enforced profiles;
- baseline-vs-mediated latency plus CPU, RAM, startup and disk/log measurements;
- false-positive and false-negative measurements on the maintained corpus;
- an ordinary single-node/VPS measurement rather than only GitHub-hosted runners.
