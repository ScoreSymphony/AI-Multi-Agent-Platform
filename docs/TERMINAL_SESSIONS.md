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

Backend-private adapter metadata remains internal. The public terminal resource exposes only the namespaced `diagnostics` values that the adapter explicitly returned as safe canonical diagnostics. Provider-private handles are stored only inside the service/adapter boundary and never serialized as canonical identity.

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

Manual interactive creation is classified as high risk. A policy can require approval for creation. Approval resumption is exact-action bound: the initial request reserves a canonical `session_id`; a retry after approval must supply the same `session_id` and `approval_id`. This prevents approving one session request and replaying that approval for a different session or payload.

Input audit records keep metadata such as byte size, not the raw submitted terminal content. Output passes through the configured redaction hook before it is exposed as canonical frames or passed to a transcript evidence sink.

## HTTP Control Plane surface

Registered resource collection:

- `GET /api/v1/terminal-sessions`
- `GET /api/v1/terminal-sessions/{session_id}`

Registered commands:

- `terminal.session.create`
- `terminal.session.input`
- `terminal.session.resize`
- `terminal.session.terminate`

Commands use the existing Control Plane command envelope and idempotency mechanism. Terminal-specific code does not introduce a second northbound API authority.

Creation can include `inactivity_timeout_seconds` and `retain_transcript`. These values are part of the authorization payload and exact create-request identity, so changing them requires a newly authorized action rather than reusing an approval for a different lifecycle policy.

The terminal resource payload is validated by the same extension private-payload guard as every other Control Plane extension. Backend-private fields such as `adapter_metadata`, provider SDK objects, private API handles, raw exceptions, or backend references are never valid northbound terminal fields.

## WebSocket stream protocol

Canonical stream endpoint:

`/api/v1/terminal-sessions/{session_id}/stream`

Browser clients use the `platform.terminal.v1` WebSocket subprotocol. Reconnect can supply `after_sequence=N` to resume after the last frame already received.

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

Disconnecting the browser detaches the attachment. It does not implicitly cancel the underlying Task or Run.

## Stream identity and reconnect

Adapter frame sequence numbers are converted to stable canonical `TerminalFrame` identities. Re-reading the same adapter sequence yields the same canonical frame ID while the session service owns that session. This makes replay/reconnect deterministic and prevents the browser from treating replayed output as new output.

The ASGI contract is covered end to end by tests that open the canonical WebSocket route, receive the initial snapshot and stream frames, observe terminal status, and reconnect from a later sequence without changing canonical frame identity.

## Inactivity timeout

There is no hidden universal terminal timeout. A session can instead carry an explicit positive `inactivity_timeout_seconds` value supplied through the canonical create request.

The service tracks canonical activity and evaluates timeout policy during reconciliation. New canonical output, interactive input and resize operations refresh the activity timestamp. Once inactivity reaches the configured threshold, the service terminates the adapter through its canonical boundary, verifies that the backend reaches a terminal status, maps that status back to the `TerminalSession`, and records a `session.inactivity_timeout` activity entry. Failure of the adapter to terminate is treated as an invalid provider response rather than silently pretending the session ended.

Because timeout is represented in the canonical contract and authorization payload, adapters do not get to introduce undisclosed UI-only or provider-private timeout semantics.

## Transcript and evidence retention

Transcript retention is opt-in through `retain_transcript=true`; it is not implicit logging of every terminal stream.

When retention is enabled, `TerminalSessionService` requires an explicitly configured transcript/evidence sink. Creation fails closed with a canonical unavailable error when policy requests retention but no sink exists. The service sends only already-redacted canonical `TerminalFrame` objects to the sink.

Retention is sequence-stable and retryable. A canonical frame receives its stable frame ID before evidence delivery. A sequence is marked retained only after the sink accepts it. If the sink temporarily fails, the canonical frame remains stable but the sequence remains pending; a later exact create retry, read/stream, input flow, or lifecycle reconciliation retries that same frame rather than silently treating it as persisted or creating a duplicate evidence record.

When retention is enabled the service also captures pending adapter output on creation and during canonical read/stream and lifecycle reconciliation paths. This means retention does not depend solely on one particular browser attachment. Future remote/push adapters may drive reconciliation continuously, but they must preserve the same sequence-stable evidence semantics.

The foundation deliberately does not prescribe one database, filesystem or evidence product. Durable storage, retention duration, deletion and export policy belong to the platform persistence/policy layer behind the sink.

## Worker and node loss

Adapters can report `lost`. Reconciliation converts that backend state into canonical `SessionStatus.LOST`, sets `ended_at`, and allows a final system frame to explain the loss. Remote worker/node transport and trust integration remains owned by issue #14; the canonical session contract does not depend on that implementation.

## Frontend

The `/terminal` area provides:

- session list and workspace/status filters;
- canonical project/workspace/task/run context links;
- links to the canonical artifact and timeline surfaces;
- connection state;
- terminal output viewport;
- explicit read-only vs interactive indication;
- capability-gated input;
- reconnect using the last received sequence;
- explicit warning and confirmation for destructive termination;
- creation of the deterministic reference session for development/testing.

The frontend contract also understands the canonical timeout, retention and diagnostics fields. The frontend derives the WebSocket URL from the configured Control Plane URL and never connects to worker-private terminal ports.

## Integration rules for future adapters

A new adapter must:

1. implement `TerminalSessionAdapter`;
2. advertise exact supported session types and capabilities;
3. return only an opaque backend handle to the service;
4. never serialize that backend handle as canonical session identity;
5. expose only explicitly safe, namespaced diagnostics northbound;
6. enforce backend-side read/write isolation in addition to platform authorization;
7. report terminal completion/loss through canonical status mapping;
8. keep browser access behind the Control Plane gateway;
9. avoid leaking secrets, environment values, private PTY paths, provider-private IDs, or backend handles through diagnostics;
10. honor service-driven termination for canonical inactivity policy;
11. leave transcript persistence to the platform evidence sink rather than writing provider-private transcripts behind the service boundary.
