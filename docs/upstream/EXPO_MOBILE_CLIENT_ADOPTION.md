# Expo / React Native mobile-client adoption review

Issue: #1240  
Review date: 2026-09-19

## Candidate identity

- **Project:** Expo SDK / React Native mobile runtime
- **Canonical upstream:** https://github.com/expo/expo and https://github.com/facebook/react-native
- **Target:** Expo SDK 57.0.17, React Native 0.86.3
- **Security storage module:** expo-secure-store 57.0.4
- **QR scanning module:** expo-camera ~57.0.5
- **Integration category:** library dependency / client build runtime
- **Boundary:** optional `mobile/` northbound Control Plane client

## Functional fit

Expo/React Native supplies one Android/iOS application runtime while preserving native platform
APIs. `expo-secure-store` supplies encrypted small-value storage backed by Android Keystore and
iOS Keychain, while `expo-camera` supplies the in-app QR scanner used only for the short-lived
pairing envelope.

No Expo type becomes a canonical platform API/domain type.

## Architecture fit

The integration is entirely northbound. The app calls public `/api/v1` endpoints and has no
server package import, database access, Worker transport, model-provider path or provider-private
identity.

The mobile app is not required for single-node or distributed server operation.

## Replaceability and exit

The Control Plane remains the contract. Expo/React Native can be replaced by native Swift/Kotlin,
Flutter or another client implementation without Task/Run data migration. The only device-local
state requiring migration is the optional stored bearer credential and non-authoritative read
cache.

## License and provenance

- Expo repository: MIT.
- React Native repository: MIT.
- No upstream source is copied, vendored, forked or selectively ported.
- Packages are installed through npm and retain their package license metadata.

## Maintenance and security

- Stay on stable Expo SDK lines for the maintained client; prerelease SDKs are not the default.
- Review Expo/React Native security and release notes during explicit dependency updates.
- Run typecheck, unit tests, Expo config validation and Android/iOS export smoke checks.
- Keep camera barcode scanning restricted to QR pairing input; no camera media is persisted or uploaded.
- Remote Control Plane endpoints require TLS.
- Bearer credentials live only behind the `SecretStorage` boundary backed by
  `expo-secure-store`.
- A revoked/expired credential receives canonical 401 handling and is removed from device
  storage.
- Ordinary resource deep links cannot carry executable commands. The versioned
  `aiagentplatform://pair` envelope is the only authentication-bootstrap exception and carries
  only HTTPS origin, pairing-request ID and short-lived one-time proof; opening it never consumes
  the challenge automatically.

## Resource/deployment footprint

The integration requires Node/npm only for mobile development/build. It adds no required server
port, database, queue, GPU, paid service or hosted Expo service to the platform baseline. EAS is
not required for repository CI or server operation.

## Decision

**Approved for the optional #1240 mobile client profile.**

The dependency is justified by native secure storage and one shared Android/iOS codebase while
remaining fully behind the canonical Control Plane boundary.
