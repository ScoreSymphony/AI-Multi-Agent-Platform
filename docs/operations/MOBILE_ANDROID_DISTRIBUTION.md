# Android APK distribution

Mobile baseline: optional mobile client profile
Distribution status: signed `mobile-v0.1.0` and `mobile-v0.1.1` artifacts have been published
Terminal physical-device/update acceptance: separate evidence gate; not established by release publication

The Android companion is distributed directly through GitHub Releases. Published signed APKs prove
the distribution/signing path; they do not by themselves establish complete data-preserving
N -> N+1 update support. That support remains acceptance-gated on retained real-device update
evidence and is not part of the required platform V1 baseline. Google Play, an app-store
account, Expo EAS and any other paid build/distribution service are not required.

## Release identity

The maintained application identity is:

- Android package: `org.scoresymphony.aimultiagentplatform`
- semantic app version: `mobile/package.json.version == mobile/app.json.expo.version`
- Android build identity: `mobile/app.json.expo.android.versionCode`
- minimum supported Android: Android 7 (API 24); the release workflow verifies the built APK's `sdkVersion` before publication

Every release must increment `versionCode` even when the human-facing semantic version changes in
a way that is not numerically comparable. Never reuse a published `versionCode`.

The first official `mobile-v*` GitHub Release establishes the signing-certificate SHA-256 in its
`mobile-release.json`. Every later mobile release downloads that manifest and fails closed if the
package ID changes, the `versionCode` does not increase, or the signing certificate fingerprint
changes.

## Build model

The source tree remains Expo/React Native. Pull requests run only the fast mobile release
validation contract: locked dependency installation, release-configuration validation,
typechecking, tests and Expo configuration checks. They do not generate the native Android
project, invoke Gradle, assemble an APK, or perform APK signing.

The native Android project is regenerated with `expo prebuild` only for an explicit
`workflow_dispatch` run. That release run removes Expo's generated debug-key signing from the
production release build, assembles the APK with Gradle, and exercises signing and
signature-verification mechanics with a throwaway CI keystore before production publication. A
deliberately different throwaway signer must produce a different certificate fingerprint.
Production signing secrets are not available to pull-request jobs.

Only the `publish` job of `.github/workflows/mobile-android-release.yml` can access the official
signing identity. It runs only for an explicit `workflow_dispatch` publication request on
`main`, behind the `mobile-production` GitHub Environment, after build and test-signing jobs
succeed.

The publish job creates:

- `AI-Multi-Agent-Mobile-vX.Y.Z.apk`
- `SHA256SUMS.txt`
- `npm-dependency-tree.json`, the exact npm dependency resolution used for the APK build and
  published as the release manifest's `resolved_set`;
- `mobile-release.json` with source commit, package/version/versionCode, APK digest, the
  resolved dependency-set digest, signer fingerprint and workflow provenance.

It verifies the APK with `apksigner` before `gh release create` publishes any asset.

## Production signing-key setup

Generate the production key once on a trusted offline/admin workstation. One suitable baseline is:

```bash
keytool -genkeypair -v \
  -keystore ai-multi-agent-mobile-release.p12 \
  -storetype PKCS12 \
  -alias ai-multi-agent-mobile \
  -keyalg RSA \
  -keysize 4096 \
  -validity 10000 \
  -dname "CN=AI Multi-Agent Mobile,O=ScoreSymphony"
```

Choose strong store/key passwords without putting them into repository files or shell scripts.
Record the initial certificate fingerprint independently:

```bash
keytool -list -v \
  -keystore ai-multi-agent-mobile-release.p12 \
  -alias ai-multi-agent-mobile
```

Configure a GitHub Environment named `mobile-production`; production repositories should require
a reviewer for that Environment. Add these Environment secrets:

- `MOBILE_ANDROID_KEYSTORE_B64`: base64 of the PKCS12/JKS file;
- `MOBILE_ANDROID_KEYSTORE_PASSWORD`;
- `MOBILE_ANDROID_KEY_ALIAS`;
- `MOBILE_ANDROID_KEY_PASSWORD`.

The workflow masks password values, decodes the keystore only into the runner's temporary
directory, never uploads it, and never exposes these secrets to pull-request jobs.

The key owner is the repository release owner/maintainers, not CI. Keep at least two encrypted,
offline backups in separate locations plus the password/recovery information under equivalent
access controls. Retain periodic restore-access evidence and verify recovery material before any signer recovery or planned rotation; do not assume an untested backup is usable.

