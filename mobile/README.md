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

Mobile pairing uses a short-lived, single-use server challenge created from an authenticated
Web/CLI session. The QR/deep-link or fallback code contains pairing material only; it is never a
durable bearer credential. The mobile app displays and validates the target Control Plane origin
before consuming the challenge.

Successful consumption returns a dedicated scoped mobile credential exactly once. Because the
pairing proof cannot be replayed, the app immediately persists that one-time result and server URL
through `expo-secure-store`, then validates it through `GET /api/v1/auth/me`. A canonical 401
clears the stored credential; a transient network failure retains it so restart recovery can retry
identity verification instead of losing a credential after consuming the challenge. The credential
is never written to ordinary application storage. Remote servers require HTTPS; loopback HTTP
remains available only for explicit local development.

Paired devices and their non-secret created/last-used/revoked state remain server-owned. Web
Settings can list and revoke one or all devices. Revocation invalidates the bearer credential on
the next canonical request; the mobile client clears its local secure-store entry when canonical
authentication reports 401.

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

The app registers `aiagentplatform://`. Ordinary deep links accept only allowlisted canonical
resource kinds and opaque, bounded canonical IDs. Query-string commands, arbitrary URLs,
provider/private identifiers and path traversal are rejected.

The one special `aiagentplatform://pair` link is a versioned authentication bootstrap envelope.
It carries only the validated HTTPS Control Plane origin, opaque pairing request ID and
short-lived one-time code. The companion can scan that envelope directly with its `expo-camera`
QR scanner or receive it as a deep link. Either path only fills the pending pairing state: the app
shows the server identity and requires explicit confirmation before the public pairing endpoint is
called.

Resource deep links select read surfaces only; they never execute an approval, verification or
Task mutation.

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
