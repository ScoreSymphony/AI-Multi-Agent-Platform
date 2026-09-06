# Release Process

## Version policy

- `0.1.0` is the first usable single-node prototype and requires the M2 acceptance gate in #252.
- `1.0.0` is the operational baseline and requires the full conformance gate in #46.
- Patch versions contain backward-compatible fixes.
- Minor versions add backward-compatible capabilities.
- Major versions may change public or canonical contracts and require migration guidance.

Optional ecosystem and advanced-deployment capabilities do not redefine the ordinary local/self-hosted baseline unless a release explicitly claims support for the corresponding profile.

## Current status

> Status snapshot: 2026-09-06

The #252 usable single-node prototype gate has passed and is maintained by the repository's prototype-acceptance profiles. This satisfies the major functional prerequisite previously assigned to the planned `0.1.0` release.

No GitHub release or semantic-version tag has been published yet. Passing #252 therefore does **not** by itself mean that `0.1.0` has been released: publication still requires an exact release commit, the checklist below, current changelog/provenance and verification of the produced artifacts.

The `1.0.0` operational target remains gated by #46 full platform conformance. Current open implementation work may add capabilities before that point, but compatibility must not be claimed beyond the profiles that have explicit evidence.

## Release candidate checklist

- [ ] The target release's required issues and explicitly claimed capability profiles have no unresolved release blockers.
- [ ] The relevant acceptance or conformance gate passed on the exact release commit.
- [ ] Required CI, compatibility, CodeQL and dependency-review checks passed.
- [ ] Database, configuration and public-contract migrations are documented.
- [ ] Backup, restore and rollback procedures were exercised where affected.
- [ ] Supported installation and upgrade paths were tested from documented instructions.
- [ ] Upstream revisions, licenses, notices and provenance records are current.
- [ ] Security findings were triaged and no known release-blocking issue remains.
- [ ] `CHANGELOG.md` contains user-visible changes, known limitations and security notes.
- [ ] Package, container and other published artifacts use the same version and source revision.
- [ ] The compatibility/acceptance report names the exact enabled optional profiles rather than implying untested support.

## Publication

1. Select the exact source commit intended for release.
2. Run the required acceptance/conformance profile and release checks against that commit.
3. Create a release candidate without modifying the already-tested source tree.
4. Verify generated artifacts and checksums.
5. Tag the accepted commit with the semantic version.
6. Publish a GitHub release using the matching changelog section.
7. Record artifact checksums, canonical source revision and tested compatibility profiles.
8. Verify a clean installation using the published artifacts.

## Rollback

Every release with migrations or persisted-state changes must document whether rollback is supported, which backup is required and which data may become unreadable by an older version. A failed publication is withdrawn or marked clearly; an already consumed version is superseded with a new patch rather than silently replacing artifacts under the same version.
