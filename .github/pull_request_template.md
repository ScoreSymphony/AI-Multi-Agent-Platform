## Summary

Describe the focused change and why it is needed.

## Related issue

Closes or progresses #

## Architecture impact

- [ ] No canonical architecture change
- [ ] Architecture/contracts changed and documentation was updated
- [ ] New third-party dependency/integration was reviewed for provenance, license and replaceability
- [ ] Architecture-significant upstream change has explicit review and an ADR when required

## Upstream / license impact

Complete this section when third-party software is added or materially changed.

- [ ] integration category/categories from `LICENSE_POLICY.md` recorded
- [ ] new architecture-significant upstream completed `docs/UPSTREAM_ADOPTION_CHECKLIST.md`
- [ ] `docs/UPSTREAMS.md` updated when approval/integration/status/provenance changed
- [ ] canonical upstream and exact version/tag/commit/deployed revision recorded
- [ ] current license verified and required copyright/license/NOTICE material retained
- [ ] material transitive/bundled license concerns reviewed
- [ ] copied/vendored/forked/ported source has traceable origin and modification status
- [ ] vendoring/forking/selective-port rationale documented when a looser boundary would work
- [ ] platform adapter/boundary and compatibility constraints recorded
- [ ] update/review method and exit/replacement strategy recorded
- [ ] provenance metadata updated when the integration changed
- [ ] baseline operation still avoids required recurring paid AI/API services

## Upstream update evidence

For architecture-significant updates, summarize old/new revisions, relevant `security` / `bug fix` / `feature` / `breaking` / `irrelevant` classifications, local modification/conflict assessment, migration impact, and rollback plan.

## Validation

- [ ] `ruff check .`
- [ ] `mypy`
- [ ] `pytest`
- [ ] `python -m build`
- [ ] relevant adapter/contract/integration/regression tests completed

## Notes

Document migrations, compatibility concerns, follow-up work or known limitations.
