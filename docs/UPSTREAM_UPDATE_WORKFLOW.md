# Upstream Update Workflow

Third-party updates are reviewed deliberately. The platform must not automatically absorb upstream changes into production-facing code.

## 1. Detect

Identify a new upstream release, tag or commit through the component's documented update method.

## 2. Verify provenance and license

Before changing source or dependency pins:

- confirm the canonical upstream location;
- verify the target version/tag/commit;
- re-check the current upstream license and notices;
- record any license change before implementation work continues.

A license change requires explicit review even when the technical diff is small.

## 3. Compare

Review changes between the currently pinned revision and the candidate revision. Classify changes affecting:

- public interfaces and protocols;
- configuration;
- state/lifecycle semantics;
- security and permissions;
- persistence or migration behavior;
- resource/deployment requirements;
- tests and compatibility;
- license or notice obligations.

For vendored/forked code, preserve a reproducible comparison path to the upstream revision.

## 4. Adapt behind platform contracts

Upstream changes must be translated through the existing platform adapter boundary. Do not modify canonical platform contracts merely to mirror an upstream implementation unless the platform abstraction itself is demonstrably incomplete and that architectural change is reviewed separately.

## 5. Test

Run the component's adapter/contract tests and the platform regression suite relevant to the change. For vendored/forked components, also run any retained upstream tests that are needed to establish behavior.

## 6. Record

Update `docs/UPSTREAMS.md` with the new pin, verification date, modification state and relevant notes. Preserve or update required notices.

## 7. Review and merge

Use a normal pull request. The PR must explain compatibility impact, migration requirements, rollback strategy when relevant, and why the update is worth adopting.

## Update cadence

Cadence is component-specific. Frequently changing or strategically important components may be checked on a recurring schedule; stable components can be reviewed less often. The registry entry must state the chosen method rather than imposing one global cadence.

## Emergency updates

Urgent fixes may use an accelerated review, but provenance, license verification, adapter boundaries and test evidence are never skipped.
