# Security Extension Checklist

Use this checklist for every issue or PR that adds or materially changes a security-sensitive subsystem, trust boundary, privileged action or external integration.

## Required review questions

### 1. Assets and actors

- What protected assets become reachable?
- Which human/service/Worker/provider actors can initiate or influence the flow?
- Can untrusted natural-language, model, file, webpage, tool or external-Event content affect the flow?

### 2. Trust boundaries and data flow

- Which trust boundary is new or changed?
- What is the canonical request/data flow across the boundary?
- Where are validation, authorization, Approval and audit enforcement points?
- Does any frontend, adapter, plugin or Worker become an accidental source of authority?

### 3. Privileged actions and side effects

- Does the subsystem read/write/delete files, execute code, use network access, mutate repositories, send messages, change external systems, install code or resolve secrets?
- What is the side-effect classification?
- Is the secure default deny, read-only or least privilege?
- If Approval is required, is it bound to the exact reviewed action/resource/invocation?

### 4. Identity and authorization

- Which canonical actor/service/Worker identity owns the operation?
- What Project/Workspace/resource scope applies?
- How is revocation enforced?
- Can backend IDs, metadata, prompt text or provider responses bypass canonical policy?
- Can a direct adapter/provider call bypass the enforcement point?

### 5. Input and output handling

- What inputs are untrusted?
- Which schema/structural/resource limits apply?
- Are output/Artifact/provider responses revalidated before privileged reuse?
- Are replay/idempotency semantics required?

### 6. Filesystem and execution

- What Workspace/root is authoritative?
- Are absolute path, traversal and symlink/junction escapes prevented?
- Is TOCTOU or hostile-code execution relevant enough to require OS/container sandboxing?
- Are environment variables and temporary files minimized/scoped?

### 7. Secrets and privacy

- Does the subsystem require secret material?
- Can it use the canonical `SecretReference` owned by #34 rather than plaintext canonical storage?
- What exact process/provider receives the secret and for how long?
- Could logs, errors, traces, Events, prompts, exports, backups or Artifacts leak it?
- What new redaction tests are required?

### 8. Network and external systems

- Which destinations are allowed?
- Are SSRF, redirects, metadata services, private ranges and DNS rebinding relevant?
- Are timeouts, size limits, rate/concurrency limits and session isolation defined?
- Are webhook/Event authenticity and replay controls defined?

### 9. Plugins, packages and supply chain

- Is executable third-party code introduced or updated?
- Is provenance/license/update treatment documented?
- Is the dependency/version pinned or otherwise reproducible?
- Can manifests or updates request/escalate permissions?
- What happens if the plugin/provider/upstream is compromised or disappears?

### 10. Distributed Workers

- How is Worker/service identity authenticated?
- How are dispatch and callback replay prevented?
- What trust level/capabilities does the Worker claim and who verifies them?
- Are secrets scoped to the job?
- How is a compromised/lost Worker quarantined or revoked?
- Are Worker results considered trusted, untrusted or conditionally trusted?

### 11. Audit and observability

- Which security Event should be emitted?
- Does it include canonical actor/action/resource/correlation context?
- Is the decision/reason recorded without plaintext secrets?
- Can an incident investigator reconstruct who authorized and executed the side effect?

### 12. Failure and abuse tests

Add regression tests for applicable cases:

- malformed/unbounded input;
- unauthorized request;
- forged identity/Approval/provider metadata;
- cross-Project/resource isolation;
- traversal/symlink/Workspace escape;
- secret leakage/redaction;
- replay/duplicate delivery;
- SSRF/destination rejection;
- Worker/service impersonation;
- plugin/package tampering or permission escalation;
- timeout/resource exhaustion;
- optional adapter absence preserving the canonical secure path.

## Required documentation updates

For a security-sensitive subsystem, the PR must:

1. update `docs/SECURITY_THREAT_MODEL.md` with the new/changed boundary, abuse cases, mitigations and residual risks;
2. update this checklist only when a reusable review requirement changes;
3. update `docs/SECURE_DEVELOPMENT.md` or `docs/SECURE_DEPLOYMENT.md` when contributor/operator behavior changes;
4. update `SECURITY.md` if vulnerability-reporting or incident-response policy changes;
5. add or extend regression tests in the shared security baseline or the subsystem's contract suite;
6. create/update an ADR if a canonical security ownership decision materially changes.

## Planned extension owners

| Issue | Required security extension |
| --- | --- |
| #12 | capability/tool invocation and provider-output threats |
| #14 | remote Node/Worker identity, dispatch and trust |
| #15 | authorization/Approval enforcement and anti-bypass |
| #20 | plugin isolation, permissions and supply chain |
| #34 | secret/config storage, resolution and SecretReference integration |
| #35 | transport authenticity, replay and message security |
| #36 | authentication, sessions, service identity and revocation |
| #37 | Workspace isolation/materialization and filesystem boundaries |
| #42 | update/upstream integrity, pinning and rollback |
| #44 | Connectors, webhook authenticity and external side effects |
| #74 | browser/network/SSRF/web-content isolation |
| #79 | import/export package trust and secret handling |
| #81 | registry/distribution/package provenance and permissions |
| #82 | repository/Git authority and irreversible side effects |
| #46 | end-to-end verification of accumulated security invariants |

A subsystem is not considered security-integrated merely because this table mentions it. Its implementation PR must complete the applicable checklist and tests.
