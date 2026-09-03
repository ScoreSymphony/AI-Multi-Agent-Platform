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
- adapter ID and non-secret adapter metadata;
- capability metadata;
- status (`starting`, `running`, `completed`, `failed`, `cancelled`, `lost`);
- started/ended timestamps;
- encoding and optional terminal dimensions;
- policy classifications.

Provider-private handles are stored only inside the service/adapter boundary and never serialized as canonical identity.

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

Input audit records keep metadata such as byte size, not the raw submitted terminal content. Output passes through the configured redaction hook before it is exposed as canonical frames.

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

## Worker and node loss

Adapters can report `lost`. Reconciliation converts that backend state into canonical `SessionStatus.LOST`, sets `ended_at`, and allows a final system frame to explain the loss. Remote worker/node transport and trust integration remains owned by issue #14; the canonical session contract does not depend on that implementation.

## Frontend

The `/terminal` area provides:

- session list and workspace/status filters;
- canonical project/workspace/task/run context links;
- connection state;
- terminal output viewport;
- explicit read-only vs interactive indication;
- capability-gated input;
- reconnect using the last received sequence;
- explicit warning and confirmation for destructive termination;
- creation of the deterministic reference session for development/testing.

The frontend derives the WebSocket URL from the configured Control Plane URL and never connects to worker-private terminal ports.

## Current retention and timeout scope

The foundation keeps replay state in the in-process terminal service so reconnect can preserve canonical frame identity. It does not yet define durable transcript persistence or a universal inactivity timeout policy. Durable evidence retention must be added through the platform persistence/policy layer rather than by silently logging every terminal transcript. Adapter- or worker-driven completion/loss is already reflected through canonical reconciliation.

This limitation is intentional and should remain explicit until retention and inactivity policy are represented by canonical configuration rather than hidden adapter defaults.

## Integration rules for future adapters

A new adapter must:

1. implement `TerminalSessionAdapter`;
2. advertise exact supported session types and capabilities;
3. return only an opaque backend handle to the service;
4. never serialize that backend handle as canonical session identity;
5. enforce backend-side read/write isolation in addition to platform authorization;
6. report terminal completion/loss through canonical status mapping;
7. keep browser access behind the Control Plane gateway;
8. avoid leaking secrets, environment values, private PTY paths, or provider-private IDs through adapter metadata.
