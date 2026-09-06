# Release and upstream update policy

Status: normative operator policy for issue #42.

## 1. Versioning model

The platform uses **Semantic Versioning (SemVer)** for the platform release identifier. The existing
`VersionSnapshot` remains the canonical compatibility vector and is not collapsed into the SemVer
number: domain schema, API, migration revision, plugin manifest, portable/template/backup formats,
worker/message protocols, adapters and plugin interfaces may evolve independently.

Release classes:

- **patch**: backwards-compatible fixes, maintenance and dependency refreshes that do not alter a
  public compatibility promise;
- **minor**: backwards-compatible platform capability additions. Before `1.0.0`, any intentionally
  incompatible public change must still be called out explicitly in release notes and migration
  guidance even though SemVer permits minor-version instability;
- **major**: intentionally incompatible public/platform contract changes after `1.0.0`;
- **security hotfix**: expedited patch-class release unless the only safe fix necessarily changes a
  compatibility boundary; the expedited path may shorten review latency but not remove provenance,
  license, test, migration or rollback evidence;
- **pre-release**: optional `-rc.N` candidates for canary/staging. Nightly builds are non-release
  artifacts and must never become a production pin implicitly.

No production dependency, image or upstream runtime may use a floating `latest` reference.

## 2. Release identity and notes

Every release has:

1. an explicit platform version;
2. a source commit;
3. a complete `VersionSnapshot`;
4. release notes identifying user-visible changes, compatibility impact, migrations, security
   impact and known limitations;
5. a release manifest conforming to `release-manifest.schema.json`;
6. an SBOM reference and release provenance reference;
7. immutable artifact hashes for distributed artifacts and upstream runtime artifacts used by the
   release.

The package version and `VersionSnapshot.platform_release` must match the release manifest's
`release_version`.

## 3. Provenance and SBOM

For every relevant upstream or redistributed artifact record:

- canonical source URL;
- exact tag/commit/digest/revision;
- revision kind;
- verified license scope;
- whether local modifications exist;
- patch identifiers, if any;
- build and test status;
- cryptographic artifact hashes;
- release-specific SBOM/provenance references where applicable;
- last verified timestamp.

`upstream/*.yaml` remains the source-of-truth governance inventory. A release manifest is an
immutable release snapshot of the inputs actually shipped or tested. Updating one does not silently
update the other.

## 4. Compatibility states

The compatibility matrix uses exactly these operator-visible states:

- `supported`: intentionally supported within a documented compatibility range;
- `tested`: verified at the exact recorded revision, but without a wider support-range promise;
- `experimental`: usable only with explicit operator opt-in; may change without compatibility
  guarantees;
- `deprecated`: still recognized but scheduled for removal/replacement;
- `blocked`: known incompatible or unsafe and prohibited from release approval.

An upstream being "latest" is never a compatibility state.

## 5. Discovery is advisory only

Candidate updates may be discovered manually or by automation from GitHub releases/tags, package
metadata, registries or image digests. Discovery may open or update a review item, but it may not:

- rewrite a production pin;
- merge an update PR;
- approve a release gate;
- deploy to production;
- suppress an incompatible/security finding.

The candidate becomes actionable only through a dedicated upstream-update PR.

## 6. Safe upstream update flow

Every relevant update follows:

`Discover -> Fetch/Pin -> Build -> Adapter Tests -> Contract Tests -> Eval/Regression -> Security Checks -> Compatibility Review -> Canary/Staging -> Approval -> Release -> Rollback path verified`

Detailed architecture/provenance review continues to follow `docs/UPSTREAM_UPDATE_WORKFLOW.md`.

## 7. Update PR convention

An upstream update PR must contain:

- old and proposed immutable revisions;
- source URL and release/advisory references;
- license/provenance re-verification;
- change classification (`security`, `bugfix`, `feature`, `breaking`, `irrelevant`);
- adapter/protocol/config/lifecycle/security/persistence/resource impact;
- local patch/conflict analysis;
- compatibility status and migration impact;
- exact tests/evals run and evidence links;
- canary/staging result when applicable;
- rollback method and verification evidence;
- ADR requirement/result for architecture-significant changes.

Use `.github/PULL_REQUEST_TEMPLATE/upstream-update.md`.

## 8. Release gates

`platform-release validate` fails closed unless every mandatory gate exists and is `passed`:

- `ci`
- `adapter_contract_tests`
- `eval_regression`
- `security`
- `compatibility_review`
- `migration_compatibility`
- `rollback_verified`
- `provenance_complete`
- `backup_restore_fresh`

A required gate with `failed` or `not_run` blocks release. Any `blocked` compatibility record also
blocks release. Experimental/deprecated components are surfaced as warnings and require explicit
release-note treatment.

Gate evidence is a reference to a concrete workflow run, report, artifact, review or drill; the
manifest must not merely self-assert that a gate passed.

## 9. Rollback and upgrade ownership

Issue #41's upgrade/migration layer remains the authority for persisted version state, migration
preflight and maintenance-mode activation. The release layer records and gates compatibility; it
must not bypass migration preflight or write deployment version state directly.

Before production release, the operator must know which rollback mode applies:

- code rollback without a data migration;
- reversible migration path;
- backup/restore-required rollback.

The release gate references the verified rollback procedure and a sufficiently recent backup/restore
drill.

## 10. API/UI metadata contract

Operator surfaces should expose the output shape of `release_metadata()`:

- current platform release and full version vector;
- active/reviewed upstream revisions;
- compatibility state;
- last verified timestamp;
- release readiness;
- known blockers/warnings;
- release notes, SBOM and provenance references.

This metadata is informational/control-plane state. It must not make an update executable without
normal authorization and release approval.

## 11. Normal release checklist

1. Freeze the intended source commit and release version.
2. Generate release notes and the complete version vector.
3. Freeze exact upstream inputs/digests and refresh compatibility/provenance where changed.
4. Build artifacts and generate hashes, SBOM and provenance attestation references.
5. Run all mandatory gates and attach immutable evidence references.
6. Validate the release manifest with `platform-release validate`.
7. Exercise canary/staging if the change can affect runtime behavior or persisted state.
8. Obtain explicit release approval.
9. Publish immutable release artifacts/tags.
10. Confirm rollback/recovery instructions remain reachable and correct.
