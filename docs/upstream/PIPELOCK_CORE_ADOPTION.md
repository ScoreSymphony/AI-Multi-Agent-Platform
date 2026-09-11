# Pipelock Core adoption checklist

This is the repository decision-readiness record for issue #730. Pipelock remains an evaluated
**Candidate**, not an adopted canonical dependency. The platform's #15 authorization and #591 egress
policy remain authoritative; Pipelock is evaluated only as an optional downstream technical
mediation/evidence layer.

## Candidate identity

- **Project name:** Pipelock Core
- **Canonical upstream repository:** `https://github.com/luckyPipewrench/pipelock`
- **Reviewed revision:** `f7d1816f1a5ad63d501b0c48f36066f836f59022`
- **Evaluated build:** tag-free source build through `make build`
- **Integration category:** optional external self-hosted enforcement/evidence adapter
- **Reviewer:** ScoreSymphony platform evaluation / issue #730
- **Review date:** 2026-09-11

## Current decision state

Final #730 outcome is **not yet recorded**. Exactly one of these must be selected after the remaining
hard evidence gate is resolved:

- `adopt`
- `optional_provider`
- `reference_only`
- `reject`

Current evidence points away from treating Pipelock as a complete or mandatory network-security
boundary: mediated traffic gains inspection and signed evidence, but Pipelock proxying alone is
bypassable by a child-owned direct network socket and independent OS/container/network containment is
required. Cross-request secret fragments also remain a demonstrated correlation limitation.

The remaining hard decision blocker is the representative ordinary-VPS/single-node measurement. The
retained GitHub-hosted benchmark is useful reference evidence but is explicitly not substituted for
that VPS evidence.

## Required evaluation

### Functional fit

- [x] Required capability is precise: strengthen technical mediation/evidence for already-canonical
  network/MCP egress decisions.
- [x] Reviewed upstream source/docs expose HTTP, WebSocket and MCP mediation/scanning plus signed
  receipt/flight-recorder evidence surfaces.
- [x] Audit-only HTTP fetch is live-validated against the exact pinned source build.
- [x] Plaintext HTTP forward and HTTPS CONNECT transport are live-validated with a dedicated
  forward-enabled audit probe; TLS interception was not enabled.
- [x] The canonical platform MCP stdio fixture is live-validated through `pipelock mcp proxy -- ...`.
- [x] Canonical platform MCP Streamable HTTP and MCP WebSocket fixtures are live-validated through
  Pipelock remote-upstream wrapping.
- [x] Generic `/ws` round trip is live-validated.
- [x] Redirect-to-private, RFC1918, localhost, link-local/metadata and IPv6 loopback boundary cases are
  exercised by reproducible tests.
- [x] Deterministic hostname resolution-change / DNS-rebinding behavior is exercised. The retained
  outcome uses the previously validated IP rather than following a later private remap.
- [x] Multi-request exfiltration is exercised. Two individually harmless secret fragments can reach a
  controlled upstream and be recombined there; this is retained as a known cross-request correlation
  limitation rather than hidden as a passing security claim.

### Architecture fit

- [x] Integration remains behind platform-owned contracts.
- [x] #15 remains canonical authorization/approval authority.
- [x] #591 remains canonical data-classification/egress-policy authority.
- [x] Canonical Task/Run/Agent/correlation and policy identities remain platform-owned.
- [x] Pipelock receipt/session/config/rule state remains non-canonical adapter evidence.
- [x] Pipelock can be removed/disabled without canonical data migration.
- [x] Pipelock is not required for local/reference operation.

### Canonical policy mapping

- [x] Canonical `ALLOW` is the only decision projected into the Pipelock mediation path.
- [x] `DENY`, `REQUIRE_APPROVAL`, `LOCAL_ONLY` and unknown/blocked states are stopped by platform
  policy before Pipelock can weaken them.
- [x] `ALLOW` does not bypass permissions denied by #15.
- [x] #591 policy revision/digest and canonical correlation identity remain platform-owned evidence.
- [x] Pipelock-native identifiers remain adapter metadata rather than canonical policy identifiers.

### License and provenance

- [x] Canonical upstream location and exact reviewed commit are recorded.
- [x] Root Core license boundary is Apache-2.0 at the reviewed commit.
- [x] `enterprise/LICENSE` is Elastic License 2.0 at the reviewed commit.
- [x] Enterprise source/build-tag boundary is recorded.
- [x] Upstream `Makefile` `build` is tag-free at the reviewed revision.
- [x] The repository Dockerfile builds `pipelock` with `-tags enterprise`.
- [x] `.goreleaser.yaml` builds the normal release binary with the `enterprise` tag, so official
  release archives/images are not treated as Core-only artifacts for this evaluation baseline.
