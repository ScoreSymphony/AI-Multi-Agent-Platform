# Security Policy

## Scope

This repository implements a general-purpose AI multi-agent platform that may combine untrusted natural-language/model output with filesystem access, tool invocation, external services, credentials and distributed execution.

Security is therefore an architectural requirement, not a release-only hardening step. The normative platform threat model is [`docs/SECURITY_THREAT_MODEL.md`](docs/SECURITY_THREAT_MODEL.md).

## Reporting a vulnerability

Do **not** publish exploit details, credentials, private data, working proof-of-concept payloads or unpatched bypasses in a public issue.

Preferred reporting path:

1. Use GitHub's enabled private vulnerability-reporting / Security Advisory flow for this repository through the **Report a vulnerability** action.
2. If private reporting is unavailable, create a minimal public issue asking the repository owner for a private security contact. Include only the affected area and that you have a security report; do not include exploit details.
3. After a private channel is established, provide the affected revision/version, impact, prerequisites, reproduction steps, suggested mitigation if known and whether the issue appears to be actively exploited.

Reports made in good faith to improve the platform are welcome. Please avoid accessing data you do not own, causing availability impact, persisting on systems or expanding testing beyond what is necessary to demonstrate the issue.

## Response ownership and targets

@ScoreSymphony owns repository security intake, advisory coordination and security release approval until that responsibility is explicitly delegated in repository governance.

The project targets:

- acknowledgement of a credible private report within three business days;
- initial severity, affected-scope and containment triage within seven calendar days;
- regular private status updates while a confirmed vulnerability remains unresolved; and
- coordinated public disclosure only after users have a practical remediation or containment path.

These targets are best-effort rather than a contractual service-level agreement. Severity, active exploitation, credential exposure and the availability of a safe mitigation determine remediation priority. Reporters should not publish exploit details before coordinated disclosure without first giving maintainers a reasonable opportunity to protect users.

## Security response basics

For a credible report, maintainers should:

1. acknowledge and privately triage the report;
2. identify affected versions, deployments, trust boundaries and credentials;
3. contain the issue, including disabling a vulnerable capability/adapter or revoking credentials when necessary;
4. preserve relevant audit evidence without copying plaintext secrets into incident notes;
5. prepare and test a fix plus regression coverage;
6. rotate/revoke exposed secrets, sessions, worker identities or signing material as required;
7. publish an appropriate advisory/release note after users have a practical remediation path;
8. update the threat model, secure-development guidance and regression suite when the incident reveals a missing invariant or trust assumption.

The security owner records affected versions, severity, remediation revision, credit preferences and disclosure decisions in the private advisory. Security fixes use the normal verification requirements, with private-fork or accelerated release handling when public development would expose users before remediation.

## Security architecture

The platform follows these baseline rules:

- model/agent output is untrusted input to privileged systems;
- canonical identity, policy and approval decisions own authority;
- sensitive operations must cross backend enforcement points and cannot rely on frontend-only checks;
- workspace/file boundaries are canonical and cannot be bypassed through provider-private paths;
- plaintext secrets must not appear in normal canonical serialization, logs, traces, events or exports;
- backend/plugin/worker identifiers do not grant permissions;
- external/retrieved content cannot elevate permissions;
- installed plugins/adapters are not automatically trusted;
- single-node and distributed modes use the same security ownership model;
- security-critical actions remain auditable with canonical actor/action/resource context.

See [`docs/SECURITY_THREAT_MODEL.md`](docs/SECURITY_THREAT_MODEL.md) for assets, actors, trust boundaries, abuse cases, mitigations, residual risks and re-evaluation triggers.

## Contributor requirements

Security-sensitive work must follow:

- [`docs/SECURE_DEVELOPMENT.md`](docs/SECURE_DEVELOPMENT.md)
- [`docs/SECURE_DEPLOYMENT.md`](docs/SECURE_DEPLOYMENT.md)
- [`docs/SECURITY_EXTENSION_CHECKLIST.md`](docs/SECURITY_EXTENSION_CHECKLIST.md)
- [`LICENSE_POLICY.md`](LICENSE_POLICY.md) and upstream provenance requirements for supply-chain changes

A downstream implementation that requires weakening a documented security invariant must stop and trigger an explicit architecture/security review rather than silently changing the invariant.

## Supported security posture

The project is under active development. Passing the baseline security regression suite does not mean the platform is secure against every deployment-specific threat, and it does not replace specialized external review or penetration testing for high-risk deployments.

At least quarterly, the maintainer reviews private-reporting availability, unresolved alerts, collaborator permissions, branch protection, dependency/security automation and threat-model assumptions. The same review is required before a release when security boundaries or privileged capabilities materially changed.
