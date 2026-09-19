# ADR 0016: Use an optional Expo/React Native mobile Control Plane client

- **Status:** Accepted
- **Date:** 2026-09-19
- **Issue:** #1240

## Context

The platform needs mobile access for monitoring and human-attention workflows without creating
mobile-specific Task/Run truth, persistence or authorization. The existing browser and CLI
already establish the architectural rule that external clients consume the versioned Control
Plane.

A mobile client also needs OS-native secure credential storage, explicit offline semantics and
safe deep-link behavior.

## Decision

Use React Native through Expo SDK 57 for the initial optional Android/iOS client.

The initial supported platform baseline follows Expo SDK 57: Android 7+ and iOS 16.4+.

The mobile client:

1. consumes only public `/api/v1` Control Plane resources/commands;
2. uses an already-issued bearer credential rather than duplicating the browser cookie/CSRF
   session mechanism;
3. validates credentials through `/api/v1/auth/me`;
4. persists credential material through `expo-secure-store` (Android Keystore-backed encrypted
   storage / iOS Keychain);
5. requires HTTPS for non-loopback servers;
6. treats cached read state as an explicitly stale projection only;
7. does not queue mutations offline in the first slice;
8. uses allowlisted deep links that identify canonical resources but never encode commands;
9. reads canonical Notifications; OS push delivery is deferred and may never become another
   notification authority;
10. performs human takeover only through the existing Conversation-input resume route for an
    already persisted user message and waiting canonical Task;
11. remains an optional repository client and never becomes a server/runtime dependency.

The first slice does not expose credential issuance, secret management, administrator-only
configuration, local model hosting or every Web administration surface.

## Consequences

### Positive

- Android and iOS share one TypeScript/React Native implementation.
- The existing API-first replacement boundary is preserved.
- Session secrets use OS secure-storage mechanisms.
- Offline behavior is deterministic and cannot silently manufacture successful mutations.
- Mobile-specific release cadence can evolve independently of the server lifecycle.

### Costs and risks

- Expo/React Native becomes an additional client toolchain that requires periodic compatibility
  and security review.
- Mobile UX cannot assume browser cookie behavior; bearer credential lifecycle must remain
  explicit.
- Shared TypeScript wire contracts are desirable, but copying frontend-private UI abstractions
  into mobile would create coupling. Cross-client generated-contract sharing should be done only
  through a platform-owned generated package or generator output.
- Native app-store signing/distribution remains deployment work outside this first repository
  slice.

## Alternatives considered

### Native Swift + Kotlin

Rejected for the initial slice because it duplicates client logic and contract maintenance across
two codebases without providing a current requirement that justifies that cost.

### Progressive Web App only

Rejected as the sole mobile profile because OS secure credential storage and native lifecycle
integration are explicit requirements. The existing Web client remains useful on mobile browsers
but is not a substitute for this optional native profile.

### Flutter

Technically viable, but it would introduce a second language/runtime ecosystem while the existing
northbound client work is TypeScript-heavy. No canonical platform contract depends on React
Native, so this decision remains replaceable.

## Affected contracts

- `control-plane-v1`
- #36 authentication/session semantics
- #15/#214 approval semantics
- #75 notifications
- #86 verification/review

No canonical Task/Run/Event/Notification lifecycle contract changes.
