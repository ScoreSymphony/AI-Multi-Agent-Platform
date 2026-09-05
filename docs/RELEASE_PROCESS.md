# Release Process

## Version policy

- `0.1.0` is the first usable single-node prototype and requires the M2 acceptance gate in #252.
- `1.0.0` is the operational baseline and requires the M3 conformance gate in #46.
- Patch versions contain backward-compatible fixes.
- Minor versions add backward-compatible capabilities.
- Major versions may change public or canonical contracts and require migration guidance.

M4 capabilities are optional extensions. Their availability does not redefine the M2 or M3 baseline.

## Release candidate checklist

- [ ] The target milestone has no unresolved required issues.
- [ ] The relevant acceptance or conformance gate passed on the release commit.
- [ ] Required CI, compatibility, CodeQL and dependency-review checks passed.
- [ ] Database, configuration and public-contract migrations are documented.
- [ ] Backup, restore and rollback procedures were exercised where affected.
- [ ] Supported installation and upgrade paths were tested from documented instructions.
- [ ] Upstream revisions, licenses, notices and provenance records are current.
- [ ] Security findings were triaged and no known release-blocking issue remains.
- [ ] `CHANGELOG.md` contains user-visible changes, known limitations and security notes.
- [ ] Package, container and other published artifacts use the same version and source revision.

## Publication

1. Create a release candidate from the exact commit that passed the release gate.
2. Verify generated artifacts without modifying the source tree.
3. Tag the accepted commit with the semantic version.
4. Publish a GitHub release using the matching changelog section.
5. Record artifact checksums and the canonical source revision.
6. Verify a clean installation using the published artifacts.

## Rollback

Every release with migrations or persisted-state changes must document whether rollback is supported, which backup is required and which data may become unreadable by an older version. A failed publication is withdrawn or marked clearly; an already consumed version is superseded with a new patch rather than silently replacing artifacts under the same version.
