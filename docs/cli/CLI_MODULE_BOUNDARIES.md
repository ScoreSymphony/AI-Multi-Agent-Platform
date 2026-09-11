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

Production code and package entrypoints must not add `issue_<number>` modules or imports. The architecture tests enforce this rule and no issue-numbered production compatibility module is retained.

Historical issue identifiers may remain in regression-test filenames, changelogs, ADRs, or other provenance where they describe history rather than define runtime architecture. New production callers must use the stable responsibility-based modules above.
