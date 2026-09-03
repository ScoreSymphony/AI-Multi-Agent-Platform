# Terminal and execution sessions

Issue #73 introduces a platform-owned terminal/session surface for inspecting execution contexts without turning browser access into raw host shell access.

## Architectural boundary

`TerminalSession` is a canonical platform resource. The canonical ID is owned by the platform and is distinct from any executor process ID, PTY path, container handle, worker-private session identifier, or provider token.

The browser talks only to the versioned Control Plane. Backend-specific session implementations sit behind `TerminalSessionAdapter` and may be local, PTY-backed, container-backed, worker-backed, or remote in later integrations.

The bundled `ReferenceTerminalAdapter` is deliberately safety-first:

- it never opens an arbitrary host shell;
- read-only sessions emit deterministic output;
- interactive sessions echo explicitly authorized input;
- it has no PTY and therefore no resize capability;
- it provides deterministic completion/loss hooks for tests and local development.

## Canonical session model

A session records:

- canonical session ID;
- session type (`agent`, `worker`, `manual`, `debug`, `process`, `log_stream`);
- project/workspace context;
- optional task/run/worker/node references;
- mode (`read_only` or `interactive`);
- owner actor reference;
- adapter ID plus namespaced, non-secret public diagnostics;
- capability metadata;
- status (`starting`, `running`, `completed`, `failed`, `cancelled`, `lost`);
- started/ended timestamps;
- encoding and optional terminal dimensions;
- policy classifications;
- optional `inactivity_timeout_seconds`;
- policy-controlled `retain_transcript`.

`project_id` and `workspace_id` are also serialized as canonical top-level fields so the generic Control Plane filtering convention can filter terminal resources without understanding the nested session context. The structured `context` remains the authoritative relationship object.

A terminal context may carry a `task_id` without a `run_id`, but a `run_id` always requires its owning `task_id`. This keeps run-linked terminal actions resolvable through the canonical Task/Run lifecycle rather than relying on a backend-private lookup.

When a canonical `WorkspaceProvider` is configured, terminal creation verifies that the supplied `workspace_id` belongs to the requested `project_id` before the adapter is invoked. Local/reference deployments without a workspace provider retain the contract-only path; they do not pretend to have performed a workspace relationship check.

## Private adapter metadata and public diagnostics

Backend-private adapter metadata and northbound diagnostics are separate contracts.

`AdapterStartResult.metadata` remains backend-private and may contain implementation diagnostics needed by the service/adapter boundary. It is stored internally on the `TerminalSession` but is never serialized by `TerminalSession.to_json()`.

An adapter must opt in separately through `public_diagnostics` to expose a value northbound. The public terminal resource serializes only these explicitly safe `TerminalDiagnostic` records under `diagnostics`.

The reference adapter intentionally proves this boundary: it keeps a private backend-handle-kind diagnostic internally while exposing only safe values such as `arbitrary_host_shell=false` and `deterministic=true` publicly.

## Capabilities

Clients must inspect capability metadata rather than assume every session behaves like a PTY:

- `interactive_input`
- `resize`
- `reconnect`
- `terminate`
- `pty`

A read-only session cannot accept input even when the underlying adapter supports interactive sessions in general. The capability set is resolved per created session.

## Authorization and approvals

Session create, read/attach, input, resize, and terminate operations are enforced server-side through `AuthorizationGate`.

Manual interactive creation is classified as high risk. A policy can require approval for creation. Approval resumption is exact-action bound: the initial request reserves a canonical `session_id`; a retry after approval supplies the same `session_id` and `approval_id`. This prevents approving one session request and replaying that approval for a different session or payload.

The terminal UI preserves this approval-bound identity. When the Control Plane returns `require_approval`, the UI retains the reserved session reference and approval reference and can resume the exact request after the canonical approval workflow grants it. Approval-gated input and termination similarly retain the exact pending action rather than silently issuing a different privileged action.

Input audit records keep metadata such as byte size, not the raw submitted terminal content.

## Central redaction boundary

Canonical terminal frames use the platform-wide #34 free-text redaction helper by default. Terminal therefore does not silently fall back to an identity/no-op redactor in the normal service constructor.

The central redaction path provides two layers:

1. explicitly known sensitive values can still be supplied to `redact_text(...)` and are removed wherever they occur;
2. obvious environment-style assignments whose keys are classified as sensitive are redacted structurally, including common forms such as `*_TOKEN=...`, `*_API_KEY=...`, `*_PASSWORD=...`, `*_SECRET=...` and `*_PRIVATE_KEY=...`.

This covers a common terminal failure mode where a command prints environment/configuration lines without requiring the terminal subsystem to own a second secret registry. Non-sensitive assignments remain visible. Hosts may still inject a stricter replaceable redactor when they have additional deployment-specific sensitive values.

Redaction happens before adapter output becomes a canonical `TerminalFrame` and before retained transcript/evidence is delivered to its sink. Components must still avoid putting raw secret material into execution output where possible; redaction is defense in depth rather than permission to expose secrets deliberately.

