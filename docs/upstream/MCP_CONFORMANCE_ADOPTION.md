# Official MCP conformance suite adoption review

## Candidate identity

- **Project name:** Model Context Protocol Conformance Test Suite
- **Canonical upstream repository:** `https://github.com/modelcontextprotocol/conformance`
- **Stable gating pin:** `v0.1.16` / `21a9a2febd7100d7c17ac1021ee7f2ed9f66a1e0`
- **Prerelease informational pin:** package version `0.2.0-alpha.11` / `a983ba93c91e0bb31d0b6849eeb52f0ad1083107`
- **Integration category/categories:** protocol/specification integration; development/test dependency
- **Platform boundary:** CI/developer conformance tooling under `conformance/mcp/` and `scripts/ci/`; never a runtime/canonical contract dependency
- **Reviewer:** ScoreSymphony
- **Review date:** 2026-09-11

## Required evaluation

### Functional fit

- [x] The required capability is stated precisely: wire-level evidence for MCP revisions claimed by the platform.
- [x] The upstream provides official client/server conformance scenarios tied to MCP specification requirements.
- [x] Platform-specific evidence aggregation remains local because upstream does not know #46 or the platform's compatibility model.

### Architecture fit

- [x] The suite remains outside canonical Task/Run/Capability contracts.
- [x] No canonical identity becomes an upstream-private identity.
- [x] Runtime lifecycle, persistence, authorization and platform acceptance remain platform-owned.
- [x] The least-coupled approach is used: exact CI checkout plus runner invocation, with no vendoring or runtime dependency.

### Replaceability and exit

- [x] Removing the suite only removes official protocol evidence; native capabilities and MCP runtime behavior remain intact.
- [x] No persisted application data or runtime configuration migration is required.
- [x] Suite-specific data is restricted to evidence/pin metadata.
- [x] Updates occur through explicit pin changes and can be rolled back by reverting the pin/update PR.
- [x] Platform-internal MCP integration tests remain available but are not treated as an equivalent protocol-conformance substitute.

### License and provenance

- [x] Canonical upstream repository verified.
- [x] Exact stable and prerelease commits recorded.
- [x] Upstream package metadata reports MIT; verified 2026-09-11.
- [x] No upstream source is copied or vendored into this repository.
- [x] The upstream checkout retains its own license and package metadata in CI/developer environments.
- [x] Dependency installation is performed from the pinned checkout's lockfile through `npm ci`.

### Project health and maintenance

- [x] The project is actively maintained by the Model Context Protocol organization.
- [x] Stable releases exist; newer protocol work is currently also represented by prerelease development revisions.
- [x] Stable and prerelease tracks are deliberately separated so prerelease churn cannot silently redefine a production claim.
- [x] Suite updates are handled through the platform's normal upstream review discipline.

### Security implications

- [x] CI executes upstream JavaScript from an exact reviewed commit, never uncontrolled `latest`.
- [x] The suite only targets local test processes/localhost fixtures in this integration.
- [x] No production credentials or hosted MCP provider are required.
- [x] The checkout/build is supply-chain relevant and therefore requires explicit commit/version pin review.

### Resource footprint

- [x] CPU/memory/storage needs are acceptable for CI and local developer testing.
- [x] No GPU is required.
- [x] Network is required only to obtain dependencies/checkouts before the test; the conformance run itself uses local processes/localhost.
- [x] The suite is not loaded by production processes.

### Deployment complexity

- [x] No production service, port, volume, database or queue is introduced.
- [x] CI/developer prerequisites are Node.js plus the pinned upstream checkout and its lockfile-resolved dependencies.
- [x] No recurring paid service is introduced.

### Dependency footprint

- [x] Direct use is isolated to conformance CI/developer tooling.
- [x] Transitive Node dependencies stay inside the upstream test checkout.
- [x] Runtime Python package dependencies are not expanded by adopting the conformance suite.
- [x] The suite does not force unrelated platform domains onto its stack.

### API and contract stability

- [x] The consumed interface is the upstream conformance CLI and scenario/spec-version model.
- [x] Exact pins protect against silent CLI/scenario changes.
- [x] Local pin-drift validation rejects a checkout whose package version or commit differs from the reviewed pin.
- [x] Suite upgrades can change evidence expectations without changing canonical platform contracts.

### Simpler internal alternative

- [x] Platform-internal MCP tests were considered and already exist.
- [x] They cannot independently certify wire-level MCP compliance, so the official suite provides distinct value.

## Decision

- [x] **Approved** — integrate as exact-pinned test/evidence infrastructure only.

### Decision rationale

The suite is authoritative enough to provide the wire-level protocol evidence missing from the platform's existing MCP integration tests, but it must not become runtime authority. Stable production claims use an exact stable suite pin. The `2026-07-28` stateless behavior is implemented through a separate opt-in `MCPClient` adapter and is exercised against the exact prerelease suite, but the profile remains informational/unclaimed until the official conformance line is stable enough to promote deliberately. This keeps implementation readiness separate from a production compatibility claim.

### Required follow-up before merge

- [x] Update `docs/UPSTREAMS.md`.
- [x] Add machine-readable provenance metadata.
- [x] No copied-source notices are required because no upstream source is vendored.
- [x] Add pin/evidence/CI regression tests.
- [x] No ADR is required because canonical architecture does not change.
