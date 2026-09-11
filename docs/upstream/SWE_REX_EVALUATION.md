# SWE-ReX evaluation (#861)

Status: **in progress**  
Provisional outcome: **`experimental_only`**

This document separates verified upstream facts, platform adapter evidence and live evidence that is still required. SWE-ReX is evaluated only as an optional implementation behind the platform-owned Executor and Worker boundaries. It does not own canonical Task, Run, Workspace, File, Artifact, authorization, secret or retry semantics.

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

The proof-of-concept in `src/ai_multi_agent_platform/adapters/swe_rex.py` intentionally does **not** import or require `swe-rex`. A future concrete client may use SWE-ReX, but absence of that dependency must not change the reference Executor path.

### Canonical ownership retained

The adapter preserves canonical `task_id`, `run_id`, `step_id` and `correlation_id`. Provider request, deployment, runtime and session identities stay adapter-private and are emitted only as namespaced diagnostics under `adapter_metadata["swe_rex"]`.

Workspace selection remains platform-owned. The adapter resolves a canonical materialized workspace beneath a configured root, rejects traversal/missing workspaces and rejects provider-returned Artifact paths outside the selected workspace.

Authorization and policy are not delegated to SWE-ReX. `ExecutionRequest.policy_context` is deliberately not forwarded as provider authority. Direct `ExecutionRequest.environment` forwarding is rejected until scoped delivery through #34 can be proven.

## Backend classification

SWE-ReX is an execution/runtime abstraction, **not one uniform isolation technology**.

| Backend/path | Reviewed meaning | Isolation claim at this stage |
| --- | --- | --- |
| Local | `LocalDeployment` wraps `LocalRuntime` on the host | **None. Unsandboxed host execution.** |
| Docker/Podman | Starts a container/runtime and communicates with a remote runtime | Container isolation only; live security evidence pending |
| Remote | Connects to an already-running SWE-ReX server | Depends entirely on remote host/deployment boundary |
| Modal | Cloud deployment adapter | Provider-specific; not measured here yet |
| Fargate | AWS Fargate deployment adapter | Provider-specific; not measured here yet |
| Daytona | Optional provider integration | Upstream/provider-specific; not measured here yet |
| Dummy | Test implementation | No production isolation claim |

The platform PoC therefore rejects `backend_kind="local"` unless an explicit `allow_unsandboxed_local=True` opt-in is supplied. Discovery/setup must never label that mode as a sandbox.

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

The PoC enforces steps 2 and 3 at its boundary for returned Artifact evidence. Live remote/container tests are still required to prove that provider-side operations cannot bypass the intended effective boundary.

## Remote transport observations

At the reviewed revision, `RemoteRuntime` authenticates with an `X-API-Key` when configured. It accepts HTTP hosts and can prepend `http://` when no scheme is present. The server defaults to binding `0.0.0.0` and exposes command and file operations. This is not sufficient by itself for a production trust boundary.

Any supported remote profile must therefore require an appropriately trusted/private network or TLS termination and must keep the API token behind #34 rather than ordinary configuration or agent-visible environment state.

The server's request-response cache retains only the last processed request and explicitly does not guarantee idempotency for multiple concurrent clients. Canonical #14 worker-job idempotency/dispatch semantics therefore remain authoritative.

## Contract evidence implemented in this branch

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

These are adapter/contract tests with a deterministic fake client. They are not evidence of live backend isolation.

## Cross-platform hypothesis

SWE-ReX has potential value because one deployment/runtime abstraction can target direct local execution, Docker/Podman, a separately hosted server and several remote/cloud backends. That is materially different from adopting one sandbox runtime only.

However, cross-platform value is not accepted from interface shape alone. #861 still requires:

- live Linux behavior;
- available Windows-path behavior, including path normalization and process behavior;
- Docker/Podman or another isolated local path;
- at least one relevant remote/server path;
- consistent canonical result mapping across the exercised backends.

## Current decision

**`experimental_only`** is the only supported conclusion from the current evidence.

Positive evidence:

- clean structural fit behind the canonical Executor boundary;
- useful backend abstraction across local/container/remote/cloud deployment types;
- command/result and file-transfer primitives are sufficient for a minimal adapter seam;
- MIT licensing and no mandatory paid service for self-hosted paths;
- the platform can keep SWE-ReX entirely optional.

Blocking evidence gaps:

- effective isolation must be measured separately per backend;
- network egress controls/escape paths are not established as a uniform capability;
- scoped credential delivery and exfiltration tests are pending;
- live timeout/cancellation/crash/process cleanup are pending;
- stdout/stderr streaming claims are unproven;
- Linux and Windows-path interoperability has not yet been demonstrated in this repository;
- remote transport hardening and operational burden need measurement;
- #798 comparison evidence and #799 discovery taxonomy are still in progress.

Promotion to `supported_optional` requires closing those evidence gaps. A failure to demonstrate meaningful cross-platform value or acceptable containment/operations should result in `reject/defer` rather than weakening canonical platform boundaries.
