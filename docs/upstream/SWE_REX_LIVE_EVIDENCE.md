# SWE-ReX live evidence log (#861)

Final evaluation classification: **`experimental_only`**.

This log records only behavior observed against the exact evaluated revision `5c995c365dfb1fd5bc56fda688be5d8538f9931f` (package metadata `1.4.0`). Provider guarantees remain backend-specific; no result for one backend is generalized to another.

## Evidence policy

Accepted evidence records the exact revision, host/backend, fixture, canonical mapping where exercised, cleanup/security observations, and a raw workflow/artifact reference. Upstream documentation or API shape alone is not counted as a live pass.

## Linux LocalDeployment

GitHub-hosted Ubuntu 24.04 / Python 3.12 evidence demonstrated:

- one-shot execution with exit code `0`;
- separate stdout/stderr markers;
- provider file write/read round-trip;
- timeout signaling with `CommandTimeoutError`;
- direct synthetic environment projection is visible to the child process;
- a sibling file outside the selected temporary Workspace is readable;
- the canonical `SwerexExecutor -> LocalRuntime` bridge preserves canonical IDs, maps controlled failure/timeout, collects an Artifact into the canonical Workspace, and dispatches nothing for blocked environment or Workspace-traversal requests.

Interpretation: **functional trusted-host execution, not a sandbox**. The provider does not enforce the canonical Workspace boundary.

The reviewed `LocalRuntime.execute()` is `async` in signature but uses synchronous `subprocess.run(...)`. The provider call therefore blocks the event loop while the child runs; canonical in-flight cancellation cannot be treated as a reliable LocalRuntime kill guarantee. Child-process cleanup must not be inferred from parent timeout signaling.

Representative raw evidence: workflow runs `34654403144` and `34658070981`, artifact `swe-rex-local-Linux`.

## Native Windows LocalDeployment

On GitHub-hosted Windows Server 2025 / Python 3.12 the exact revision installs, but importing `LocalDeployment` fails before execution:

```text
AttributeError: module 'pexpect' has no attribute 'spawn'
```

Interpretation: native Windows LocalDeployment is **not demonstrated/supported** at the evaluated revision. A Windows remote client is a separate profile and is not inferred from Linux evidence.

Representative raw evidence: workflow run `34658070981`, artifact `swe-rex-local-Windows`.

## Default DockerDeployment

Workflow run `34658070981`, artifact `swe-rex-docker`, exercised a server image built from the exact reviewed revision with `pull="never"`.

Observed raw runtime evidence:

| Observation | Result |
| --- | --- |
| Core command semantics | pass |
| Artifact round-trip | pass |
| Synthetic env visible in the command | yes |
| Synthetic env persisted to the next command | no |
| Read outside intended provider Workspace (`/etc/hostname`) | **succeeded** |
| Internet request to `http://example.com` | **allowed**, HTTP `200` |
| Runtime port exposure | `0.0.0.0:<port>` and `[::]:<port>` |
| Parent timeout | `CommandTimeoutError` |
| Spawned child survives parent timeout | **yes** |
| Startup | ~`0.78s` |
| Stop | ~`0.22s` |
| Test image size | `176,887,972` bytes (~177 MB) |
| One resource sample | `38.26MiB / 15.61GiB`, `0.13%` CPU |

Interpretation:

- the pinned Docker path is functionally useful;
- default DockerDeployment is **not** evidence of deny-by-default networking or Workspace containment;
- parent timeout does not establish process-tree cleanup;
- the default published control port requires an external exposure policy before production use.

The same run exercised the canonical Docker bridge. Artifact collection, controlled failure, timeout mapping, canonical guards and canonical IDs behaved as expected, but the `echo` command failed because the evaluation fixture uploaded an empty host Workspace to a provider path that did not yet exist. This was a fixture defect, not a provider isolation result. The branch now explicitly creates `/tmp/issue861-canonical` before upload; that corrected bridge remains subject to the final current-head CI/live run before merge readiness.

## Deny-egress Docker attempts

