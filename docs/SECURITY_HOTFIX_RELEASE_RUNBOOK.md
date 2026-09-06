# Security hotfix release runbook

Use this path only for a concrete vulnerability or urgent security regression.

## Invariants

Urgency may compress waiting/review time, but it may not skip:

- exact source/upstream pins;
- license and provenance review for changed inputs;
- relevant adapter/contract tests;
- security checks for the fix;
- migration compatibility/preflight when persisted state is affected;
- rollback/recovery classification;
- release manifest, hashes and SBOM/provenance references;
- explicit approval before production rollout.

## Procedure

1. Identify affected platform/upstream versions and severity; record the advisory privately where
   disclosure must be delayed.
2. Create a dedicated hotfix branch from the supported release line.
3. Apply the smallest safe fix behind existing platform-owned boundaries.
4. Pin every changed external input immutably; never substitute `latest`.
5. Run targeted security regression tests immediately, then the mandatory release gates that can be
   affected by the change.
6. If a migration is required, run issue #41 preflight and establish the permitted rollback mode
   before activation.
7. Generate release artifacts, hashes, SBOM/provenance references and release notes. Public notes may
   defer exploit detail but must not misstate compatibility or migration requirements.
8. Populate concrete evidence in the release manifest and run `platform-release validate`.
9. Obtain explicit release approval and perform canary/staging when technically meaningful; if a
   canary is unsafe or would disclose the vulnerability, document why and use the narrowest safe
   production rollout.
10. Publish the immutable hotfix release and monitor health/security signals.
11. If regression criteria are met, execute the pre-declared rollback/restore path rather than
    improvising a downgrade.
12. After stabilization, complete any deferred public advisory, provenance notes and normal upstream
    review documentation.

A failed required release gate is never converted to `passed` merely because the release is urgent.
