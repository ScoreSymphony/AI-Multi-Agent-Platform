# SWE-ReX evaluation (#861)

Status: **in progress**  
Provisional outcome: **`experimental_only`**

This document separates verified upstream facts, platform adapter evidence and live evidence. SWE-ReX is evaluated only as an optional implementation behind the platform-owned Executor and Worker boundaries. It does not own canonical Task, Run, Workspace, File, Artifact, authorization, secret, retry or dispatch semantics.

## Evaluated upstream

- repository: `https://github.com/SWE-agent/SWE-ReX`
- revision: `5c995c365dfb1fd5bc56fda688be5d8538f9931f`
- package version: `1.4.0`
- license: MIT (`LICENSE.txt` at the reviewed revision)
- reviewed: 2026-09-12

The reviewed deployment configuration exposes local, Docker/Podman, remote, Modal, Fargate, dummy and Daytona implementations. The runtime API exposes one-shot command execution, persistent bash sessions, file read/write/upload operations and health/close operations.

## Architectural fit

Preferred seam:

```text
Canonical ExecutionRequest
        |
        v
    SwerexExecutor
        |
        v
 provider-private client
        |
        v
 SWE-ReX deployment/runtime
        |
        v
Canonical ExecutionResult / Artifact evidence
```

The proof-of-concept in `src/ai_multi_agent_platform/adapters/swe_rex.py` intentionally does **not** import or require `swe-rex`. The live bridge in `scripts/benchmarks/issue861_swe_rex_canonical.py` supplies a concrete evaluation-only client against the exact pinned upstream runtime. Absence of SWE-ReX must not change the reference Executor path.

### Canonical ownership retained

The adapter preserves canonical `task_id`, `run_id`, `step_id` and `correlation_id`. Provider request, deployment, runtime and session identities stay adapter-private and are emitted only as namespaced diagnostics under `adapter_metadata["swe_rex"]`.

Workspace selection remains platform-owned. The adapter resolves a canonical materialized workspace beneath a configured root, rejects traversal/missing workspaces and rejects provider-returned Artifact paths outside the selected workspace.

Authorization and policy are not delegated to SWE-ReX. `ExecutionRequest.policy_context` is deliberately not forwarded as provider authority. Direct `ExecutionRequest.environment` forwarding is rejected until scoped delivery through #34 can be proven.

## Backend classification

SWE-ReX is an execution/runtime abstraction, **not one uniform isolation technology**.

| Backend/path | Reviewed meaning | Evidence / isolation claim at this stage |
| --- | --- | --- |
| Local on Linux | `LocalDeployment` wraps `LocalRuntime` on the host | Live functional evidence exists; **no isolation** and outside-Workspace reads are possible |
| Local on Windows | Native `LocalRuntime` path | Exact pinned package installs, but `LocalDeployment` import fails on the available Windows runner because `pexpect.spawn` is unavailable |
| Docker/Podman | Starts a container and talks to a SWE-ReX server through `RemoteRuntime` | Evidence campaign in progress; must pin the server image separately from the host package |
| Remote | Connects to an already-running SWE-ReX server | Depends entirely on remote host/deployment boundary; live evidence pending |
| Modal | Cloud deployment adapter | Provider-specific; not measured here yet |
| Fargate | AWS Fargate deployment adapter | Provider-specific; not measured here yet |
| Daytona | Optional provider integration | Upstream/provider-specific; not measured here yet |
| Dummy | Test implementation | No production isolation claim |

The platform PoC therefore rejects `backend_kind="local"` unless an explicit `allow_unsandboxed_local=True` opt-in is supplied. Discovery/setup must never label local mode as a sandbox.

## Live Linux LocalDeployment evidence

The first GitHub-hosted Ubuntu 24.04 / Python 3.12 campaign against the exact pinned revision demonstrated:

- successful one-shot execution with exit code `0`;
- separate stdout and stderr;
- provider file write/read round-trip;
- timeout signaling via `CommandTimeoutError` at a `0.05s` command timeout;
- visibility of an explicitly supplied synthetic environment canary in the child process;
- successful read of a sibling file outside the selected temporary Workspace directory.

This is useful evidence for trusted-host execution semantics, but it directly rejects LocalDeployment as a Workspace containment or sandbox mechanism. Child-process cleanup after timeout/cancellation still requires explicit evidence.

## Native Windows result

On the available Windows Server 2025 / Python 3.12 runner, the exact pinned SWE-ReX revision built and installed successfully. Importing `LocalDeployment` then failed before any command could be executed:

```text
AttributeError: module 'pexpect' has no attribute 'spawn'
```

The failure arises while defining `swerex.runtime.local.BashSession`. Therefore #861 currently has **negative native-Windows LocalDeployment evidence**. A Windows-side client targeting a supported Linux/container remote server is a different architecture and remains a separate hypothesis.

## Docker/Remote packaging and provenance findings

The first Docker evidence run exposed an upstream packaging gap before runtime start: `RemoteRuntime` imports `aiohttp`, while the reviewed base SWE-ReX package does not declare/install it. The evidence workflow now installs `aiohttp` explicitly for Docker/remote-runtime evaluation only. This dependency is not added to the platform baseline.

A second provenance issue is equally important: `DockerDeployment` does not automatically propagate the host package revision into the container server. If the selected image does not already contain `swe-rex`, its startup path can fall back to `pipx run swe-rex`, which is not pinned to the host revision.

Therefore reproducible Docker evidence in #861 now:

