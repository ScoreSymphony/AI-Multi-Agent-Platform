# Issue #40 hardening acceptance delta

This note records why #40 was reopened after PR #268 and what the follow-up hardening branch closes.

## Gaps found after the first merge

The original implementation provided a working backup/restore round trip, checksums, WAL-safe SQLite snapshots, recovery markers, active-Run reconciliation and distributed stale-state sanitization. A stricter audit against the original issue text found that several guarantees were documented or tested only indirectly:

- `verify_backup()` did not execute the normative JSON Schema at runtime;
- a source missing an entire durable component could be accepted;
- recorded SQLite `PRAGMA user_version` metadata was not compared with payload/restored databases;
- exact source-commit compatibility could not be requested at restore time;
- post-restore provider health and canonical-reference integrity were operator guidance rather than a pre-serving gate;
- an unresolved recovery report could lose the one-shot pending marker and therefore needed a persistent readiness decision.

## Follow-up acceptance

The hardening implementation adds:

- packaged v1 manifest schema plus runtime Draft 2020-12 validation;
- repository/package schema parity test;
- required `db/`, `files/`, `workspaces/`, configuration metadata and canonical kernel backup anchors;
- SQLite integrity and schema-version agreement during backup verification and again on the restored data root;
- optional exact platform-commit restore pinning in addition to the existing release-version check;
- canonical Task→Project, Run→Task/Step and Workspace→Project validation;
- durable file metadata/bytes/size/SHA-256/Project validation;
- composed Control Plane provider health/readiness validation;
- persistent `ready_for_service` recovery reports;
- retry of blocked recovery reports by both `platform-server serve` and `platform-server recover-restore`;
- refusal to start the network server while unresolved Runs or readiness validation failures remain.

## Ownership boundary

#40 owns durable-state backup/restore, replacement-root portability, disaster semantics and the recovery/readiness contract.

#41 owns cross-version migrations and supported translation between different platform/database revisions.

#240 owns the packaged heterogeneous/multi-device deployment profiles. Its future relocation tests should consume the #40 contract and prove different hostnames/resource/device layouts without redefining backup semantics.