- [x] The evaluated baseline builds the exact pinned source through the tag-free Core path.
- [x] No upstream source is copied into the platform.
- [ ] Byte-for-byte reproducible Core builds are not demonstrated. Run-specific binary fingerprints
  have differed at the same source pin/toolchain, so only run-specific hashes are claimed.

### Replaceability and failure semantics

- [x] Removal path is explicit: disable/remove the adapter/runtime without canonical data migration.
- [x] No canonical lifecycle or policy state needs migration.
- [x] Audit-only operation may degrade without redefining canonical policy.
- [x] Enforced mediation is exercised under process outage and does not silently forward through the
  dead mediator.
- [x] Recovery restores mediation without duplicating canonical policy state.
- [x] Platform local/reference CI remains green without Pipelock as a runtime dependency.

### Security and adversarial corpus

- [x] Tool-description poisoning is exercised.
- [x] Descriptor/tool-definition drift is exercised.
- [x] MCP response/instruction injection is exercised.
- [x] Plaintext synthetic-secret DLP is exercised.
- [x] Encoding/base64 variants are exercised.
- [x] WebSocket DLP/injection cases are exercised.
- [x] Multi-stage/cross-request exfiltration is exercised and retains the observed correlation
  limitation.
- [x] SSRF/private/localhost/link-local/metadata targets are exercised.
- [x] DNS resolution-change/rebinding behavior is exercised.
- [x] Redirect-to-private-target behavior is exercised.
- [x] IPv6 loopback-equivalent blocking is exercised.

### Bypass and containment

- [x] MCP stdio child-owned direct-network bypass is tested beneath `pipelock mcp proxy`.
- [x] The uncontained child can reach the controlled direct target, proving Pipelock mediation alone is
  **not** a complete egress security boundary.
- [x] A narrow Linux x86-64 evaluation seccomp profile preserves MCP stdio/asyncio while denying fresh
  `AF_INET`/`AF_INET6` sockets with `EPERM` for raw TCP, HTTP-shaped, MCP-HTTP-shaped and
  WebSocket-upgrade-shaped child traffic.
- [x] The tested protected MCP-stdio profile therefore requires a separate deployment-owned containment
  layer; Pipelock itself is not credited with providing that containment.
- [ ] No deployment-wide containment claim is made for every browser, Connector, Hermes/tool, inherited
  descriptor or future adapter path. Those profiles must have their own OS/container/network boundary
  before being labelled protected against direct-network bypass.

See `PIPELOCK_CORE_CONTAINMENT_EVIDENCE.md` for the exact evaluated boundary.

### Evidence / receipts

- [x] Pipelock receipts remain external evidence rather than canonical lifecycle/policy truth.
- [x] Canonical correlation/policy identity is retained by the platform evidence layer.
- [x] Protected/raw target material is excluded from the initial normalized platform evidence where it
  is not required.
- [x] Live receipt chains are verified with out-of-band pinned Ed25519 public keys.
- [x] Strict normal-lifecycle CONNECT receipt behavior is reproduced with `require_receipts: true` and a
  fresh writer.
- [x] The isolated strict CONNECT chain verifies successfully: 5 receipts, final sequence 4, no
  sealed-chain error.
- [x] The earlier `chain sealed: transcript root already emitted` observation is classified as an
  evaluation-harness teardown race when the recorder was stopped before tunnel close.
- [ ] Strict CONNECT evidence does not claim every abnormal-shutdown lifecycle is receipt complete.

See `PIPELOCK_CORE_CONNECT_RECEIPT_EVIDENCE.md` for retained run/artifact fingerprints.

### Performance and operability

The reproducible benchmark is retained in `scripts/benchmarks/issue730_pipelock_benchmark.py` and its
measurement contract in `PIPELOCK_CORE_PERFORMANCE_EVIDENCE.md`.

- [x] Direct-versus-mediated HTTP latency is measured on a same-run hosted reference.
- [x] Direct-versus-mediated generic WebSocket round-trip latency is measured.
- [x] Direct-versus-mediated MCP stdio call-total is measured. This includes process startup/protocol
  handshake per call and is not presented as a persistent-session microbenchmark.
- [x] Pipelock cold-start time is measured.
- [x] CPU and RSS are measured for the exercised hosted reference workload.
- [x] HOME/stdout log/disk growth is measured for the exercised hosted reference workload.
- [x] Maintained live HTTP DLP/encoding subset plus benign controls records `2 TP / 4 TN / 0 FP / 0 FN`.
- [x] Hosted reference evidence is explicitly labelled non-VPS evidence.
- [ ] Representative ordinary-VPS/single-node measurements using the same retained harness are still
  pending and must not be replaced by GitHub-hosted runner numbers.

Retained GitHub-hosted reference observations:

| Measurement | Direct mean | Mediated mean | Delta | Relative overhead |
| --- | ---: | ---: | ---: | ---: |
| HTTP request | 18.311 ms | 19.486 ms | +1.176 ms | +6.42% |
| WebSocket round trip | 1.447 ms | 3.898 ms | +2.451 ms | +169.44% |
| MCP stdio call-total | 958.191 ms | 1112.430 ms | +154.239 ms | +16.10% |

