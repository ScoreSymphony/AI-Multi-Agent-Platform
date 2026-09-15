# Permanent naming and GitHub provenance

Permanent repository vocabulary should explain product behavior without requiring a reader to open a GitHub issue.

## Rule

Use behavior- and domain-oriented names for production modules, classes, functions, helpers, tests, fixtures and CI scripts. Do not use a GitHub issue number as the semantic name of a maintained artifact.

Prefer:

```text
test_recovery_completion_idempotency.py
materialize_runtime_assets.py
validate_manifest_capabilities()
```

Avoid:

```text
test_issue_439_final_hardening.py
issue725_materialize_runtime_assets.py
test_issue_20_manifest_contract()
```

The same rule applies to comments and docstrings. State the invariant or responsibility first. If the originating issue still adds useful context, keep it as secondary provenance:

```python
# Prevent duplicate completion events after recovery.
# Historical context: issue #439.
```

A comment whose meaning is only `Issue #439 final hardening` is not durable documentation.

## Where issue numbers remain appropriate

Issue and pull-request numbers are historical metadata and remain useful in:

- `docs/history/` completion and migration records;
- ADR historical context;
- changelog and release notes;
- compatibility/deprecation notes where the external reference is relevant;
- evidence directories whose identity intentionally records a specific acceptance campaign;
- branch names, commits and pull-request descriptions.

Repository integrations may also use the domain concept of an issue (`issue_id`, `repository.issue.read`, and similar names). The prohibited pattern is an implementation-history number embedded in permanent semantics, not the word `issue` itself.

## Migration policy

The repository contains historical production comments and scripts created before this policy. Migration is intentionally incremental rather than one unreadable repository-wide churn:

1. Newly introduced permanent paths and identifiers must be behavior-oriented.
2. When a Python source/test/script file is changed, issue-numbered identifiers in that file must be renamed in the same change.
3. When a changed Python file retains a useful issue reference in a comment or docstring, the reference must be secondary `Historical context:` or `Provenance:` text.
4. Historical/evidence locations keep their provenance-oriented names.
5. Focused cleanup batches should update callers, CI configuration and documentation together when a permanent script/module is renamed.

`scripts/ci/validate_permanent_naming.py` enforces these rules on the changed tree of pull requests. The diff-scoped policy lets existing historical debt be removed in focused batches while preventing new debt from entering the repository.

## Current classification

The initial #898 inventory identified four distinct categories:

| Category | Treatment |
| --- | --- |
| Historical completion/migration/release documentation | keep issue references as provenance |
| Repository-issue product concepts such as `issue_id` | keep; these are domain vocabulary |
| Maintained tests/helpers/CI scripts whose names contain a concrete issue number | rename to behavior/domain terminology |
| Production comments/docstrings led by `Issue #<n>` | rewrite behavior-first; keep only useful secondary provenance |

This document records the durable policy. Point-in-time cleanup details belong in the pull request and Git history rather than a new issue-by-issue status ledger.
