# SWE-ReX evaluation (#861)

Status: **final evaluation decision**  
Outcome: **`experimental_only`**

SWE-ReX is evaluated only as an optional implementation behind the platform-owned Executor and Worker boundaries. It does not own canonical Task, Run, Workspace, File, Artifact, authorization, secret, retry, idempotency, egress, or lifecycle semantics.

## Evaluated upstream

- repository: `https://github.com/SWE-agent/SWE-ReX`
- revision: `5c995c365dfb1fd5bc56fda688be5d8538f9931f`
- package metadata version: `1.4.0`
- license: MIT (`LICENSE.txt` at the reviewed revision)
- reviewed: 2026-09-12
- published tag note: `v1.4.0` points to the older commit `f802b3e14d82aa4c13291d2fda5bd4fd48f36f91`; #861 evidence is tied to the exact revision above, not merely the version string.

The reviewed configuration exposes local, Docker/Podman, remote, Modal, Fargate, dummy and Daytona deployment paths. Only Local, Docker and loopback Remote were exercised by #861; unexercised backends remain unknown.

## Architectural fit

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
Canonical ExecutionResult / collected Artifact evidence
```

The PoC in `src/ai_multi_agent_platform/adapters/swe_rex.py` intentionally has no SWE-ReX runtime dependency. Provider-private request/deployment/runtime/session identities remain namespaced diagnostics. Canonical `task_id`, `run_id`, `step_id`, `correlation_id`, Workspace selection and Artifact ownership remain platform-owned.

The adapter also:

- requires explicit opt-in for unsandboxed local execution;
- normalizes backend names before applying that guard;
- rejects non-empty canonical `ExecutionRequest.environment` until #34-safe scoped delivery is proven;
- does not forward `policy_context` as provider authority;
- revalidates returned Artifact paths and requires the collected file to exist inside the canonical Workspace;
- recomputes Artifact size from the collected file;
- filters provider metadata and prevents provider values from overriding canonical backend classification;
- keeps provider failures/health errors from leaking arbitrary exception text into canonical metadata.

## Backend findings

### Linux LocalDeployment

Linux Local is functionally useful. Live evidence verifies command execution, stdout/stderr, file operations, timeout signaling and a full canonical Local bridge including canonical IDs, controlled failure, Artifact collection and fail-closed platform guards.

It is not a sandbox. A sibling path outside the selected Workspace is readable, and a directly supplied environment canary is visible to child code. The reviewed `LocalRuntime.execute()` calls synchronous `subprocess.run(...)` from an async method, so in-flight cancellation cannot be treated as a reliable process-kill guarantee while that call blocks.

Classification: **experimental trusted-host execution only**.

### Native Windows LocalDeployment

The exact revision installs on Windows Server 2025 / Python 3.12, but importing LocalDeployment fails before execution because `pexpect.spawn` is unavailable on the exercised native Windows path.

Classification: **unsupported at the evaluated revision**. Windows remote-client behavior is a separate, unproven profile.

### Default DockerDeployment

The exact pinned server image passes raw execution and Artifact round-trip evidence. The observed default profile also:

- permits Internet egress (`http://example.com` returned HTTP 200);
- permits provider file reads outside the intended provider Workspace (`/etc/hostname`);
- publishes the runtime server port on IPv4 all-interfaces and IPv6;
- allows a spawned child process to survive the parent command timeout;
- does not persist the synthetic command environment canary into the next exercised command;
- starts/stops quickly in the fixture (~0.78s / ~0.22s) with a ~177 MB test image.

The corrected canonical Docker bridge subsequently passed end-to-end in workflow run `34696440396`: health, canonical ID preservation, echo success, Artifact collection, controlled failure, timeout mapping, fail-closed environment handling and Workspace-traversal rejection all passed. The earlier bridge failure was an evaluation-fixture bug caused by uploading an empty host Workspace to a provider directory that did not yet exist; explicitly creating the provider Workspace before upload fixed the fixture.

Classification: **experimental runtime backend, not a sandbox/security profile**.

### Deny-egress Docker profiles

Both `--network=none` and Docker `--internal` network experiments break the host-to-runtime HTTP path SWE-ReX itself needs for DockerDeployment startup. #861 therefore did not establish a working Docker executor with deny-by-default external egress by applying those network flags directly.

Classification: **no supported deny-egress Docker profile demonstrated**. A future protected profile would need an external network/proxy design plus fresh evidence.

### Remote loopback

The exact `swerex-remote` server passes loopback auth, wrong-key rejection, command/file operations, four concurrent commands and server cleanup. The provider can still read outside the selected Workspace. The reviewed transport is HTTP unless TLS/private-network protection is provided externally.

Classification: **experimental remote runtime only**, with external transport/network trust requirements.

## Packaging and provenance

`swerex.runtime.remote` imports `aiohttp`, but the reviewed base package does not declare it. The evaluation workflow installs `aiohttp>=3.11,<4` only for remote-backed evidence. The platform baseline remains SWE-ReX-free.

Docker server provenance also requires independent pinning. Host-side package pinning does not prove the container server revision; accepted #861 evidence builds the server image from the exact evaluated commit and uses `pull="never"`. An unpinned fallback server is not accepted as reproducible evidence.

## Security and ownership conclusion

SWE-ReX can sit behind the canonical Executor boundary only as a **backend abstraction with variable trust/isolation**. It cannot replace:

- #14 dispatch/idempotency/lifecycle ownership;
- #15 authorization/approval;
- #34 secret ownership and scoped delivery;
- #37 Workspace/File/Artifact ownership;
- #43 threat-model and egress policy;
- platform service/worker identity and observability.

The live evidence specifically rejects any generic claim that selecting SWE-ReX implies Workspace containment, deny-by-default egress, process-tree cleanup, native Windows-local portability, or secure remote transport.

## Why the outcome is `experimental_only`

`supported_optional` would be too strong because the evaluated profiles have material security/portability/lifecycle gaps. `reject/defer` would also be too strong because Linux Local, pinned Docker and loopback Remote demonstrate real runtime-abstraction value and the canonical adapter can preserve platform ownership cleanly.

The appropriate scope is therefore:

- **Linux Local**: Advanced/experimental trusted-host execution only;
- **pinned Docker**: Advanced/experimental runtime backend only;
- **protected Remote**: Advanced/experimental runtime backend only, requiring external transport/network controls;
- **native Windows Local**: unavailable at the evaluated revision;
- **sandbox-required or deny-egress profiles**: do not select SWE-ReX based on #861 evidence.

## Product/discovery implication

#799 is completed. Its candidate taxonomy maps the #861 result to `experimental/evaluate`, so SWE-ReX may be surfaced only in Advanced/Custom discovery with backend-specific capability/risk metadata. It must not advertise generic sandboxing, native Windows-local support, deny-by-default egress, provider-owned Workspace security, or uniform cancellation/cleanup.

#798 is completed as the source-backed architecture/security/adapter evaluation for the separate high-isolation Agent-Sandbox candidate. Production-shaped runtime evidence and Agent-Sandbox's final support classification are owned by #829. Closing the #861 comparison dependency does not upgrade Agent-Sandbox to a proven production isolation profile and does not substitute for #829's decision.

## Re-evaluation rule

Any future promotion attempt must pin the then-current upstream revision/server image and rerun canonical contract, Workspace/Artifact, credential, egress, cancellation/process-tree cleanup, transport/auth, provenance and resource evidence for one explicitly defined supported profile. Unknown cloud/provider paths remain unknown until exercised.