## Standard Control Plane composition

Terminal is part of the exported platform composition instead of a test-only side stack.

The terminal composition extends the current Control Plane stack and composes with later plugin/authentication/search/task-management layers rather than replacing them. Authentication, Automation, Search, Task/Run lifecycle, Workspace handling, plugins and other current Control Plane behavior remain the upstream composition that Terminal augments.

A host configures the normal Control Plane with the optional terminal service:

```python
control_plane = ControlPlane(
    kernel=kernel,
    events=events,
    workspace_provider=workspace_provider,
    terminal_sessions=terminal_sessions,
)
http = ControlPlaneHTTP(control_plane)
app = ControlPlaneASGI(http)
```

When `terminal_sessions` is present, the composed `ControlPlane` registers the canonical terminal resource collection and commands, and the exported `ControlPlaneASGI` automatically brokers the terminal WebSocket route. When Terminal is absent, the same Control Plane/HTTP/ASGI classes retain ordinary non-terminal behavior.

## HTTP Control Plane surface

Registered resource collection:

- `GET /api/v1/terminal-sessions`
- `GET /api/v1/terminal-sessions/{session_id}`

Registered commands:

- `terminal.session.create`
- `terminal.session.input`
- `terminal.session.resize`
- `terminal.session.terminate`

Commands use the existing Control Plane command envelope. Terminal-specific code does not introduce a second northbound API authority.

### Idempotent session creation

`terminal.session.create` requires the normal Control Plane `Idempotency-Key`.

If a caller does not supply an explicit canonical `session_id`, the terminal command layer derives a stable canonical session ID from the actor, project and idempotency key. An exact retry with the same key therefore reaches the same session. Reusing that key for a materially different create request collides with the existing canonical session and fails rather than creating a second session.

An explicit `session_id` remains supported for exact approval resumption and controlled callers.

Creation can also include `inactivity_timeout_seconds` and `retain_transcript`. These values are part of the authorization payload and exact create-request identity, so changing them requires a newly authorized action rather than reusing an approval for a different lifecycle policy.

The terminal resource payload is validated by the same extension private-payload guard as other Control Plane extensions. Backend-private fields such as `adapter_metadata`, provider SDK objects, private API handles, raw exceptions, or backend references are never valid northbound terminal fields.

## Execution-linked termination

Detaching a browser connection never changes Task or Run ownership.

Explicit termination has two deliberately different meanings:

- manual/debug/log-stream sessions terminate only their terminal/session adapter;
- execution-owning `agent`, `worker`, or `process` sessions that carry canonical `task_id` + `run_id` require cancellation through the normal `ControlPlane.cancel_run(...)` boundary before the adapter is mutated.

The required sequence for a run-linked execution-owning session is:

1. authorize/approve the terminal termination action;
2. request canonical `run:cancel` through the Control Plane with the same trusted actor/context and idempotency key;
3. only after canonical Run cancellation succeeds, terminate/reconcile the terminal adapter.

A caller therefore needs both terminal-termination authority and the authority required to cancel the linked Run. If `run:cancel` is denied or unavailable, the terminal adapter/session remains unchanged. Returning a `403` or canonical cancellation error after already killing the underlying execution is explicitly forbidden.

If canonical Run cancellation succeeds but adapter cleanup subsequently fails, the Run remains canonical lifecycle truth and the adapter cleanup can be retried/reconciled idempotently. Terminal state never substitutes for Run state.

## WebSocket stream protocol

Canonical stream endpoint:

`/api/v1/terminal-sessions/{session_id}/stream`

Browser clients must offer the `platform.terminal.v1` WebSocket subprotocol. The gateway rejects a WebSocket upgrade that does not offer the required version instead of accepting an unspecified protocol. Reconnect can supply `after_sequence=N` to resume after the last frame already received.

### Authenticated WebSocket identity

Terminal WebSocket identity uses the same `prepare_stream_request` boundary that protects authenticated Control Plane streams. When the #36 authenticated transport is configured, the WebSocket handshake is authenticated before `TerminalSessionASGI` constructs its actor context.

Caller-supplied `X-Principal-Ref`, `X-Owner-Type`, or `X-Owner-Id` values are not an authentication mechanism. The authenticated transport strips/replaces those fields with the canonical identity established by the configured credential or browser session. An anonymous client cannot gain terminal access by spoofing actor headers; rejected authentication is translated into the corresponding WebSocket close before a terminal attachment is created.

The same composition remains usable without an authentication wrapper for local/reference stacks, where the base `prepare_stream_request` hook is intentionally a no-op. This preserves replaceability without weakening deployments that enable canonical authentication.

Server messages:

- `session.snapshot` — canonical session plus attachment metadata;
- `stream.frame` — one canonical stdout/stderr/log/system frame;
- `session.status` — refreshed canonical terminal status;
- `error` — canonicalized stream error;
- `pong` — response to a client ping.

