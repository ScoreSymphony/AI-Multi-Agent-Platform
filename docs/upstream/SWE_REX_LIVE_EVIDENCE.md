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

## Campaign 1 — PR #866, workflow run 34654403144

Evaluated branch head: `461fbb5187108a8e97a0a2a9fa964429c807c2c6`  
Pinned SWE-ReX revision: `5c995c365dfb1fd5bc56fda688be5d8538f9931f`  
Package version built from that revision: `1.4.0`

### Linux LocalDeployment — functional pass, isolation rejection confirmed

Environment:

- GitHub-hosted Ubuntu 24.04 runner;
- Python 3.12.14;
- Linux `6.17.0-1022-azure` x86_64.

Observed live evidence:

- pinned SWE-ReX installed successfully;
- `LocalDeployment` started and stopped successfully;
- one-shot command returned exit code `0`;
- stdout and stderr remained distinguishable;
- an explicitly supplied synthetic environment canary was visible to the child process;
- provider write/read round-trip succeeded;
- a read of a sibling path **outside the selected temporary Workspace directory succeeded**;
- a `0.05s` command timeout raised `CommandTimeoutError` after about `0.051s`.

Interpretation:

- Linux local execution semantics are useful and fast;
- direct environment projection is demonstrably a credential exposure surface, validating the adapter's fail-closed default;
- `LocalDeployment` provides no effective Workspace containment and remains rejected as a sandbox/high-isolation profile;
- timeout signaling exists for this fixture, but child-process cleanup still requires explicit follow-up evidence.

Raw evidence artifact: workflow run `34654403144`, artifact `swe-rex-local-Linux`.

### Windows LocalDeployment — native local path fails at import

Environment:

- GitHub-hosted Windows Server 2025 runner;
- Python 3.12.10.

Installation of the exact pinned revision succeeded. Importing `swerex.deployment.local.LocalDeployment` then failed before any execution fixture could start:

```text
AttributeError: module 'pexpect' has no attribute 'spawn'
```

The failure originates while defining SWE-ReX `BashSession` in `swerex.runtime.local`, whose annotation references `pexpect.spawn`. The installed `pexpect` module on the native Windows runner does not expose that attribute.

Interpretation:

- the reviewed revision does **not** demonstrate a working native Windows `LocalDeployment` path on this available Windows runner;
- cross-platform value must not be claimed from package installation or upstream positioning alone;
- a Windows client talking to a remote Linux SWE-ReX server remains a separate hypothesis and needs its own evidence;
- no platform workaround should monkey-patch this upstream behavior into the canonical Executor path.

### DockerDeployment — first run exposed an upstream dependency packaging gap

Both `docker` and `docker-network-none` fixtures installed the exact pinned SWE-ReX revision successfully but failed while importing `swerex.deployment.docker.DockerDeployment`:

```text
ModuleNotFoundError: No module named 'aiohttp'
```

`DockerDeployment` imports `RemoteRuntime`, and `RemoteRuntime` imports `aiohttp`. At the reviewed revision the base SWE-ReX package metadata does not declare `aiohttp` among its installed dependencies.

Interpretation:

- this is an operational/packaging defect, not evidence that Docker execution itself is broken;
- campaign 2 explicitly installs `aiohttp` **only in the evaluation fixture** so Docker behavior can be measured;
- the platform package remains dependency-free with respect to SWE-ReX;
- any future supported integration must account for the effective dependency set rather than assuming the upstream base package is self-sufficient for remote-backed deployments.

## Current matrix

