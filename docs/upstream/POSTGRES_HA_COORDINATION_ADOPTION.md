# PostgreSQL / Psycopg HA Coordination Adoption Review

- **Projects:** PostgreSQL and Psycopg 3
- **Canonical upstreams:** https://github.com/postgres/postgres and https://github.com/psycopg/psycopg
- **Reviewed versions:** PostgreSQL 18.6; Psycopg 3.3.5
- **Integration categories:** optional external self-hosted service; optional library dependency; platform-owned adapter
- **Proposed platform boundary:** `ai_multi_agent_platform.distributed.postgres_control_plane_coordination`
- **Review date:** 2026-09-13
- **Related issue:** #566

## Decision

**Approved for the optional #566 coordination slice**, subject to real PostgreSQL integration evidence
on the PR head before the adapter is promoted from draft/experimental evidence to a supported HA
compatibility claim.

PostgreSQL is selected as one replaceable self-hostable reference authority because the existing #89
contract needs transactional single-writer lease decisions, row-level serialization, monotonic fencing
state and a backend-owned clock across independent processes/hosts. It is not selected as canonical HA
architecture. #956 separately owns the decision/implementation required to use central SQL for
canonical durable platform state.

Psycopg is used only as the Python PostgreSQL transport. No Psycopg or PostgreSQL type is exposed by the
`CoordinationProvider` contract.

## Functional fit

- [x] Transactional row locking can serialize concurrent lease decisions.
- [x] PostgreSQL timestamps can own expiry instead of trusting caller wall clocks.
- [x] One database service can be reached by independent processes/hosts.
- [x] The service is self-hostable and does not require a paid hosted API.
- [x] The SQL path supports acquire, renew, release, inspect and fence validation required by #89.
- [x] Runtime availability failures can map to the existing fail-closed `CoordinationUnavailable` contract.

## Architecture fit

- [x] `CoordinationProvider` remains the only canonical coordination abstraction.
- [x] The implementation lives under the canonical `distributed` owner; the historical
  `high_availability` package remains a migration/contract compatibility boundary.
- [x] PostgreSQL instance/database/table identifiers remain adapter/deployment metadata.
- [x] Single-node composition remains unchanged and does not install/import Psycopg eagerly.
- [x] Active/active is not introduced.
- [x] #956 prevents the coordination service from being misrepresented as proof that host-local
  canonical application state is HA-safe.

## Replaceability and exit

The adapter can be replaced by another implementation of the existing `CoordinationProvider` protocol
without migrating canonical Task/Run/Agent identities. The PostgreSQL coordination table contains only
operational lease/fencing state. A backend replacement must start from a safe leaderless/reconciled
transition rather than copying a stale live lease as canonical truth.

Removing Psycopg removes only this optional adapter. It does not change core imports, the single-node
profile or canonical public contracts.

## License and provenance

### PostgreSQL

- PostgreSQL 18 uses the PostgreSQL License, a permissive BSD-like license.
- No PostgreSQL server source is copied, vendored, forked or selectively ported into this repository.
- The service is separately deployed and retains its upstream license/notices.

### Psycopg

- Psycopg 3.3.5 package metadata declares `LGPL-3.0-only`.
- The repository consumes Psycopg as an optional normal library dependency; no upstream source is
  copied or modified.
- The project-level source remains MIT; installed Psycopg retains its own license terms and metadata.
- This slice uses the pure `psycopg==3.3.5` package rather than the optional bundled `binary` extra,
  avoiding an unnecessary bundled native-library distribution boundary in the platform package.

A release/package review must preserve the separation and re-check direct/transitive obligations if the
installation strategy changes, for example by bundling Psycopg binary wheels or database images.

## Project health and current versions

At review time (2026-09-13), PostgreSQL 18.6 is the current PostgreSQL 18 maintenance release, dated
2026-08-13, while PostgreSQL 19 is still a development/beta line. Psycopg 3.3.5 was published
2026-08-31 and is the current package release reviewed for this adapter.

These dates are provenance/review inputs, not a promise to auto-follow latest versions. Updates remain
explicit and tested.

## Security implications

- Database credentials are deployment secrets, never canonical state.
- Error translation must not echo DSNs or driver exception strings that may contain credentials.
- Non-loopback traffic requires private/authenticated networking and TLS/service identity according to
  #36/#43 deployment policy.
- Coordination credentials cannot override fencing: owner + epoch + backend expiry remain required.
- `initialize()` permits schema bootstrap to use a distinct identity from the narrower runtime role.
- Source-IP-only trust is not sufficient.

## Resource and deployment footprint

PostgreSQL adds an optional database service for HA deployments. It is not required by ordinary local or
single-node operation. CPU/RAM/storage requirements depend on the chosen HA deployment and future #956
shared-state workload; the coordination table itself is negligible.

No recurring paid service is required. Operators may self-host PostgreSQL on existing infrastructure.

The pure Psycopg package requires a usable PostgreSQL client library (`libpq`) in the runtime
environment; image/package recipes for a supported HA deployment must make that dependency explicit.

## API and contract stability

The platform adapter uses DB-API-style connect/cursor/transaction operations and ordinary PostgreSQL
SQL semantics. All public behavior is translated into platform-owned `CoordinationLease`,
`CoordinationState`, `FencingToken`, `LeadershipConflict`, `StaleFencingToken` and
`CoordinationUnavailable` values/errors. Upstream driver exceptions never become public platform
semantics.

## Simpler internal alternatives considered

### Shared-file SQLite

Rejected for the HA claim. #566 explicitly forbids treating host-local/shared-file SQLite as supported
multi-host HA without correctness proof, and the current single-node topology is intentionally local.

### Process-local in-memory coordination

Already exists for #89 deterministic semantics, but it cannot coordinate independent processes/hosts.

### Custom network coordination daemon

Rejected for this slice because it would create a new service/protocol and duplicate transactional
coordination behavior while increasing the split-brain correctness burden.

### etcd/Consul/Kubernetes Lease as the first reference

Technically viable, but each adds a dedicated coordination product or deployment ecosystem. PostgreSQL
better aligns with the already documented future central-SQL seam while remaining an adapter and does
not make Kubernetes/cloud infrastructure canonical.

## Required follow-up before merge/support claim

- [x] Keep Psycopg in an optional extra only.
- [x] Add deterministic adapter contract tests.
- [x] Add an optional real independent-process PostgreSQL integration test.
- [x] Document security/bootstrap/secret boundaries.
- [x] Record provenance files for PostgreSQL and Psycopg.
- [ ] Record the approved entries in `docs/UPSTREAMS.md` before merge.
- [ ] Run the real PostgreSQL integration test against the reviewed server/driver combination.
- [ ] Confirm full repository CI is green on the final PR head.

No new ADR is required for this adapter choice because ADR 0009 already makes the coordination backend
replaceable and #891 explicitly preserves a future central PostgreSQL seam. A future decision that makes
PostgreSQL mandatory or changes canonical persistence ownership would require separate architecture
review.
