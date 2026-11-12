# Expo / React Native mobile-client adoption review

Issue: #1240  
Review date: 2026-11-12

## Candidate identity

- **Project:** Expo SDK / React Native mobile runtime
- **Canonical upstream:** https://github.com/expo/expo and https://github.com/facebook/react-native
- **Target:** Expo SDK 58.0.22, React Native 0.88.1, React 19.3.0
- **Security storage module:** expo-secure-store 58.0.3
- **Camera module:** expo-camera 58.0.14
- **Integration category:** library dependency / client build runtime
- **Boundary:** optional `mobile/` northbound Control Plane client

## Functional fit

Expo/React Native supplies one Android/iOS application runtime while preserving native platform
APIs. `expo-secure-store` supplies encrypted small-value storage backed by Android Keystore and
iOS Keychain, which fits the mobile bearer-credential requirement.

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

- Expo repository: MIT; reviewed against release tag `expo@58.0.22` (`004bfc41cfd82784da23450101015717c5da74e6`).
- React Native repository: MIT; version `0.88.1` is paired with Expo SDK 58.
- No upstream source is copied, vendored, forked or selectively ported.
- Packages are installed through npm and retain their package license metadata.

## Maintenance and security

- Stay on stable Expo SDK lines for the maintained client; prerelease SDKs are not the default.
- Review Expo/React Native security and release notes during explicit dependency updates.
- Run typecheck, unit tests, Expo config validation and Android/iOS export smoke checks.
- Expo SDK 58 uses Android 7+ and iOS 16.4+ as the minimum runtime OS versions; Android compile SDK 37, target SDK 36, and iOS build toolchain Xcode 26.4+.
- The React Native 0.88 strict TypeScript API is the default. The mobile deep-link callback signature was adapted in PR #1514.
- Keep the transitive `node-forge` resolved at patched `1.4.3` through the npm override; do not relax dependency-review checks.
- Remote Control Plane endpoints require TLS.
- Bearer credentials live only behind the `SecretStorage` boundary backed by
  `expo-secure-store`.
- A revoked/expired credential receives canonical 401 handling and is removed from device
  storage.
- Deep links cannot carry executable commands.

## Resource/deployment footprint

The integration requires Node/npm only for mobile development/build. It adds no required server
port, database, queue, GPU, paid service or hosted Expo service to the platform baseline. EAS is
not required for repository CI or server operation.

## Decision

**Approved for the optional #1240 mobile client profile.**

The dependency is justified by native secure storage and one shared Android/iOS codebase while
remaining fully behind the canonical Control Plane boundary.

## Maintained SDK 58 update (PR #1514)

The initial #1240 review approved SDK 57. This maintenance review changes the pinned runtime to Expo SDK 58, React Native 0.88.1, React 19.3.0, Secure Store 58.0.3, and Camera 58.0.14. It preserves the canonical Control Plane boundary, secure-storage requirements, Android 7+/iOS 16.4+ minimums and replaceability decision. The package-lock.json was regenerated with npm, and the merged integration validation must include mobile typecheck, tests, configuration, Android/iOS export, release workflow, dependency review and overall CI. No physical-device validation or signed APK release is inferred from these checks.
