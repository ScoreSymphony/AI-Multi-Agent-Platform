# SWE-ReX security review (#861)

Reviewed upstream revision: `5c995c365dfb1fd5bc56fda688be5d8538f9931f`  
Review date: 2026-09-12  
Status: static review plus platform adapter tests; live backend security evidence pending.

## Trust-boundary rule

SWE-ReX is treated as an optional execution implementation below canonical authorization, secret, Workspace and Worker boundaries. Installation or selection of the adapter grants no authority by itself.

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
        +--> #34 scoped secrets (future concrete path)
        |
        v
SWE-ReX backend
```

## Finding 1: LocalDeployment is host execution

The reviewed `LocalDeployment` creates a `LocalRuntime` in the current host process environment. It is useful as a cross-platform execution abstraction but is not a containment mechanism.

Platform consequence:

- local mode must never be described as sandboxed;
- high-risk or untrusted workloads must not select it through a sandbox-required profile;
- the evaluation adapter requires an explicit `allow_unsandboxed_local=True` opt-in;
- #799 discovery metadata must distinguish execution availability from isolation strength.

Severity for sandbox-required workloads: **high if mislabeled**, otherwise an explicit trusted-host execution mode.

## Finding 2: provider file APIs do not enforce canonical Workspace ownership

The runtime API exposes direct file read/write/upload operations using provider paths. The reviewed remote server also accepts a target path and performs the operation through its local runtime.

Platform consequence:

- canonical Workspace materialization must remain the source of allowed paths;
- provider operations must be constrained to that materialization;
- returned Artifact paths must be revalidated at the canonical boundary;
- live fixtures must attempt reads/writes outside the Workspace, including traversal and platform-specific path forms;
- remote provider success must never be interpreted as proof that a path was authorized.

The PoC validates selected workspaces and returned Artifact paths, but that alone does not prove provider-side filesystem containment.

## Finding 3: remote authentication is a bearer API key, transport protection is external

The reviewed remote path sends `X-API-Key` when an auth token is configured. `RemoteRuntime` accepts HTTP hosts, and the server binds to `0.0.0.0` by default when launched through its CLI.

Platform consequence:

- the token must be a #34-managed secret/reference, not normal profile state;
- protected deployments need a trusted/private network or TLS termination outside SWE-ReX;
- discovery should not promote an arbitrary reachable HTTP SWE-ReX server to a trusted executor;
- #15/#36 service identity and worker trust remain independent of provider authentication;
- token rotation/revocation and redaction require platform controls.

## Finding 4: remote server exposes privileged execution and file operations

The reviewed server exposes endpoints for command execution, persistent shell sessions, file read/write/upload and runtime close. Compromise of that service or its API token therefore has direct execution impact within the server host's effective privilege boundary.

Platform consequence:

- run SWE-ReX remote services with the minimum OS/container privileges needed;
- do not reuse broad host credentials or platform administrator identity;
- isolate server filesystem/network access according to the backend threat model;
- audit executor selection and remote target identity through platform-owned telemetry.

## Finding 5: canonical idempotency cannot rely on SWE-ReX request caching

The server caches only the last processed request/response and explicitly notes that idempotency is not guaranteed for multiple concurrent clients. `RemoteRuntime` generates an `X-Request-ID` for one logical request/retry sequence.

Platform consequence:

- #14 WorkerJob dispatch/idempotency remains authoritative;
- a provider request ID is adapter-private metadata;
- duplicate execution/reconciliation must be tested at the platform Worker boundary rather than delegated to SWE-ReX.

## Finding 6: environment variables are a credential-risk surface

The upstream command type can accept arbitrary environment variables. That is technically convenient but would allow broad process environment or plaintext secret projection if used directly.

Platform consequence:

- the evaluation adapter currently rejects every non-empty canonical `ExecutionRequest.environment`;
- a future concrete SWE-ReX client may receive only explicitly authorized, scoped #34 deliveries;
- synthetic credential tests must exercise enumeration, subprocess inheritance, stdout/stderr leakage and cleanup/revocation;
- secrets must not be persisted into adapter metadata or ordinary configuration.

## Finding 7: egress is backend-specific

The reviewed abstraction exposes execution backends but does not establish one uniform deny-by-default network policy model. Local, container, remote and cloud deployments can have materially different network boundaries.

Platform consequence:

- do not advertise a generic SWE-ReX egress guarantee;
- record isolation and egress evidence per backend;
- protected profiles must fail closed if their required network policy cannot be enforced;
- live tests must include Internet deny/allow, DNS, loopback, private/RFC1918, link-local/metadata and IPv6 where applicable.

## Finding 8: cancellation and cleanup are not assumed from the API shape

The runtime exposes command timeouts, shell interrupt actions and deployment/runtime close operations, but a uniform platform cancellation guarantee is not established by static inspection alone.

Platform consequence:

- the PoC models canonical cancellation through a provider-private client seam so the architecture can be tested;
- each supported concrete backend must prove in-flight cancellation, child-process cleanup and deployment cleanup;
- inability to stop a provider operation must surface as explicit residual risk/failure evidence rather than a successful canonical cancellation claim.

## Finding 9: provider metadata is untrusted diagnostic data

Backend images, platforms, runtime identities and provider errors can aid diagnostics but cannot authorize platform actions.

The PoC allows only a small metadata allowlist into canonical evidence and keeps provider deployment/runtime/session IDs under `adapter_metadata["swe_rex"]`.

## Static security conclusion

SWE-ReX can fit safely behind the platform boundary only if treated as a **backend abstraction with variable trust/isolation**, not as a security boundary in itself.

Current security classification: **`experimental_only`**.

Required live evidence before promotion:

- Linux and available Windows-path Workspace escape tests;
- Docker/Podman and relevant remote effective isolation;
- blocked/allowed egress and SSRF-relevant destinations;
- scoped synthetic credential delivery/exfiltration/redaction/revocation;
- timeout/cancellation/crash/child-process and deployment cleanup;
- remote service authentication/transport deployment profile;
- concurrent request/idempotency behavior under canonical #14 dispatch semantics.
