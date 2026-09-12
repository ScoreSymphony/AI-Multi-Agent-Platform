# SWE-ReX security review (#861)

Reviewed upstream revision: `5c995c365dfb1fd5bc56fda688be5d8538f9931f`  
Review date: 2026-09-12  
Security classification: **`experimental_only`**

This review combines static source inspection, canonical adapter tests, and live Local/Docker/Remote evidence. SWE-ReX is treated as an optional runtime implementation below platform-owned authorization, secrets, Workspace, Worker and security boundaries.

## Trust boundary

```text
Actor / Agent output
        |
        v
#15 Authorization / Approval
        |
        v
canonical Executor request
        |
        v
SwerexExecutor boundary
        |
        +--> #37 Workspace materialization
        +--> #34 scoped secrets (no direct env projection in PoC)
        |
        v
SWE-ReX backend
```

Selecting or installing SWE-ReX grants no authority and establishes no generic sandbox guarantee.

## 1. LocalDeployment is host execution

Live Linux evidence confirms LocalDeployment can execute and access files outside the selected canonical Workspace. Directly supplied environment data is visible to child code.

Controls in #861:

- local mode requires explicit `allow_unsandboxed_local=True`;
- discovery must label it trusted-host/unsandboxed;
- sandbox-required or untrusted workloads must not select it based on this evaluation.

Residual risk if mislabeled as sandbox: **high**.

## 2. Provider file APIs are not the canonical Workspace boundary

Local, Docker and loopback Remote evidence all show that provider file operations can address paths beyond the intended per-run Workspace. Docker read `/etc/hostname`; Local/Remote read deliberately created outside-Workspace files.

Platform controls therefore remain mandatory:

- canonical #37 Workspace materialization and authorization;
- provider requests constrained by adapter-owned mapping;
- returned Artifact path validation;
- acceptance only after Artifact collection into the canonical Workspace.

The PoC enforces those result-side/platform guards, but provider success never proves path authorization.

## 3. Remote auth is useful but transport security is external

The reviewed server uses `X-API-Key`. Live loopback evidence accepts the correct key and rejects the wrong key. The transport remains HTTP in the exercised profile; TLS/private-network protection is external.

Implications:

- tokens belong behind #34, not ordinary profiles;
- arbitrary reachable HTTP endpoints must not become trusted executors through discovery alone;
- #15/#36 service identity and Worker trust remain independent of provider authentication;
- token rotation/redaction/revocation are platform/deployment responsibilities.

## 4. Default Docker network exposure is not deny-by-default

Live default Docker evidence:

- outbound Internet access succeeded (HTTP 200 to `example.com`);
- the runtime control port was published on `0.0.0.0` and IPv6;
- provider file APIs could read outside the intended provider Workspace.

Attempts to add `--network=none` or Docker `--internal` networking broke the host-to-runtime HTTP control channel needed by DockerDeployment startup. Therefore #861 has no working deny-egress Docker profile using these flags alone.

A future protected profile needs a separately designed network/proxy boundary and must fail closed if that policy cannot be enforced.

## 5. Timeout does not establish process-tree cleanup

Live Docker evidence produced `CommandTimeoutError` for the parent while a spawned child survived and later wrote its marker file. The reviewed LocalRuntime one-shot implementation uses `subprocess.run(..., timeout=...)`; uniform descendant cleanup is not provided by the abstraction.

Additionally, `LocalRuntime.execute()` performs that synchronous subprocess call from an async method. Canonical `CancellationToken` observation cannot interrupt the event loop while the call is blocking, so adapter-level cancellation semantics do not prove real LocalRuntime in-flight kill semantics.

Implications:

- timeout result mapping may be used as evidence of signaling only;
- cancellation/process-tree cleanup must be separately proven for any future supported backend;
- sandbox-required workloads cannot rely on #861 for cleanup containment.

## 6. Environment variables remain a secret/exfiltration surface

Raw provider fixtures show an explicitly injected synthetic value is visible to executed code. In the exercised Docker fixture it did not persist into a later command, but that does not prove a complete secret lifecycle.

The platform PoC therefore rejects every non-empty canonical `ExecutionRequest.environment`. Any future concrete secret delivery path must use explicitly authorized #34-scoped material and separately test enumeration, inheritance, output leakage, redaction, revocation and cleanup.

## 7. Canonical idempotency remains platform-owned

SWE-ReX remote request IDs and its limited request-response cache are provider mechanics. They cannot replace #14 WorkerJob dispatch/idempotency, especially with concurrent clients.

Provider request IDs remain adapter-private metadata; duplicate execution/reconciliation remains a platform responsibility.

## 8. Server/client provenance must be pinned independently

Host-side SWE-ReX revision pinning does not guarantee that DockerDeployment starts the same server revision. Accepted #861 Docker evidence builds an image directly from the exact evaluated commit and uses `pull="never"`.

An unpinned provider fallback is not acceptable evidence for a supported execution/security profile.

## 9. Dependency completeness is an operational security concern

At the reviewed revision `swerex.runtime.remote` imports `aiohttp`, while the base package does not declare it. Remote-backed fixtures explicitly install `aiohttp>=3.11,<4`; the platform baseline does not.

A future integration must pin and audit the effective dependency set rather than silently relying on ambient packages.

## 10. Provider metadata is diagnostic only

Provider image/platform/version/runtime IDs and errors cannot authorize actions. The PoC allowlists diagnostic metadata, keeps deployment/runtime/session IDs under `adapter_metadata["swe_rex"]`, prevents provider `backend_kind` override, and redacts unexpected exception text to type-only diagnostic information.

## Security conclusion

SWE-ReX fits safely only as an **experimental backend abstraction with variable trust/isolation** behind existing platform controls. #861 specifically does not establish SWE-ReX as:

- a generic sandbox;
- a Workspace security boundary;
- a deny-egress network boundary;
- a secret boundary;
- a uniform cancellation/process-cleanup boundary;
- a secure remote transport layer;
- a native Windows-local executor at the evaluated revision.

Promotion requires a new evaluation of one precisely defined profile against the then-current upstream revision, including Workspace escape, explicit deny/allow egress (including private/link-local/IPv6 where relevant), scoped-secret exfiltration/redaction/revocation, cancellation/process-tree cleanup, remote transport/auth/provenance and operational resource limits.
