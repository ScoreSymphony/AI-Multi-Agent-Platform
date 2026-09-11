# CLI module boundaries

The packaged `platform` command uses responsibility-based modules rather than GitHub issue history.

Canonical composition:

```text
platform
  -> ai_multi_agent_platform.cli.app
     -> ai_multi_agent_platform.cli.repositories
        -> ai_multi_agent_platform.cli.auth
           -> ai_multi_agent_platform.cli.main
```

The stable responsibilities are:

- `cli.app`: top-level composition plus Registry and governed Learning routing.
- `cli.repositories`: provider-neutral repository and collaboration commands.
- `cli.auth`: authentication, credential/session handling, and Approval decisions.
- `cli.main`: established core CLI commands that predate the responsibility-specific layers.

Production code and package entrypoints must not add new `issue_<number>` modules or imports. The architecture tests enforce this rule.

## Compatibility exception

`cli.issue_214` is a deprecated, non-canonical import shim that re-exports `cli.auth.main` and `cli.auth.run_cli`. It exists only for the previous direct Python import path; no package entrypoint or canonical production module may depend on it.

Removal policy: migrate callers to `ai_multi_agent_platform.cli.auth` now. Remove the shim at the next breaking package release after downstream import migration. No additional issue-numbered compatibility modules may be introduced without a separately documented compatibility contract and an explicit removal policy.