Two negative profiles were exercised:

1. `DockerDeployment(..., docker_args=["--network=none"])`;
2. Docker `--internal` network.

Both remove/break the host-to-runtime HTTP path that SWE-ReX itself needs to reach the container server. The internal-network experiment reached startup timeout rather than yielding a working executor with denied Internet egress.

Interpretation: #861 did **not** demonstrate a functional deny-egress profile by applying Docker network flags directly to SWE-ReX `DockerDeployment`. Protected egress requires a separate network/proxy design outside the provider abstraction and fresh evidence. The known-negative internal-network experiment is therefore retained as historical evidence, not repeated in the normal evaluation workflow.

## Remote loopback

Workflow run `34658070981`, artifact `swe-rex-remote-loopback`, started the exact `swerex-remote` server on `127.0.0.1`.

Observed:

- correct API key accepted;
- wrong API key rejected (401 behavior); the wrong client's authenticated `/close` attempt is also rejected;
- command stdout/stderr and exit semantics pass;
- provider file round-trip passes;
- four concurrent commands all pass;
- a path outside the selected Workspace is readable;
- server process cleanup succeeds;
- raw auth token is not recorded in JSON evidence;
- transport scheme is `http`;
- startup was ~`0.63s`.

Interpretation: the remote API is functionally viable but is not a canonical Workspace or transport-security boundary. TLS/private-network protection and service identity remain external platform/deployment responsibilities.

## Packaging and provenance evidence

The reviewed base package imports `aiohttp` through `swerex.runtime.remote` but does not declare it in the base dependency set. Docker/remote evaluation jobs therefore install `aiohttp>=3.11,<4` explicitly while the platform baseline remains SWE-ReX-free.

Host-side SWE-ReX pinning is not sufficient to establish Docker server provenance because DockerDeployment can start/install a separate server. Accepted Docker evidence therefore builds the server image from the exact evaluated revision and uses `pull="never"`; an unpinned fallback is not accepted as #861 evidence.

## Final matrix

| Scenario | Evidence | #861 result |
| --- | --- | --- |
| Canonical IDs/provider-ID namespacing | Contract suite + live Local bridge | pass |
| Canonical Workspace traversal guard | Contract suite + live bridge | pass at platform boundary |
| Provider filesystem containment | Local/Remote/Docker outside reads | **not provided by SWE-ReX abstraction** |
| Artifact collection | Contract suite + Local bridge; Docker raw round-trip | pass; Docker corrected bridge requires final current-head run |
| Timeout mapping | Contract suite + Local/Docker evidence | pass for signaling |
| In-flight cancellation / process-tree cleanup | adapter seam works; Local blocks in `subprocess.run`; Docker child survives timeout | **not a uniform provider guarantee** |
| Direct secret/environment projection | adapter rejects; raw canary is visible if directly supplied | fail-closed platform policy retained |
| Environment persistence | Docker synthetic canary follow-up | not persisted in exercised Docker command path |
| Default Docker Internet egress | live request | **allowed** |
| Functional deny-egress Docker profile | `network=none` + internal network | **not demonstrated** |
| Native Windows Local | live Windows runner | **unavailable at evaluated revision** |
| Remote auth | loopback correct/wrong key | pass |
| Remote transport | loopback | HTTP; external protection required |
| Remote concurrency | 4 concurrent commands | pass |
| Reference Executor independent of SWE-ReX | dependency-free adapter + normal platform CI path | design satisfied; final current-head CI is merge gate |

## Decision

The evidence is sufficient to decide #861 without pretending that all possible backend/security cases have been solved. SWE-ReX belongs, at most, behind the platform as an **experimental runtime/backend abstraction**. It does not qualify as a generic sandbox, native Windows-local provider, deny-egress provider, secret boundary, Workspace authority, or lifecycle/idempotency authority.

A future promotion attempt must define one precise supported profile and rerun security/contract/live evidence against the then-current upstream revision. Unknown/unexercised cloud backends remain unknown rather than inheriting these results.