| Scenario | Adapter/static evidence | Live Linux | Live Windows-path | Live remote/container | Status |
| --- | --- | --- | --- | --- | --- |
| Canonical IDs preserved | Contract test added | adapter CI pending | native provider path not reached | pending | partial |
| Provider IDs namespaced | Contract test added | adapter CI pending | native provider path not reached | pending | partial |
| Success/stdout/stderr/exit mapping | Contract test added; upstream one-shot model inspected | Local: success/stdout/stderr verified | Local: import failure | Docker rerun pending | partial |
| Controlled failure mapping | Contract test added | pending | Local: import failure | pending | partial |
| Timeout | Contract test added | Local: `CommandTimeoutError` observed | Local: import failure | pending | partial |
| In-flight cancellation | Adapter seam test added | pending | Local: import failure | pending | partial |
| Child-process cleanup after timeout/cancel | none | pending | Local: import failure | pending | missing |
| Provider crash/restart cleanup | none | pending | Local: import failure | pending | missing |
| Missing Workspace rejection | Contract suite | provider Local has no such boundary | Local: import failure | pending | partial |
| `../` traversal rejection | Contract suite | provider Local has no such boundary | Local: import failure | pending | partial |
| Absolute/outside path read/write rejection | boundary design only | **outside sibling read succeeded** | Local: import failure | pending | confirms Local is not containment |
| Symlink/junction escape | canonical #37 owns policy; provider interaction untested | pending | native fixture unavailable | pending | missing |
| Artifact/file round-trip | Contract suite/fake client | Local write/read verified | Local: import failure | Docker rerun pending | partial |
| Provider reports escaped Artifact | Adapter test added | adapter CI pending | adapter CI pending | adapter CI pending | partial |
| Direct environment secret projection | rejected by adapter | provider Local canary visible | Local: import failure | Docker rerun pending | validates fail-closed adapter |
| Scoped synthetic credential use | none | only broad provider env behavior measured | Local: import failure | pending | missing |
| Credential enumeration/exfiltration | none | pending | pending | pending | missing |
| Credential redaction/revocation cleanup | none | pending | pending | pending | missing |
| Internet blocked | no uniform upstream claim accepted | not measured | not measured | `--network=none` rerun pending | missing |
| Scoped Internet allow | no uniform upstream claim accepted | not measured | not measured | default Docker rerun pending | missing |
| Loopback/private/link-local/metadata blocked | none | pending | pending | pending | missing |
| DNS/redirect/IPv6 bypass cases | none | pending | pending | pending | missing |
| Concurrent executions | fake client request-ref uniqueness | pending | pending | pending | partial |
| Duplicate/retry behavior | static server limitation identified | N/A for local fixture | pending | pending | missing |
| Local backend isolation | upstream source proves host LocalRuntime | **live outside-Workspace read confirms none** | native Local unavailable on runner | N/A | **rejected as sandbox** |
| Docker/Podman isolation | implementation path verified statically | first import blocked by missing `aiohttp`; rerun pending | not exercised | rerun pending | partial |
| Remote auth/transport | X-API-Key + HTTP behavior inspected | pending | remote-client hypothesis pending | pending | partial |
| Resource/latency overhead | none | Local startup/stop near-zero in fixture | Local unavailable | pending | partial |
| Reference Executor unaffected when SWE-ReX absent | PoC adds no runtime dependency | canonical CI pending | canonical CI pending | N/A | partial |

## Required live fixture sequence

### A. Linux local execution semantics

Core one-shot execution/file/timeout semantics have now been demonstrated. Remaining local work:

1. controlled non-zero exit mapping;
2. child-process survival/cleanup after timeout;
3. explicit interrupt/cancellation behavior;
4. concurrent execution behavior;
5. canonical adapter integration against a concrete client, if local mode remains useful as a trusted-host option.

The result continues to label LocalDeployment as unsandboxed regardless of functional success.

### B. Docker or Podman backend

Campaign 2 retries after explicitly installing the missing `aiohttp` remote-runtime dependency. Then run the canonical fixture plus:

1. host filesystem read/write attempts outside the intended mount/materialization;
2. process/container cleanup after timeout/cancel/crash;
3. CPU/RAM limit behavior if configured;
4. network deny/allow and bypass targets;
5. synthetic secret delivery and exfiltration attempts;
6. concurrent execution density and cold-start overhead.

Record the exact container runtime, image digest and arguments.

### C. Windows-path behavior

Native `LocalDeployment` is currently blocked by the reviewed upstream implementation on the available Windows runner. Remaining Windows work therefore separates two questions:

1. whether the native local runtime can be supported without carrying a platform-owned compatibility fork;
2. whether a Windows-side SWE-ReX remote client can reliably target a supported Linux/container runtime while preserving canonical Windows Workspace/File semantics.

Do not infer Windows support from Linux tests.

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
