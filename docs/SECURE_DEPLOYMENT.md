# Secure Deployment Baseline

This checklist defines minimum deployment expectations. It is intentionally technology-neutral and applies to single-node and distributed installations.

## Identity and access

- Enable the platform's canonical authentication/authorization controls when available; do not expose administrative or privileged APIs anonymously.
- Use separate user, service and worker identities rather than shared credentials.
- Apply least privilege to filesystem, database, network and operating-system accounts.
- Ensure revocation removes practical access to sessions, workers and credentials.
- Do not rely on network location, hostname, worker ID or plugin ID as authorization.

## Network exposure

- Bind services only to interfaces that must be reachable.
- Place public endpoints behind appropriate TLS termination and network controls.
- Keep databases, internal model endpoints, worker management interfaces and metadata/admin endpoints private unless explicitly required.
- Apply outbound network restrictions to executors/browser/connector processes where practical.
- Block unintended access to loopback, link-local, internal management networks and cloud metadata endpoints from untrusted execution contexts.
- Set request, response, connection, concurrency and timeout limits.

## Filesystem and execution

- Run platform processes as non-root/non-administrator identities where feasible.
- Give executors access only to required workspace roots and explicitly approved resources.
- Keep source/config/secrets outside writable untrusted workspaces.
- Use container/OS sandboxing for workloads that can execute hostile or arbitrary code.
- Mount sensitive host paths read-only or not at all.
- Use dedicated temporary directories with restrictive permissions and cleanup policies.
- Treat artifacts/downloads as untrusted until validated by their consumer.

## Secrets

- Do not commit production credentials to the repository or deployment manifests.
- Use scoped secret references/storage when #34 is available; until then, minimize environment-based secret distribution.
- Avoid injecting unrelated host/service credentials into Agent, model, tool or executor processes.
- Redact logs, traces, diagnostics and exports before they leave the trust boundary.
- Rotate credentials after suspected exposure and after worker/node compromise.

## Persistence, logs and backups

- Restrict canonical databases/event stores to the platform identities that need them.
- Protect integrity and confidentiality of backups; define whether backups may contain secrets before enabling them.
- Test restore procedures without overwriting the only known-good copy.
- Keep audit/security events long enough to investigate incidents while minimizing sensitive data.
- Do not enable verbose diagnostics that serialize raw prompts, provider payloads or credentials by default.

## Workers and distributed deployments

When remote workers are enabled:

- authenticate registration and dispatch;
- use canonical service/worker identity;
- scope delivered capabilities and secrets to the job;
- reject replayed/duplicate jobs according to canonical idempotency rules;
- assign explicit worker trust levels/capabilities;
- provide a quarantine/revocation path for lost or compromised workers;
- validate results before using them in later privileged operations.

Single-node mode must follow the same authority model even when transport authentication collapses to a local boundary.

## Models, tools, plugins and connectors

- Treat external providers as separate trust domains.
- Send only data required for the configured operation.
- Do not install plugins/adapters with broader permissions than needed.
- Isolate provider sessions/tokens by intended scope.
- Keep connector/browser external side effects behind canonical policy/approval.
- Treat retrieved webpage/tool/repository content as untrusted input.

## Supply chain and updates

- Install dependencies and platform releases from intended sources.
- Prefer pinned/reproducible versions and integrity verification where available.
- Follow `LICENSE_POLICY.md`, `docs/UPSTREAMS.md` and `docs/UPSTREAM_UPDATE_WORKFLOW.md` for architecture-significant upstreams.
- Review permission/config/schema changes before upgrading.
- Keep a rollback path for platform and data migrations.
- Do not auto-install unreviewed plugins, model tooling or upstream packages into privileged environments.

## Operational readiness

Before exposing the deployment to untrusted users or content:

- run the repository CI/security regression suite;
- verify authentication and authorization settings;
- verify workspace confinement with a non-privileged test workload;
- verify redaction on representative logs/errors;
- verify outbound network restrictions;
- verify resource limits and timeouts;
- verify backup/restore and credential revocation procedures;
- document who can perform administrative actions;
- document the incident containment path.

## High-risk deployments

Deployments processing sensitive data, accepting arbitrary public content, executing arbitrary code or connecting to high-impact external systems should obtain specialized security review/penetration testing. This baseline is necessary but not a guarantee of complete security.