Client messages:

- `input` — interactive input when capability and policy allow it;
- `resize` — dimensions when the session advertises resize capability;
- `terminate` — canonical termination request;
- `detach` — close the attachment without cancelling task/run ownership;
- `ping` — liveness check.

Approval-bound input may include `approval_id`; WebSocket termination may include `approval_id` and an optional idempotency key. The browser never connects to worker-private terminal ports.

Disconnecting the browser detaches the attachment. It does not implicitly cancel the underlying Task or Run.

## Stream identity and reconnect

Adapter frame sequence numbers are converted to stable canonical `TerminalFrame` identities. Re-reading the same adapter sequence yields the same canonical frame ID while the session service owns that session. This makes replay/reconnect deterministic and prevents the browser from treating replayed output as new output.

The ASGI contract is covered end to end by tests that use the exported standard Control Plane composition, open the canonical WebSocket route, receive the initial snapshot and stream frames, observe terminal status, reconnect from a later sequence without changing canonical frame identity, reject clients that do not offer the required subprotocol, and verify that authenticated WebSockets cannot establish identity from spoofed principal headers.

## Inactivity timeout

There is no hidden universal terminal timeout. A session can instead carry an explicit positive `inactivity_timeout_seconds` value supplied through the canonical create request.

The service tracks canonical activity and evaluates timeout policy during reconciliation. New canonical output, interactive input and resize operations refresh the activity timestamp. Once inactivity reaches the configured threshold, the service terminates the adapter through its canonical boundary, verifies that the backend reaches a terminal status, maps that status back to the `TerminalSession`, and records a `session.inactivity_timeout` activity entry. Failure of the adapter to terminate is treated as an invalid provider response rather than silently pretending the session ended.

The foundation intentionally makes reconciliation host-driven rather than starting hidden background tasks inside every `TerminalSessionService`. A long-running deployment is responsible for driving normal service reconciliation; future remote/push worker adapters may provide stronger continuous liveness without changing the canonical timeout contract.

## Transcript and evidence retention

Transcript retention is opt-in through `retain_transcript=true`; it is not implicit logging of every terminal stream.

When retention is enabled, `TerminalSessionService` requires an explicitly configured transcript/evidence sink. Creation fails closed with a canonical unavailable error when policy requests retention but no sink exists. The service sends only already-redacted canonical `TerminalFrame` objects to the sink.

Retention is sequence-stable and retryable. A canonical frame receives its stable frame ID before evidence delivery. A sequence is marked retained only after the sink accepts it. If the sink temporarily fails, the canonical frame remains stable but the sequence remains pending; a later exact create retry, read/stream, input flow, or lifecycle reconciliation retries that same frame rather than silently treating it as persisted or creating a duplicate evidence record.

The foundation deliberately does not prescribe one database, filesystem or evidence product. Durable storage, retention duration, deletion and export policy belong to the platform persistence/policy layer behind the sink.

## Worker and node loss

Adapters can report `lost`. Reconciliation converts that backend state into canonical `SessionStatus.LOST`, sets `ended_at`, and allows a final system frame to explain the loss. Remote worker/node transport and trust integration remains owned by issue #14; the canonical session contract does not depend on that implementation.

## Frontend

The `/terminal` area provides:

- an explicit project-scoped session list, plus optional workspace/status filters;
- canonical project/workspace/task/run context links;
- links to the canonical artifact and timeline surfaces;
- connection state;
- terminal output viewport;
- explicit read-only vs interactive indication;
- capability-gated input;
- reconnect using the last received sequence;
- explicit warning and confirmation for destructive termination;
- approval-aware exact-request resume for creation, input and termination;
- creation of the deterministic reference session for development/testing.

The list does not issue an unscoped terminal query before a project is selected. This keeps the UI aligned with project-scoped #15 policies instead of requiring the backend to weaken its authorization semantics for convenience.

When a session is created from the page, its canonical project becomes the active list scope automatically. The frontend derives the WebSocket URL from the configured Control Plane URL and never connects to worker-private terminal ports.

## Integration rules for future adapters

A new adapter must:

1. implement `TerminalSessionAdapter`;
2. advertise exact supported session types and capabilities;
3. return only an opaque backend handle to the service;
4. never serialize that backend handle as canonical session identity;
5. keep backend-private diagnostics in `metadata` and place only explicitly safe values in `public_diagnostics`;
6. enforce backend-side read/write isolation in addition to platform authorization;
7. report terminal completion/loss through canonical status mapping;
8. keep browser access behind the Control Plane gateway;
9. avoid leaking secrets, environment values, private PTY paths, provider-private IDs, or backend handles through public diagnostics/output;
10. honor service-driven termination for canonical inactivity policy;
11. leave transcript persistence to the platform evidence sink rather than writing provider-private transcripts behind the service boundary;
12. preserve canonical task/run identity so execution-owning termination can use authorized Control Plane Run lifecycle before adapter mutation.
