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

## Android release identity

Official direct-install Android builds use the stable application ID
`org.scoresymphony.aimultiagentplatform`. The semantic app version is shared by
`package.json` and Expo `version`; Android additionally uses a monotonically increasing
integer `versionCode`.

`npm run validate:release` fails if those release identifiers drift. The generated native
Android tree is intentionally not committed: `npm run build:android:release` regenerates it
with Expo prebuild and assembles an unsigned production APK. Official signing happens only in
the protected GitHub release workflow; local/debug workflows do not have the production key.

Direct APK distribution, signing-key custody, checksum/signature verification and update
semantics are documented in
`docs/operations/MOBILE_ANDROID_DISTRIBUTION.md`.

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
npm run validate:release
npm run smoke:android
npm run smoke:ios
```

To reproduce the native Android release assembly locally, install the Android SDK/JDK 17 and run:

```bash
npm run build:android:release
```

That command deliberately leaves the release APK unsigned. Never copy the production keystore
into the repository or generated `mobile/android/` tree.

The package is optional. Installing or starting the server does not install or import this
client.