Additional hosted reference observations: Pipelock `run` startup `101.872 ms`; CPU time `0.11 s` for
that workload; RSS `67,743,744` bytes at start and `68,575,232` bytes peak/end; HOME/stdout workload
growth `25,844` bytes. These are retained single-run reference values, not universal performance
claims.

### Comparison baseline

- [x] Platform-native #591 application-layer decision hooks remain the zero-Pipelock baseline.
- [x] Direct transport baselines are measured alongside mediated paths.
- [x] The evaluation records what Pipelock adds: mediated inspection, network/MCP blocking surfaces,
  flight-recorder/receipt evidence and additional SSRF/DLP checks.
- [x] The evaluation also records what Pipelock does not add by itself: complete process/network
  containment, cross-request secret correlation and canonical authorization/egress policy.

### Project health / dependency follow-up

- [x] The exact reviewed upstream commit is pinned instead of relying on a moving branch.
- [x] Security/advisory handling has been reviewed; the upstream security policy exposes private GitHub
  advisory reporting and supported-version/severity guidance.
- [ ] Release/update cadence is not yet quantified in this decision record.
- [ ] Bus-factor/abandonment risk is not yet quantified in this decision record.
- [x] Actual Core build metadata is retained in compatibility/performance artifacts.

These maintenance-risk items should influence lifecycle management if the final result is
`optional_provider` or `adopt`; they do not change the already-demonstrated security boundaries.

## Decision evidence summary

### Demonstrated strengths

- optional/self-hosted tag-free Core evaluation requires no recurring paid Pipelock service;
- useful HTTP/WebSocket/MCP mediation and DLP/SSRF inspection on traffic that actually traverses the
  mediator;
- reproducible compatibility/security workflows against an exact pin;
- signed receipt/flight-recorder evidence can be independently verified;
- canonical #15/#591 policy authority can remain outside Pipelock;
- removal/absence does not require canonical data migration or break the platform reference baseline.

### Demonstrated limitations

- Pipelock proxying alone is bypassable by direct child-owned sockets;
- a separate OS/container/network containment layer is required for any protected-profile claim;
- multi-request secret fragments can evade per-request correlation and be reconstructed upstream;
- the evaluated official Docker/release build paths are not accepted as the Apache-Core-only baseline
  because they enable Enterprise build tags; the platform evaluation must keep using the reviewed
  tag-free source-build boundary;
- byte-for-byte reproducible builds are not established;
- GitHub-hosted performance evidence is not representative ordinary-VPS evidence;
- broad deployment-wide containment across every future network-capable path is not proven by the
  narrow MCP-stdio seccomp pilot.

## Acceptance readiness

- [x] Exact Core revision and effective license/build boundary verified.
- [x] Evaluation began with audit-only pilots.
- [x] #15 and #591 remain canonical authorities.
- [x] HTTP, WebSocket, MCP stdio and MCP HTTP/WebSocket paths evaluated.
- [x] Tool poisoning, response injection, descriptor drift, DLP/encoding, SSRF, DNS/private/metadata and
  IPv6 cases have reproducible tests.
- [x] Multi-stage exfiltration across multiple requests is tested and its limitation retained.
- [x] Direct-network bypass resistance is tested for the exercised protected MCP-stdio profile; other
  profiles are not claimed protected without their own containment boundary.
- [x] Outage/fail-closed/recovery behavior is exercised for the enforced mediation pilot.
- [x] Receipts/Flight Recorder are assessed and remain non-canonical evidence.
- [x] Hosted-reference latency, CPU, RAM, log/disk and maintained-subset FP/FN behavior are measured.
- [x] Platform local/reference operation remains green without Pipelock as a mandatory runtime.
- [ ] Representative ordinary-VPS/single-node evidence is retained.
- [ ] Exactly one final #730 recommendation is recorded and backed by the completed evidence set.

## Required follow-up before final #730 outcome

1. Run the existing benchmark harness on the representative ordinary Linux x86-64 VPS/single-node
   profile and retain JSON plus exact binary/config hashes and environment metadata.
2. Compare the VPS result with the retained hosted reference without treating either as a universal
   threshold.
3. Choose exactly one final result: `adopt`, `optional_provider`, `reference_only` or `reject`.
4. Update this decision record and `upstream/pipelock-core.yaml` lifecycle status consistently.
5. Update `docs/UPSTREAMS.md` only if the chosen lifecycle status requires catalog promotion.
6. Run the complete final combined #804 CI on the resulting integration head before any merge to
   `main`.

Until those steps are complete, the safe repository status remains **Candidate / final decision
pending**, with `optional_provider` a plausible direction but not yet the recorded #730 outcome.
