# Optional mobile client

**Public stability: Beta**  
Issue: #1240

This directory contains the optional Android/iOS client profile for the AI Multi-Agent Platform.

## Boundary

The app is a northbound client of the canonical versioned Control Plane only:

```text
Android / iOS
    |
    | HTTPS + Authorization: Bearer <credential>
    v
/api/v1
    |
canonical Control Plane
```

The app never connects directly to model gateways, Workers, databases, queues, MCP servers,
provider APIs or private lifecycle services.

## Supported platforms

The initial profile uses Expo SDK 57 / React Native 0.86 and therefore targets the Expo 57
supported mobile baseline:

- Android 7+;
- iOS 16.4+.

Web is intentionally not an initial mobile target because the repository already has the
canonical browser frontend.

## Authentication and secrets

Mobile pairs from an already-authenticated trusted Web session. The server creates a short-lived,
single-use pairing challenge that the Android app receives through the `aiagentplatform://pair`
QR deep link or through the displayed fallback request ID/code. The app shows the target Control
Plane origin before accepting it.

Successful completion issues a dedicated revocable mobile credential. The app validates that
credential through `GET /api/v1/auth/me` before it is persisted. The raw credential and server
URL are stored through `expo-secure-store`; the credential is not written to ordinary
application storage. Remote servers require HTTPS. Loopback HTTP is accepted only for explicit
local development.

Pairing challenges expire after five minutes, are single-use, and are server-side bound to the
authenticated account that created them. Pairing secrets/codes are returned only while creating
the challenge and are never included in normal device-list responses. Paired devices remain
visible from Web settings and can be renamed or revoked there. A revoked/expired device
credential is rejected by the canonical authentication boundary and removed locally after a
canonical 401.

The mobile client does not emulate the browser HttpOnly-cookie/CSRF session model. Authorization,
credential scope, revocation and audit remain canonical server responsibilities.

## Offline semantics

The first slice deliberately has no queued mutations.

- successful reads are cached in memory;
- if a later read fails because the server/network is unreachable, the last successful value
  may be shown with a visible stale/read-only banner;
- cached state is never promoted to canonical state;
- mutations fail closed while the client is in the offline/degraded state;
- a fresh successful read clears the degraded state;
- cache is process-local and is not durable across app restarts.

Queued offline mutation support is intentionally deferred until canonical idempotency,
conflict and reconciliation semantics are explicitly designed.

## Notifications

The app reads and updates canonical Notification resources. It does not create a second
notification database or lifecycle. Push registration/delivery is not part of the initial
slice.

A future push adapter may carry only a canonical Notification/resource reference and wake the
app to re-read Control Plane state; receipt of an OS push must never be interpreted as the
authoritative Notification state.

## Deep links

The app registers `aiagentplatform://`. Only allowlisted canonical resource kinds and opaque,
bounded canonical IDs are accepted. Query-string commands, arbitrary URLs, provider/private
identifiers and path traversal are rejected.

Resource deep links select the relevant read surface; they never execute an approval,
verification or Task mutation. The separately versioned `aiagentplatform://pair` deep link may
carry only the one-time pairing origin/request/proof tuple and requires an explicit server review
before a device credential is accepted.

## Initial workflows

- Control Plane health/status;
- Task/Run monitoring;
- Result/Artifact references;
- lightweight Task submission;
- Approval approve/deny with exact `requested_action_digest` binding;
- explicit human takeover for a waiting Task through the canonical
  `conversation-messages/{message_id}:resume-task` route and an already persisted user message;
- Verification accept/reject/request-changes;
- canonical Notifications and mark-read;
- Search/history;
- Agent and Worker status inspection.

Human takeover does not create mobile-owned Task state. The client requires both the canonical
Conversation message ID and waiting Task ID and forwards them to the existing Conversation input
resume route. The server re-authorizes the message/conversation and `task:resume`, records only
canonical input provenance and performs the lifecycle transition through the kernel.

## Development

```bash
cd mobile
npm install
npm run typecheck
npm test
npm run check:expo
npm run smoke:android
npm run smoke:ios
```

The package is optional. Installing or starting the server does not install or import this
client.