### Rotation and loss

For the Android 7 support baseline, continuity of the original signing identity is the conservative
compatibility contract for in-place updates. Do not rotate the key as a routine maintenance action.
A lost or intentionally replaced key can break the existing install lineage and may require users
to uninstall/reinstall, which deletes ordinary app data. Treat rotation as a security incident or
planned compatibility migration with separate acceptance evidence.

## Publishing

1. Update `mobile/package.json.version` and `mobile/app.json.expo.version` to the same semantic
   version.
2. Increase `mobile/app.json.expo.android.versionCode`.
3. Merge the change to `main` only after normal repository checks and the lightweight mobile
   Android pull-request validation are green. A full APK build is not required for every pull
   request.
4. In GitHub Actions, dispatch **Mobile Android release** on `main`, enter the exact configured
   version, and set `publish=true`.
5. Approve the protected `mobile-production` Environment gate.
6. Publication occurs only after tests, native build, APK metadata checks, production signing,
   signature verification, checksum generation and previous-release lineage checks all pass.
   GitHub publication is staged as a draft first; if asset upload or publication is interrupted,
   a later run may resume only the matching unpublished draft for the exact same source commit.

Missing signing secrets, a version mismatch, signature verification failure, package drift,
non-monotonic `versionCode`, a changed signing identity, an APK minimum SDK other than API 24,
a pre-existing published release, a standalone conflicting tag, or a draft tied to a different
source commit prevents publication. A matching unpublished draft for the same source commit is
the only retryable pre-existing release state.

## Direct install from GitHub Releases

On Android:

1. Open the repository's GitHub Releases page.
2. Open the desired `mobile-vX.Y.Z` release.
3. Download `AI-Multi-Agent-Mobile-vX.Y.Z.apk`, `npm-dependency-tree.json` and
   `SHA256SUMS.txt`.
4. Allow installation from the browser/file-manager source when Android asks.
5. Open the APK and confirm the install.

No Google Play account is involved. Android may show normal sideloading/security prompts.

## Verify integrity and signature

On a workstation with standard checksum tools:

```bash
sha256sum -c SHA256SUMS.txt
```

With Android SDK Build Tools installed:

```bash
apksigner verify --verbose --print-certs AI-Multi-Agent-Mobile-vX.Y.Z.apk
```

Compare the reported certificate SHA-256 with `mobile-release.json` and, for N+1 releases, with
the prior official mobile release. The manifest also binds the SHA-256 of the published
`npm-dependency-tree.json` as the immutable resolved dependency set used by that build. A
checksum, dependency-set or certificate mismatch means the APK must not be installed.

## In-place update behavior

A normal in-place update requires the same Android package ID, the same accepted signing identity
and a greater `versionCode`. Install the newer APK over the existing application:

```bash
adb install -r AI-Multi-Agent-Mobile-vNEXT.apk
```

Android retains application data for a normal compatible update. That includes the app's normal
storage and, subject to Android/keystore device policy, secure pairing/session material stored by
`expo-secure-store`. Uninstalling the app, clearing app data, device-policy/key invalidation or
using an incompatible signer are different operations and can remove or invalidate stored
credentials.

A wrong-signature replacement should fail rather than silently replace the official application;
with ADB it is normally observable as an update-incompatible installation failure.

## Debug versus official release

Development/Expo debug builds and PR test-signed APKs are not official releases and must never be
presented as update-compatible production artifacts. Only assets published by the protected
production job are official.

The APK remains a client only:

```text
GitHub Release
  -> signed APK
  -> Android device
  -> HTTPS /api/v1
  -> canonical Control Plane
```

It contains no platform server runtime, model runtime, provider credentials, production signing
key or second Task/Run lifecycle authority.

## Acceptance evidence

Pull-request CI proves the mobile source/configuration contract without spending time on APK
assembly. An explicit mobile release workflow run proves clean native release assembly, APK
metadata validation, ephemeral signing, signature verification and wrong-signer distinction
without exposing production material. An official publication additionally proves protected-key
signing, signer continuity after the first release, checksum generation and expected GitHub Release
assets.

Clean install/launch against a remote Control Plane and N -> N+1 data-preserving update require
retained real-device evidence. The terminal physical Android/VPS journey, including a
wrong-signature update rejection on a real device, must be validated separately so the distribution
workflow is not confused with product-level device acceptance.