1. installs the exact evaluated revision on the host;
2. builds a dedicated container server image from the same exact revision;
3. passes that image to `DockerDeployment` with `pull="never"`;
4. records the image identity in workflow logs/evidence.

Docker results from an unpinned fallback server are not accepted as evidence for this issue.

## Command and result mapping

The reviewed SWE-ReX command model supports command text/argv, timeout, shell mode, exit checking, environment variables, working directory and separated/merged stdout/stderr. `CommandResponse` exposes stdout, stderr and exit code. These concepts map cleanly to the canonical Executor result model at a structural level.

Important limitations remain:

- canonical cancellation must be proven against each concrete deployment/runtime path; the generic API does not by itself establish kill/cleanup guarantees;
- stdout/stderr streaming is not established by the one-shot `CommandResponse` API and must not be claimed without live evidence;
- retries and platform-level retry policy remain outside the adapter;
- resource data is backend-specific and may be unavailable;
- shell sessions are provider-private execution mechanics, not canonical Run identity.

## File and Artifact mapping

SWE-ReX exposes provider file read/write/upload primitives. They take provider paths directly. The remote server similarly accepts target paths and runs a local runtime. These APIs are useful implementation tools but are **not** canonical Workspace/File/Artifact APIs.

The platform must continue to:

1. materialize the canonical Workspace;
2. constrain the provider to the approved materialization;
3. reject traversal/out-of-bound provider evidence;
4. collect changes as canonical Files/Artifacts;
5. clean provider-private state independently of Artifact durability.

The platform PoC validates the selected canonical Workspace and returned Artifact evidence. The live canonical bridge additionally performs explicit provider-to-canonical Artifact collection. Remote/container escape tests are still required to prove the effective provider-side boundary.

## Remote transport observations

At the reviewed revision, `RemoteRuntime` authenticates with an `X-API-Key` when configured. It accepts HTTP hosts and can prepend `http://` when no scheme is present. The server defaults to binding `0.0.0.0` and exposes command and file operations. This is not sufficient by itself for a production trust boundary.

Any supported remote profile must therefore require an appropriately trusted/private network or TLS termination and must keep the API token behind #34 rather than ordinary configuration or agent-visible environment state.

The server's request-response cache retains only the last processed request and explicitly does not guarantee idempotency for multiple concurrent clients. Canonical #14 worker-job idempotency/dispatch semantics therefore remain authoritative.

## Contract and bridge evidence implemented in this branch

`tests/test_swe_rex_executor.py` reuses `ExecutorContractSuite` and adds provider-specific assertions for:

- canonical success/failure/timeout/cancellation behavior;
- canonical ID preservation;
- private unique provider request references;
- provider deployment/runtime identities namespaced under adapter metadata;
- missing/traversing Workspace rejection;
- Artifact round-trip and escaped Artifact rejection;
- fail-closed environment/secret projection;
- provider metadata filtering;
- prevention of provider authority from arbitrary policy context;
- explicit opt-in for the unsandboxed local backend;
- health metadata with the exact reviewed upstream pin.

`scripts/benchmarks/issue861_swe_rex_canonical.py` then exercises the same platform boundary against live SWE-ReX runtime paths. It checks canonical IDs, health/capabilities, success/failure/timeout mapping, provider-private metadata, Artifact collection and the fail-closed Workspace/environment guards. Live bridge results are recorded separately from deterministic fake-client contract tests.

## Cross-platform hypothesis after first evidence

SWE-ReX still has potential value because one deployment/runtime abstraction can target direct local execution, Docker/Podman, a separately hosted server and several remote/cloud backends. That is materially different from adopting one sandbox runtime only.

However, the original cross-platform hypothesis is now weaker:

- Linux LocalDeployment is functionally useful but unsandboxed;
- native Windows LocalDeployment fails on the available runner at the reviewed revision;
- Docker/Remote paths have an undeclared host dependency (`aiohttp`) at this pin;
- Docker server provenance must be pinned separately from the host library;
- security properties remain backend-specific rather than properties of SWE-ReX as a whole.

The remaining value proposition is therefore primarily **backend/runtime abstraction**, not “one portable sandbox.”

## Current decision

**`experimental_only`** remains the only supported conclusion from the current evidence.

Positive evidence:

- clean structural fit behind the canonical Executor boundary;
- useful Linux trusted-host runtime semantics;
- potentially useful abstraction across container/remote/cloud deployment types;
- command/result and file-transfer primitives are sufficient for a minimal adapter seam;
- MIT licensing and no mandatory paid service for self-hosted paths;
- the platform can keep SWE-ReX entirely optional.

Negative evidence / blockers:

- LocalDeployment has no Workspace containment and therefore cannot satisfy a sandbox-required profile;
- native Windows LocalDeployment is currently unusable on the exercised runner;
- base packaging omits `aiohttp` needed by the reviewed RemoteRuntime path;
- Docker server provenance is not automatically tied to the host package revision;
- effective container/remote isolation must still be measured per backend;
- network egress controls/escape paths are not established as a uniform capability;
- scoped credential delivery and exfiltration tests are pending;
- cancellation/crash/child-process cleanup is pending;
- stdout/stderr streaming claims are unproven;
- remote transport hardening and operational burden need measurement;
- #798 comparison evidence and #799 discovery taxonomy are still in progress.

Promotion to `supported_optional` requires closing the relevant evidence gaps for a precisely defined supported profile. If the viable profile becomes too narrow or SWE-ReX adds little beyond direct platform adapters, the correct outcome should be `reject/defer` rather than weakening canonical platform boundaries.
