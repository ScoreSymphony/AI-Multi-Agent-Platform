# Pipelock Core adoption checklist

This is the repository adoption-checklist record for issue #730. The current decision is **Candidate**,
not Approved or Integrated.

## Candidate identity

- **Project name:** Pipelock Core
- **Canonical upstream repository:** `https://github.com/luckyPipewrench/pipelock`
- **Candidate version/tag/commit:** `f7d1816f1a5ad63d501b0c48f36066f836f59022`
- **Integration category/categories:** external self-hosted service; adapter integration
- **Proposed platform boundary/adapter:** post-#15/#591 technical mediation and evidence
- **Reviewer:** ScoreSymphony platform evaluation / issue #730
- **Review date:** 2026-09-11

## Required evaluation

### Functional fit

- [x] Required capability is precise: strengthen technical mediation/evidence for already-canonical
  network/MCP egress decisions.
- [x] Reviewed upstream source/docs expose HTTP, WebSocket and MCP mediation/scanning plus signed
  receipt/flight-recorder evidence surfaces.
- [ ] Live platform transport, bypass, failure and performance behavior is measured.

### Architecture fit

- [x] Integration can remain behind platform-owned contracts.
- [x] Canonical Task/Run/Agent and policy identities remain platform-owned.
- [x] Pipelock receipt/session/config state is explicitly non-canonical evidence/adapter state.
- [x] Optional external-runtime adapter is less coupled than vendoring/forking/selective source porting.

### Replaceability and exit

- [x] Removal path is explicit: disable/remove adapter/runtime without canonical data migration.
- [x] No canonical lifecycle or policy state needs migration.
- [x] Upstream-specific values remain adapter evidence.
- [ ] Live rollback/degradation behavior is exercised under an enforced profile.
- [x] Baselines include platform-only #591 hooks and a minimal/reference mediation comparison where
  available.

### License and provenance

- [x] Canonical upstream location is verified.
- [x] Exact reviewed commit is recorded.
- [x] Root Core license boundary is Apache-2.0 at the reviewed commit.
- [x] `enterprise/LICENSE` is Elastic License 2.0 at the reviewed commit.
- [x] Enterprise source/build-tag boundary is recorded.
- [x] Upstream `Makefile` `build` is tag-free at the reviewed revision.
- [x] The repository Dockerfile builds `pipelock` with `-tags enterprise`.
- [x] `.goreleaser.yaml` builds the normal `pipelock` release binary with the `enterprise` tag, so
  release archives/images are not accepted as Core-only artifacts for this evaluation baseline.
- [x] No upstream source is copied or modified by this candidate adapter.

### Project health and maintenance

- [x] The exact reviewed main commit and its signed GitHub commit metadata are recorded as the current
  evaluation pin rather than relying on an unpinned branch.
- [ ] Release/update cadence is quantified before approval.
- [ ] Bus-factor/abandonment risk is assessed before approval.
- [ ] Security/advisory handling is reviewed before approval.

### Security implications

- [x] Proxy/MCP trust boundary and external-process nature are documented.
- [x] Pipelock cannot grant permissions denied by canonical policy mapping.
- [x] Receipt trust requires external cryptographic verification rather than self-assertion.
- [x] Raw receipt targets/patterns are excluded from the initial platform evidence normalizer.
- [x] Direct-network bypass is treated as an explicit unsupported condition until complete mediation is
  proven.
- [ ] Adversarial corpus and outage/bypass tests have been executed against the pinned binary.

### Resource footprint

- [ ] Added HTTP request latency is measured.
- [ ] MCP call latency is measured.
- [ ] WebSocket overhead is measured.
- [ ] CPU/RAM/startup/log/disk overhead is measured on the target single-node/VPS profile.
- [ ] False-positive/false-negative behavior is measured against the maintained corpus.

### Deployment complexity

- [x] Baseline deployment remains optional/self-hosted.
- [x] No recurring paid service is required for the tag-free Core source-build candidate.
- [x] Candidate binds behind existing canonical policy instead of imposing a new platform registry or
  lifecycle service.
- [ ] Enforced deployment topology and OS/container network-containment requirements are proven in a
  live pilot.

### Dependency footprint

- [x] No Python runtime dependency is introduced by the initial projection/evidence adapter.
- [x] Pipelock remains a separately built optional runtime.
- [x] Enterprise-tagged code is excluded from the intended source-built Core evaluation baseline.
- [ ] Material transitive/runtime dependency footprint of the pinned Core binary is recorded from the
  actual build artifact.

### API and contract stability

- [x] Platform consumes its own `EgressDecision` mapping rather than exposing Pipelock types as
  canonical contracts.
- [x] Adapter contract tests cover mapping and evidence normalization.
- [x] Unknown/unmapped canonical policy state is designed to fail closed.
- [ ] Live upstream CLI/config/receipt compatibility tests are automated against the pinned revision.

### Simpler internal alternative

- [x] Platform-native #591 application-layer enforcement remains the zero-Pipelock baseline.
- [x] Evaluation explicitly compares Pipelock against a minimal/reference mediation path rather than
  assuming an upstream is required.
- [ ] Measured evidence demonstrates whether Pipelock adds enough enforcement/evidence value to
  justify operational complexity.

## Decision

- [ ] **Reject**
- [ ] **Reference only**
- [x] **Candidate**
- [ ] **Approved**

### Decision rationale

The source/provenance review is sufficient to continue a proof of concept without changing canonical
architecture or introducing a paid baseline dependency. It is not sufficient for approval: complete
network mediation, outage behavior, adversarial coverage and resource/performance costs remain
unmeasured.

The artifact boundary is material. At the pinned revision, both the repository Dockerfile and the
GoReleaser definition for the normal `pipelock` binary enable Enterprise build tags. Official release
archives/images therefore cannot be treated as Apache-Core-only artifacts for this baseline merely
because paid features may be inactive. The #730 Core pilot deliberately builds the pinned source via
the tag-free `make build` path instead.

### Required follow-up before approval/integration

- [ ] Execute the audit-only pilot using the exact pinned source revision.
- [ ] Record binary/config/signing-key fingerprints and live receipts.
- [ ] Execute HTTP/WebSocket/MCP transport and adversarial test matrices.
- [ ] Prove or explicitly reject protected-profile direct-network bypass resistance.
- [ ] Exercise fail-open/fail-closed/audit-degradation behavior.
- [ ] Measure latency/CPU/RAM/startup/log growth and false-positive/false-negative behavior.
- [ ] Choose the final #730 outcome: `adopt`, `optional_provider`, `reference_only` or `reject`.
- [ ] Update `docs/UPSTREAMS.md` only if Pipelock is promoted to approved/integrated status.
- [x] Add candidate provenance metadata (`upstream/pipelock-core.yaml`).
- [x] Add platform-owned mapping/evidence contract tests.
- [x] Add a pinned source-build compatibility workflow for the Core/audit baseline.
