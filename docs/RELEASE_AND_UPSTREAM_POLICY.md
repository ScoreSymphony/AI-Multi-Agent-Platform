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
2. a full 40- or 64-character source commit identifier;
3. a complete `VersionSnapshot`;
4. release notes identifying user-visible changes, compatibility impact, migrations, security
   impact and known limitations;
5. a release manifest conforming to release-manifest schema v2;
6. one or more immutable dependency sets representing exact lockfiles or resolved dependency sets;
7. an SBOM reference and release provenance reference;
8. immutable `sha256:` or `sha512:` hashes for every platform/release artifact that is actually
   published or distributed.

The package version and `VersionSnapshot.platform_release` must match the release manifest's
`release_version`.

## 3. Dependency, provenance and SBOM binding

Each dependency set records:

- a stable logical name;
- its ecosystem;
- whether the evidence is a lockfile or exact resolved set;
- a scheme-qualified source/reference for the frozen input;
- a cryptographic `sha256:` or `sha512:` digest of that exact set.

A package manifest containing version ranges is not by itself an exact dependency set. If an
ecosystem has no committed lockfile, the release process must produce an exact resolved-set artifact
and bind its digest into the release manifest.

For every relevant upstream or redistributed artifact record:

- canonical source URL;
- exact tag/commit/digest/revision;
- revision kind;
- verified license scope;
- whether local modifications exist;
- patch identifiers, if any;
- build and test status;
- cryptographic artifact hashes when that upstream input is itself built, mirrored, packaged or
  distributed as an artifact by the platform release;
- release-specific SBOM/provenance references where applicable;
- last verified timestamp.

A source-only upstream integration does not need a fictional artifact hash: its reproducibility is
anchored by the immutable source revision plus provenance. If that upstream is later built or
redistributed as part of a release, the resulting artifact must be hashed and recorded.

`upstream/*.yaml` remains the source-of-truth governance inventory. A release manifest is an
immutable release snapshot of the inputs actually shipped or tested. Updating one does not silently
update the other.

## 4. Compatibility matrix and states

The compatibility inventory is a tested combination, not only a list of upstream pins. Schema v2
binds the complete canonical `VersionSnapshot` to the reviewed upstream combination, including:

- platform release;
- domain schema and API;
- migration revision;
- plugin manifest;
- portable/template/backup formats;
- worker and message protocols;
- deployment-specific adapter versions where claimed;
- deployment-specific plugin-interface versions where claimed;
- exact reviewed Hermes, Forge, LiteLLM or other upstream revisions.

The packaged baseline may contain empty adapter/plugin-interface maps when it makes no
installation-specific claim; a deployment-specific compatibility claim must populate those maps
instead of implying support merely because a component starts.

The matrix uses exactly these operator-visible states:

- `supported`: intentionally supported within a documented compatibility range;
- `tested`: verified at the exact recorded revision, but without a wider support-range promise;
- `experimental`: usable only with explicit operator opt-in; may change without compatibility
  guarantees;
- `deprecated`: still recognized but scheduled for removal/replacement;
- `blocked`: known incompatible or unsafe and prohibited from release approval.

An upstream being "latest" is never a compatibility state. A release compatibility record must
refer to the exact same upstream revision recorded in that release's provenance.

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

## 8. Release gates and evidence

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

Gate evidence is structured rather than a free-form success claim. Every item declares an evidence
kind (`workflow_run`, `report`, `artifact`, `review`, `drill` or `attestation`) and a
scheme-qualified reference. Artifact/report/attestation evidence also carries a cryptographic
digest. Gates whose result is defined by the exact candidate source tree are commit-bound and must
name the release manifest's exact source commit: CI, adapter-contract tests, eval/regression,
security, compatibility review, migration compatibility and provenance completion. Rollback and
backup/restore drills may refer to separately established operational evidence.

The validator checks these bindings locally and deterministically; it does not require GitHub or any
other hosted provider to be reachable during release validation.

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
- exact dependency-set provenance;
- active/reviewed upstream revisions;
- compatibility state;
- last verified timestamp;
- release readiness;
- known blockers/warnings;
- gate evidence;
- release notes, SBOM and provenance references.

The packaged compatibility inventory exposed to operators includes the complete reviewed version
vector as well as the upstream entries. This metadata is informational/control-plane state. It must
not make an update executable without normal authorization and release approval.

## 11. Normal release checklist

1. Freeze the intended source commit and release version.
2. Generate release notes and the complete version vector.
3. Freeze or generate exact dependency lock/resolved sets and compute their digests.
4. Freeze exact upstream inputs/digests and refresh compatibility/provenance where changed.
5. Build artifacts and generate hashes, SBOM and provenance attestation references.
6. Run all mandatory gates and attach typed, commit-bound evidence where applicable.
7. Validate the release manifest with `platform-release validate`.
8. Exercise canary/staging if the change can affect runtime behavior or persisted state.
9. Obtain explicit release approval.
10. Publish immutable release artifacts/tags.
11. Confirm rollback/recovery instructions remain reachable and correct.
