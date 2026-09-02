# Upstream Update Workflow

Third-party updates are reviewed deliberately. The platform must not automatically absorb upstream changes into production-facing code.

This workflow applies to architecture-significant upstream integrations. Standard development/build tooling may use a lighter dependency-maintenance path when it does not affect runtime architecture, while still respecting package licensing and validation requirements.

## 1. Identify the current revision

Before evaluating an update, identify and record the currently used version, tag, commit, image digest, protocol version, or other reproducible revision from `docs/UPSTREAMS.md` and the component provenance record.

If the current production revision is not reproducible, fix that provenance gap before treating an update as routine.

## 2. Detect a candidate update

Identify a new upstream release, tag, commit, specification revision, or image through the component's documented update/review method.

No candidate update is accepted merely because it is newer.

## 3. Verify provenance and license

Before changing source or dependency pins:

- confirm the canonical upstream location;
- verify the target version/tag/commit/revision;
- re-check the current upstream license and required notices;
- review material bundled/transitive license changes;
- record any license change before implementation continues.

A license change always requires explicit review even when the technical diff is small. If compatibility becomes unclear, block the update until resolved.

## 4. Review and classify upstream changes

Compare the candidate revision with the currently pinned revision. Classify each relevant change as one or more of:

- **security**;
- **bug fix**;
- **feature**;
- **breaking**;
- **irrelevant to the platform integration**.

Also inspect changes affecting:

- public APIs, protocols, and configuration;
- lifecycle/state semantics;
- authentication, authorization, permissions, and trust boundaries;
- persistence, schemas, or migrations;
- deployment topology and operational dependencies;
- CPU, memory, GPU, storage, or network requirements;
- bundled/transitive dependencies;
- tests and supported versions;
- license/NOTICE/attribution obligations.

## 5. Assess local modifications and conflicts

For vendored, forked, or selectively ported code:

- compare local source with the recorded upstream revision;
- identify local modifications that overlap upstream changes;
- assess merge/cherry-pick/porting conflicts;
- preserve project modifications deliberately rather than accepting an upstream overwrite;
- update modification markers and provenance as needed.

Maintain a reproducible comparison path to the original upstream revision.

## 6. Assess architecture and replacement impact

Determine whether the candidate update changes:

- canonical platform contract assumptions;
- adapter boundaries;
- persistence ownership;
- security boundaries;
- distributed-node behavior;
- required infrastructure;
- replaceability or the documented exit path.

If the platform abstraction itself must change, review that architecture change separately. Breaking architecture implications require an ADR before the update is treated as stable.

## 7. Update in a dedicated branch and pull request

Perform architecture-significant upstream updates in a focused branch/PR. Do not mix an upstream update with unrelated feature work.

The PR must state:

- old and new revisions;
- relevant change classifications;
- license/provenance result;
- local modification/conflict assessment;
- compatibility and migration impact;
- rollback strategy where relevant;
- architecture/ADR impact;
- why the update is worth adopting.

## 8. Adapt only behind platform contracts

Translate upstream changes through existing platform adapters/boundaries. Do not modify canonical platform contracts merely to mirror an upstream implementation unless the platform abstraction is demonstrably incomplete and the architectural change is explicitly approved.

## 9. Test

Run validation appropriate to the integration, including as applicable:

- unit tests;
- adapter/contract tests;
- integration tests;
- regression tests;
- migration tests;
- security-relevant tests;
- retained upstream tests for vendored/forked/ported source.

The update PR should make test evidence visible.

## 10. Update provenance and notices

Before merge:

- update `docs/UPSTREAMS.md`;
- update the component's provenance metadata;
- record the new revision and review date;
- update compatibility constraints and modification summary;
- preserve/update required copyright, license, and NOTICE material;
- update the exit/replacement strategy when the update changes it.

## 11. Review and merge

Merge only after technical, architecture, license/provenance, and test concerns appropriate to the change are resolved.

Emergency security fixes may use an accelerated review, but provenance, license verification, adapter boundaries, and test evidence are never skipped.

# Periodic upstream review

Every architecture-significant upstream must declare its review/update method in `docs/UPSTREAMS.md`.

## Integrations requiring active monitoring

Active periodic checks are expected for:

- maintained forks;
- vendored source with relevant upstream development;
- selective code ports that can miss upstream security/bug fixes;
- external services or dependencies that are critical to orchestration, execution, persistence, security, model access, or protocol compatibility;
- components with fast-moving or unstable APIs;
- components whose upstream security advisories can materially affect the platform.

Stable protocols/reference-only influences can use a less frequent or event-driven review when justified.

## Manual review

A manual review should:

1. identify the recorded current revision;
2. check upstream releases/commits/advisories since the last review;
3. classify relevant changes;
4. record whether an update is needed;
5. update `last_review_date` even when no update is adopted;
6. open a focused issue/PR when action is required.

## Future automation

Automation may discover candidate releases, compare revisions, collect release/security metadata, or open review issues/PRs. Automation must **not** automatically approve or deploy architecture-significant upstream changes.

Automated checks should produce the same inputs required by the manual process: current revision, candidate revision, change summary/classification, license/provenance signals, and a human-reviewable decision point.
