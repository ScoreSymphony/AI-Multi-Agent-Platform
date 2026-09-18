# Optional mobile client

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

Mobile uses an already-issued bearer credential. Activation validates the credential through
`GET /api/v1/auth/me` before it is persisted.

The raw credential and server URL are stored through `expo-secure-store`; the credential is
not written to ordinary application storage. Remote servers require HTTPS. Loopback HTTP is
accepted only for explicit local development.

This first slice does not create/recover/reveal credentials and does not emulate the browser
HttpOnly-cookie/CSRF session model. Credential issuance and revocation remain canonical server
operations exposed through existing trusted Web/CLI/operator flows.

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

A deep link selects the relevant read surface; it never executes an approval, verification or
Task mutation.

## Initial workflows

- Control Plane health/status;
- Task/Run monitoring;
- Result/Artifact references;
- lightweight Task submission;
- Approval approve/deny with exact `requested_action_digest` binding;
- Verification accept/reject/request-changes;
- canonical Notifications and mark-read;
- Search/history;
- Agent and Worker status inspection.

There is currently no separate public human-takeover command in the audited `main` Control
Plane contract. The mobile client does not invent one; any future takeover workflow must first
land as a canonical northbound contract.

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
