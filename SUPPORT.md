# Support

## Supported channels

Use the repository's structured Work item issue form for reproducible defects, documentation gaps, installation problems and actionable enhancement proposals.

Before opening an issue:

1. search open and closed issues for an existing report or replacement;
2. use the latest documented installation and configuration instructions;
3. remove credentials, tokens, private data and proprietary content from all examples; and
4. include the affected revision or release, environment, reproduction steps, expected behavior and actual behavior.

Security vulnerabilities must not be reported through a public issue. Use GitHub private vulnerability reporting as described in [SECURITY.md](SECURITY.md).

## Support scope

The project is under active development. Until version 0.1.0 is published, main is the only actively maintained development line and support is best-effort. After releases begin, the latest published minor line receives fixes unless a release note explicitly states a different support window.

Maintainers may help with:

- supported installation and upgrade paths;
- reproducible behavior in platform-owned code;
- canonical API and configuration questions;
- security-safe diagnostics; and
- documentation corrections.

The project does not guarantee support for bespoke infrastructure, unreviewed forks, private downstream modifications, unsupported third-party services or provider-specific behavior outside documented adapter contracts.

## Response expectations

Public support has no guaranteed service-level agreement. The repository administration routine targets an initial triage within seven calendar days. Complex investigations, upstream dependencies and volunteer availability may require longer.

Maintainers may close reports that cannot be reproduced, omit required information, duplicate existing work, disclose sensitive information after a safe redaction request, or fall outside the supported platform boundary. A closed report may be reopened when new reproducible evidence becomes available.

## Safe diagnostics

Never publish access tokens, API keys, passwords, private keys, session cookies, unredacted environment files, production database contents or sensitive logs. Replace secrets with explicit placeholders and reduce examples to the minimum information required to reproduce the problem.
