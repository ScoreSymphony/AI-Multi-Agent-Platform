# SWE-ReX live evidence log (#861)

This file distinguishes completed adapter/static evidence from **live backend evidence**. No row may be marked passed from upstream documentation or interface shape alone.

## Evidence policy

For every exercised backend record:

- host OS/architecture;
- SWE-ReX revision/version;
- deployment/backend type and relevant runtime version;
- exact fixture/command;
- canonical request/result mapping;
- wall-clock timing where relevant;
- cleanup state;
- security-policy preconditions;
- raw evidence location or CI/run identifier.

Provider guarantees are recorded per backend. A passing Docker test does not establish Local, Remote, Modal, Fargate or Daytona security properties.

## Current matrix

| Scenario | Adapter/static evidence | Live Linux | Live Windows-path | Live remote/container | Status |
| --- | --- | --- | --- | --- | --- |
| Canonical IDs preserved | Contract test added | pending | pending | pending | partial |
| Provider IDs namespaced | Contract test added | pending | pending | pending | partial |
| Success/stdout/stderr/exit mapping | Contract test added; upstream one-shot model inspected | pending | pending | pending | partial |
| Controlled failure mapping | Contract test added | pending | pending | pending | partial |
| Timeout | Contract test added | pending | pending | pending | partial |
| In-flight cancellation | Adapter seam test added | pending | pending | pending | partial |
| Child-process cleanup after timeout/cancel | none | pending | pending | pending | missing |
| Provider crash/restart cleanup | none | pending | pending | pending | missing |
| Missing Workspace rejection | Contract suite | pending | pending | pending | partial |
| `../` traversal rejection | Contract suite | pending | pending | pending | partial |
| Absolute/outside path read/write rejection | boundary design only | pending | pending | pending | missing |
| Symlink/junction escape | canonical #37 owns policy; provider interaction untested | pending | pending | pending | missing |
| Artifact round-trip | Contract suite/fake client | pending | pending | pending | partial |
| Provider reports escaped Artifact | Adapter test added | pending | pending | pending | partial |
| Direct environment secret projection | rejected by adapter | pending | pending | pending | partial |
| Scoped synthetic credential use | none | pending | pending | pending | missing |
| Credential enumeration/exfiltration | none | pending | pending | pending | missing |
| Credential redaction/revocation cleanup | none | pending | pending | pending | missing |
| Internet blocked | no uniform upstream claim accepted | pending | pending | pending | missing |
| Scoped Internet allow | no uniform upstream claim accepted | pending | pending | pending | missing |
| Loopback/private/link-local/metadata blocked | none | pending | pending | pending | missing |
| DNS/redirect/IPv6 bypass cases | none | pending | pending | pending | missing |
| Concurrent executions | fake client request-ref uniqueness | pending | pending | pending | partial |
| Duplicate/retry behavior | static server limitation identified | pending | pending | pending | missing |
| Local backend isolation | upstream source proves host LocalRuntime | N/A: no isolation claim | N/A | N/A | **rejected as sandbox** |
| Docker/Podman isolation | implementation path verified statically | pending | pending | pending | missing |
| Remote auth/transport | X-API-Key + HTTP behavior inspected | pending | pending | pending | partial |
| Resource/latency overhead | none | pending | pending | pending | missing |
| Reference Executor unaffected when SWE-ReX absent | PoC adds no runtime dependency | CI pending | CI pending | N/A | partial |

## Required live fixture sequence

### A. Linux local execution semantics

This is **not** a sandbox test. It should establish only cross-platform runtime semantics against a disposable test host/user:

1. one-shot success with separated stdout/stderr;
2. non-zero exit;
3. cwd/workspace mapping;
4. Artifact creation/round-trip;
5. timeout and child process observation;
6. interrupt/cancellation behavior;
7. cleanup/close.

The result must continue to label LocalDeployment as unsandboxed regardless of functional success.

### B. Docker or Podman backend

Run the same canonical fixture plus:

1. host filesystem read/write attempts outside the intended mount/materialization;
2. process/container cleanup after timeout/cancel/crash;
3. CPU/RAM limit behavior if configured;
4. network deny/allow and bypass targets;
5. synthetic secret delivery and exfiltration attempts;
6. concurrent execution density and cold-start overhead.

Record the exact container runtime, image digest and arguments.

### C. Windows-path behavior

Where a Windows runner/host is available, exercise:

1. canonical Workspace mapping with Windows path separators;
2. `..` traversal;
3. absolute drive path attempts;
4. UNC/device-path variants where relevant;
5. junction/symlink behavior subject to runner privileges;
6. process timeout/cancellation semantics;
7. Artifact round-trip.

Do not infer these results from Linux tests.

### D. Remote server path

Exercise a disposable remote/server instance through a protected test transport:

1. authentication success/failure;
2. canonical Workspace mapping on the remote host;
3. duplicate/retry behavior under concurrent requests;
4. timeout/cancellation/connection loss;
5. server/runtime crash and restart cleanup;
6. Artifact upload/download round-trip;
7. secret handling without storing raw token in ordinary profile/evidence;
8. transport exposure and TLS/private-network assumptions.

## Promotion rule

`experimental_only` remains binding until the live matrix demonstrates the supported profile(s) with reproducible evidence. Unsupported backends must stay explicitly unsupported rather than inheriting guarantees from another backend.
